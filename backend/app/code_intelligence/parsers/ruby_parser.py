"""
Ruby symbol parser — extracts symbols and relationships using tree-sitter.

Extracts:
- Class declarations
- Module declarations
- Method definitions
- Require/load calls (imports)
- Constants
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.code_intelligence.semantic_graph import (
    ConfidenceLevel,
    GraphNode,
    RelationshipType,
    make_symbol_id,
    normalize_qualified_name,
)

_parser: Optional[Any] = None
_TREE_SITTER_AVAILABLE: bool = True


def _get_parser() -> Optional[Any]:
    """Lazy-initialized tree-sitter parser (singleton).

    Gracefully degrades if tree-sitter packages are not installed.
    Returns None if unavailable — caller handles the None case.
    """
    global _parser, _TREE_SITTER_AVAILABLE
    if _parser is not None:
        return _parser
    if not _TREE_SITTER_AVAILABLE:
        return None

    try:
        from tree_sitter import Language, Parser  # type: ignore[import-untyped]
        import tree_sitter_ruby as tsrb  # type: ignore[import-untyped]

        lang = Language(tsrb.language())
        _parser = Parser(lang)
        return _parser
    except Exception:
        _TREE_SITTER_AVAILABLE = False
        return None


class RubySymbolParser:
    """Parse Ruby source and extract structured symbols with relationships."""

    def __init__(self, file_path: str, content: str) -> None:
        self.file_path = file_path
        self.content = content
        self.bytes_content = content.encode("utf-8") if isinstance(content, str) else content
        self.lines = content.split("\n")
        self.language = "Ruby"

        self.symbols: List[GraphNode] = []
        self.relationships: List[dict] = []
        self.diagnostics: List[str] = []
        self._current_module: List[str] = []

    def parse(self) -> Tuple[List[GraphNode], List[dict], List[str]]:
        if not self.content.strip():
            return [], [], []

        parser = _get_parser()
        if parser is None:
            self.diagnostics.append(
                "Ruby parser unavailable: tree-sitter-ruby package not installed. "
                "Install with: pip install tree-sitter tree-sitter-ruby"
            )
            return [], [], self.diagnostics

        try:
            tree = parser.parse(self.bytes_content)
        except Exception as exc:
            self.diagnostics.append(f"Parse error: {exc}")
            return [], [], self.diagnostics

        root = tree.root_node
        if root.has_error:
            self.diagnostics.append("Parse tree contains errors (partial results may follow)")

        file_id = self._create_file_node()

        for child in root.children:
            if not child.is_named:
                continue
            if child.type == "class":
                self._extract_class(child, file_id)
            elif child.type == "module":
                self._extract_module(child, file_id)
            elif child.type == "method":
                self._extract_method(child, file_id)
            elif child.type == "call" and self._is_require(child):
                self._extract_require(child, file_id)
            elif child.type == "constant_assignment":
                self._extract_constant(child, file_id)

        return self.symbols, self.relationships, self.diagnostics

    def _create_file_node(self) -> str:
        module_name = self.file_path.replace("/", ".").rsplit(".", 1)[0].replace("\\", ".")
        file_id = make_symbol_id(self.file_path, module_name)
        self.symbols.append(GraphNode(
            id=file_id, name=self.file_path.split("/")[-1], qualified_name=module_name,
            kind="file", file_path=self.file_path, language=self.language,
            start_line=1, end_line=len(self.lines),
        ))
        return file_id

    def _is_require(self, node: Node) -> bool:
        text = self._node_text(node)
        return text.startswith("require ") or text.startswith("require_relative ") or text.startswith("load ")

    def _extract_class(self, node: Node, parent_id: str) -> None:
        for child in node.children:
            if child.type == "constant":
                name = self._node_text(child)
                parent_names = list(self._current_module)
                symbol_id, qname = self._make_symbol(name, parent_names or None)
                start_line, end_line = self._node_lines(node)

                self._current_module.append(name)
                self.symbols.append(GraphNode(
                    id=symbol_id, name=name, qualified_name=qname,
                    kind="class", file_path=self.file_path, language=self.language,
                    start_line=start_line, end_line=end_line, parent_id=parent_id,
                ))
                self.relationships.append({
                    "source_id": parent_id, "target_id": symbol_id,
                    "relationship": RelationshipType.CONTAINS.value,
                    "confidence": ConfidenceLevel.EXACT.value, "source_lines": [start_line],
                })

                # Extract inner methods
                body = self._find_child(node, "body_statement")
                if body:
                    self._extract_body(body, symbol_id, name)
                self._current_module.pop()
                return

    def _extract_module(self, node: Node, parent_id: str) -> None:
        for child in node.children:
            if child.type == "constant":
                name = self._node_text(child)
                parent_names = list(self._current_module)
                symbol_id, qname = self._make_symbol(name, parent_names or None)
                start_line, end_line = self._node_lines(node)

                self._current_module.append(name)
                self.symbols.append(GraphNode(
                    id=symbol_id, name=name, qualified_name=qname,
                    kind="module", file_path=self.file_path, language=self.language,
                    start_line=start_line, end_line=end_line, parent_id=parent_id,
                ))
                self.relationships.append({
                    "source_id": parent_id, "target_id": symbol_id,
                    "relationship": RelationshipType.CONTAINS.value,
                    "confidence": ConfidenceLevel.EXACT.value, "source_lines": [start_line],
                })

                body = self._find_child(node, "body_statement")
                if body:
                    self._extract_body(body, symbol_id, name)
                self._current_module.pop()
                return

    def _extract_body(self, node: Node, parent_id: str, parent_name: str) -> None:
        for child in node.children:
            if not child.is_named:
                continue
            if child.type == "method":
                self._extract_method(child, parent_id)
            elif child.type == "class":
                self._extract_class(child, parent_id)
            elif child.type == "module":
                self._extract_module(child, parent_id)
            elif child.type == "call" and self._is_require(child):
                self._extract_require(child, parent_id)

    def _extract_method(self, node: Node, parent_id: str) -> None:
        for child in node.children:
            if child.type == "identifier":
                name = self._node_text(child)
                symbol_id, qname = self._make_symbol(name)
                start_line, end_line = self._node_lines(node)

                self.symbols.append(GraphNode(
                    id=symbol_id, name=name, qualified_name=qname,
                    kind="method", file_path=self.file_path, language=self.language,
                    start_line=start_line, end_line=end_line, parent_id=parent_id,
                ))
                self.relationships.append({
                    "source_id": parent_id, "target_id": symbol_id,
                    "relationship": RelationshipType.CONTAINS.value,
                    "confidence": ConfidenceLevel.EXACT.value, "source_lines": [start_line],
                })
                return

    def _extract_require(self, node: Node, parent_id: str) -> None:
        for child in node.children:
            if child.type in ("string", "simple_symbol"):
                path = self._node_text(child).strip("'\"")
                name = path.split("/")[-1]
                symbol_id = make_symbol_id(self.file_path, f"import:{path}")
                sl, _ = self._node_lines(node)
                self.symbols.append(GraphNode(
                    id=symbol_id, name=name, qualified_name=f"import:{path}",
                    kind="import", file_path=self.file_path, language=self.language,
                    start_line=sl, end_line=sl, metadata={"require_path": path},
                ))
                self.relationships.append({
                    "source_id": parent_id, "target_id": symbol_id,
                    "relationship": RelationshipType.IMPORTS.value,
                    "confidence": ConfidenceLevel.EXACT.value, "source_lines": [sl],
                })
                return

    def _extract_constant(self, node: Node, parent_id: str) -> None:
        for child in node.children:
            if child.type == "constant":
                name = self._node_text(child)
                if name == name.upper():
                    symbol_id, qname = self._make_symbol(name)
                    sl, _ = self._node_lines(node)
                    self.symbols.append(GraphNode(
                        id=symbol_id, name=name, qualified_name=qname,
                        kind="constant", file_path=self.file_path, language=self.language,
                        start_line=sl, end_line=sl, parent_id=parent_id,
                    ))

    def _make_symbol(self, name: str, parent_names: Optional[List[str]] = None) -> Tuple[str, str]:
        qname = normalize_qualified_name(self.file_path, name, parent_names)
        return make_symbol_id(self.file_path, qname), qname

    def _node_text(self, node: Node) -> str:
        return self.bytes_content[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

    def _node_lines(self, node: Node) -> Tuple[int, int]:
        return node.start_point[0] + 1, node.end_point[0] + 1

    def _find_child(self, node: Node, child_type: str) -> Optional[Node]:
        for child in node.children:
            if child.type == child_type:
                return child
        return None

    def supports_language(self, language: str) -> bool:
        return language.lower() in {"ruby", "rb"}
