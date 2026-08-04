"""
Kotlin symbol parser — extracts symbols and relationships using tree-sitter.

Extracts:
- Package declarations
- Imports
- Classes (with constructors, methods, properties)
- Functions
- Interfaces
- Objects
- Data classes
- Companion objects
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
        import tree_sitter_kotlin as tskt  # type: ignore[import-untyped]

        lang = Language(tskt.language())
        _parser = Parser(lang)
        return _parser
    except Exception:
        _TREE_SITTER_AVAILABLE = False
        return None


class KotlinSymbolParser:
    """Parse Kotlin source and extract structured symbols with relationships."""

    def __init__(self, file_path: str, content: str) -> None:
        self.file_path = file_path
        self.content = content
        self.bytes_content = content.encode("utf-8") if isinstance(content, str) else content
        self.lines = content.split("\n")
        self.language = "Kotlin"

        self.symbols: List[GraphNode] = []
        self.relationships: List[dict] = []
        self.diagnostics: List[str] = []

    def parse(self) -> Tuple[List[GraphNode], List[dict], List[str]]:
        if not self.content.strip():
            return [], [], []

        parser = _get_parser()
        if parser is None:
            self.diagnostics.append(
                "Kotlin parser unavailable: tree-sitter-kotlin package not installed. "
                "Install with: pip install tree-sitter tree-sitter-kotlin"
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
            if child.type == "package_header":
                self._extract_package(child, file_id)
            elif child.type == "import":
                self._extract_import(child, file_id)
            elif child.type == "class_declaration":
                self._extract_class(child, file_id)
            elif child.type == "function_declaration":
                self._extract_function(child, file_id)
            elif child.type == "object_declaration":
                self._extract_object(child, file_id)
            elif child.type == "interface_declaration":
                self._extract_interface(child, file_id)

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

    def _extract_package(self, node: Node, parent_id: str) -> None:
        qid = self._find_child(node, "qualified_identifier")
        if not qid:
            return
        pkg = self._node_text(qid)
        symbol_id = make_symbol_id(self.file_path, f"package:{pkg}")
        sl, _ = self._node_lines(node)
        self.symbols.append(GraphNode(
            id=symbol_id, name=pkg.split(".")[-1], qualified_name=f"package:{pkg}",
            kind="package", file_path=self.file_path, language=self.language,
            start_line=sl, end_line=sl, metadata={"package": pkg},
        ))

    def _extract_import(self, node: Node, parent_id: str) -> None:
        qid = self._find_child(node, "qualified_identifier")
        if not qid:
            return
        imp_path = self._node_text(qid)
        name = imp_path.split(".").pop()
        symbol_id = make_symbol_id(self.file_path, f"import:{imp_path}")
        sl, _ = self._node_lines(node)

        self.symbols.append(GraphNode(
            id=symbol_id, name=name, qualified_name=f"import:{imp_path}",
            kind="import", file_path=self.file_path, language=self.language,
            start_line=sl, end_line=sl, metadata={"import_path": imp_path},
        ))
        self.relationships.append({
            "source_id": parent_id, "target_id": symbol_id,
            "relationship": RelationshipType.IMPORTS.value,
            "confidence": ConfidenceLevel.EXACT.value, "source_lines": [sl],
        })

    def _extract_class(self, node: Node, parent_id: str) -> None:
        name_node = self._find_child(node, "identifier")
        if not name_node:
            return
        name = self._node_text(name_node)
        symbol_id, qname = self._make_symbol(name)
        start_line, end_line = self._node_lines(node)

        is_data = "data" in self._node_text(node.children[0]) if node.children else False
        is_sealed = "sealed" in self._node_text(node) if "sealed" in self._node_text(node) else False

        self.symbols.append(GraphNode(
            id=symbol_id, name=name, qualified_name=qname,
            kind="data_class" if is_data else "class",
            file_path=self.file_path, language=self.language,
            start_line=start_line, end_line=end_line, parent_id=parent_id,
            metadata={"is_data": is_data, "is_sealed": is_sealed},
        ))
        self.relationships.append({
            "source_id": parent_id, "target_id": symbol_id,
            "relationship": RelationshipType.CONTAINS.value,
            "confidence": ConfidenceLevel.EXACT.value, "source_lines": [start_line],
        })

        class_body = self._find_child(node, "class_body")
        if class_body:
            for child in class_body.children:
                if child.type == "function_declaration":
                    self._extract_method(child, symbol_id, name)

    def _extract_function(self, node: Node, parent_id: str) -> None:
        name_node = self._find_child(node, "identifier")
        if not name_node:
            return
        name = self._node_text(name_node)
        symbol_id, qname = self._make_symbol(name)
        start_line, end_line = self._node_lines(node)

        self.symbols.append(GraphNode(
            id=symbol_id, name=name, qualified_name=qname,
            kind="function", file_path=self.file_path, language=self.language,
            start_line=start_line, end_line=end_line, parent_id=parent_id,
        ))
        self.relationships.append({
            "source_id": parent_id, "target_id": symbol_id,
            "relationship": RelationshipType.CONTAINS.value,
            "confidence": ConfidenceLevel.EXACT.value, "source_lines": [start_line],
        })

    def _extract_method(self, node: Node, class_id: str, class_name: str) -> None:
        name_node = self._find_child(node, "identifier")
        if not name_node:
            return
        name = self._node_text(name_node)
        symbol_id, qname = self._make_symbol(name, [class_name])
        start_line, end_line = self._node_lines(node)

        self.symbols.append(GraphNode(
            id=symbol_id, name=name, qualified_name=qname,
            kind="method", file_path=self.file_path, language=self.language,
            start_line=start_line, end_line=end_line, parent_id=class_id,
        ))
        self.relationships.append({
            "source_id": class_id, "target_id": symbol_id,
            "relationship": RelationshipType.CONTAINS.value,
            "confidence": ConfidenceLevel.EXACT.value, "source_lines": [start_line],
        })

    def _extract_object(self, node: Node, parent_id: str) -> None:
        name_node = self._find_child(node, "identifier")
        if not name_node:
            return
        name = self._node_text(name_node)
        symbol_id, qname = self._make_symbol(name)
        start_line, end_line = self._node_lines(node)

        is_companion = "companion" in self._node_text(node)
        self.symbols.append(GraphNode(
            id=symbol_id, name=name, qualified_name=qname,
            kind="companion_object" if is_companion else "object",
            file_path=self.file_path, language=self.language,
            start_line=start_line, end_line=end_line, parent_id=parent_id,
            metadata={"is_companion": is_companion},
        ))
        self.relationships.append({
            "source_id": parent_id, "target_id": symbol_id,
            "relationship": RelationshipType.CONTAINS.value,
            "confidence": ConfidenceLevel.EXACT.value, "source_lines": [start_line],
        })

    def _extract_interface(self, node: Node, parent_id: str) -> None:
        name_node = self._find_child(node, "identifier")
        if not name_node:
            return
        name = self._node_text(name_node)
        symbol_id, qname = self._make_symbol(name)
        start_line, end_line = self._node_lines(node)

        self.symbols.append(GraphNode(
            id=symbol_id, name=name, qualified_name=qname,
            kind="interface", file_path=self.file_path, language=self.language,
            start_line=start_line, end_line=end_line, parent_id=parent_id,
        ))
        self.relationships.append({
            "source_id": parent_id, "target_id": symbol_id,
            "relationship": RelationshipType.CONTAINS.value,
            "confidence": ConfidenceLevel.EXACT.value, "source_lines": [start_line],
        })

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
        return language.lower() in {"kotlin", "kt", "kts"}
