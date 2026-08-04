"""
Swift symbol parser — extracts symbols and relationships using tree-sitter.

Extracts:
- Import declarations
- Classes / Structs (with properties, methods)
- Functions
- Protocols (interfaces)
- Enums
- Initializers
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
        import tree_sitter_swift as tssw  # type: ignore[import-untyped]

        lang = Language(tssw.language())
        _parser = Parser(lang)
        return _parser
    except Exception:
        _TREE_SITTER_AVAILABLE = False
        return None


class SwiftSymbolParser:
    """Parse Swift source and extract structured symbols with relationships."""

    def __init__(self, file_path: str, content: str) -> None:
        self.file_path = file_path
        self.content = content
        self.bytes_content = content.encode("utf-8") if isinstance(content, str) else content
        self.lines = content.split("\n")
        self.language = "Swift"

        self.symbols: List[GraphNode] = []
        self.relationships: List[dict] = []
        self.diagnostics: List[str] = []

    def parse(self) -> Tuple[List[GraphNode], List[dict], List[str]]:
        if not self.content.strip():
            return [], [], []

        parser = _get_parser()
        if parser is None:
            self.diagnostics.append(
                "Swift parser unavailable: tree-sitter-swift package not installed. "
                "Install with: pip install tree-sitter tree-sitter-swift"
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
            if child.type == "import_declaration":
                self._extract_import(child, file_id)
            elif child.type == "class_declaration":
                self._extract_class(child, file_id)
            elif child.type in ("struct_declaration", "struct_specifier"):
                self._extract_struct(child, file_id)
            elif child.type == "function_declaration":
                self._extract_function(child, file_id)
            elif child.type == "protocol_declaration":
                self._extract_protocol(child, file_id)
            elif child.type == "enum_declaration":
                self._extract_enum(child, file_id)

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

    def _extract_import(self, node: Node, parent_id: str) -> None:
        for child in node.children:
            if child.type == "identifier" and child.is_named:
                imp_path = self._node_text(child)
                name = imp_path.split(".")[-1] if "." in imp_path else imp_path
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
                break

    def _extract_class(self, node: Node, parent_id: str) -> None:
        name_node = self._find_child(node, "type_identifier")
        if not name_node:
            return
        name = self._node_text(name_node)
        symbol_id, qname = self._make_symbol(name)
        start_line, end_line = self._node_lines(node)

        # Determine inheritance
        bases = []
        for child in node.children:
            if child.type in ("class_heritage", "inheritance_clause"):
                for sub in child.children:
                    if sub.type == "type_identifier":
                        bases.append(self._node_text(sub))

        self.symbols.append(GraphNode(
            id=symbol_id, name=name, qualified_name=qname,
            kind="class", file_path=self.file_path, language=self.language,
            start_line=start_line, end_line=end_line, parent_id=parent_id,
            metadata={"bases": bases},
        ))
        self.relationships.append({
            "source_id": parent_id, "target_id": symbol_id,
            "relationship": RelationshipType.CONTAINS.value,
            "confidence": ConfidenceLevel.EXACT.value, "source_lines": [start_line],
        })

        for base in bases:
            self.relationships.append({
                "source_id": symbol_id, "target_id": f"__external__::{base}",
                "relationship": RelationshipType.INHERITS.value,
                "confidence": ConfidenceLevel.HIGH.value, "source_lines": [start_line],
            })

        class_body = self._find_child(node, "class_body")
        if class_body:
            self._extract_class_body(class_body, symbol_id, name)

    def _extract_struct(self, node: Node, parent_id: str) -> None:
        name_node = self._find_child(node, "type_identifier")
        if not name_node:
            return
        name = self._node_text(name_node)
        symbol_id, qname = self._make_symbol(name)
        start_line, end_line = self._node_lines(node)

        self.symbols.append(GraphNode(
            id=symbol_id, name=name, qualified_name=qname,
            kind="struct", file_path=self.file_path, language=self.language,
            start_line=start_line, end_line=end_line, parent_id=parent_id,
        ))
        self.relationships.append({
            "source_id": parent_id, "target_id": symbol_id,
            "relationship": RelationshipType.CONTAINS.value,
            "confidence": ConfidenceLevel.EXACT.value, "source_lines": [start_line],
        })

        class_body = self._find_child(node, "class_body")
        if class_body:
            self._extract_class_body(class_body, symbol_id, name)

    def _extract_class_body(self, node: Node, class_id: str, class_name: str) -> None:
        for child in node.children:
            if not child.is_named:
                continue
            if child.type == "function_declaration":
                self._extract_method(child, class_id, class_name)
            elif child.type == "property_declaration":
                self._extract_property(child, class_id, class_name)
            elif child.type == "init_declaration":
                self._extract_initializer(child, class_id, class_name)

    def _extract_function(self, node: Node, parent_id: str) -> None:
        for child in node.children:
            if child.type in ("simple_identifier", "identifier"):
                name = self._node_text(child)
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
                return

    def _extract_method(self, node: Node, class_id: str, class_name: str) -> None:
        for child in node.children:
            if child.type in ("simple_identifier", "identifier"):
                name = self._node_text(child)
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
                return

    def _extract_property(self, node: Node, class_id: str, class_name: str) -> None:
        for child in node.children:
            if child.type == "value_binding_pattern":
                for sub in child.children:
                    if sub.type == "pattern" and sub.is_named:
                        for sub2 in sub.children:
                            if sub2.type == "identifier":
                                prop_name = self._node_text(sub2)
                                p_id, p_qname = self._make_symbol(prop_name, [class_name])
                                pl, _ = self._node_lines(sub2)
                                self.symbols.append(GraphNode(
                                    id=p_id, name=prop_name, qualified_name=p_qname,
                                    kind="property", file_path=self.file_path, language=self.language,
                                    start_line=pl, end_line=pl, parent_id=class_id,
                                ))
                                return

    def _extract_initializer(self, node: Node, class_id: str, class_name: str) -> None:
        name = "init"
        symbol_id, qname = self._make_symbol(name, [class_name])
        start_line, end_line = self._node_lines(node)
        self.symbols.append(GraphNode(
            id=symbol_id, name=name, qualified_name=qname,
            kind="initializer", file_path=self.file_path, language=self.language,
            start_line=start_line, end_line=end_line, parent_id=class_id,
        ))
        self.relationships.append({
            "source_id": class_id, "target_id": symbol_id,
            "relationship": RelationshipType.CONTAINS.value,
            "confidence": ConfidenceLevel.EXACT.value, "source_lines": [start_line],
        })

    def _extract_protocol(self, node: Node, parent_id: str) -> None:
        name_node = self._find_child(node, "type_identifier")
        if not name_node:
            return
        name = self._node_text(name_node)
        symbol_id, qname = self._make_symbol(name)
        start_line, end_line = self._node_lines(node)

        self.symbols.append(GraphNode(
            id=symbol_id, name=name, qualified_name=qname,
            kind="protocol", file_path=self.file_path, language=self.language,
            start_line=start_line, end_line=end_line, parent_id=parent_id,
        ))
        self.relationships.append({
            "source_id": parent_id, "target_id": symbol_id,
            "relationship": RelationshipType.CONTAINS.value,
            "confidence": ConfidenceLevel.EXACT.value, "source_lines": [start_line],
        })

    def _extract_enum(self, node: Node, parent_id: str) -> None:
        name_node = self._find_child(node, "type_identifier")
        if not name_node:
            return
        name = self._node_text(name_node)
        symbol_id, qname = self._make_symbol(name)
        start_line, end_line = self._node_lines(node)

        self.symbols.append(GraphNode(
            id=symbol_id, name=name, qualified_name=qname,
            kind="enum", file_path=self.file_path, language=self.language,
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
        return language.lower() in {"swift"}
