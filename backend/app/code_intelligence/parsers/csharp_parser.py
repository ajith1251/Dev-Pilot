"""
C# symbol parser — extracts symbols and relationships using tree-sitter.

Extracts:
- Using directives (imports)
- Namespaces
- Classes (with methods, properties, fields)
- Interfaces
- Structs
- Enums
- Methods
- Properties
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
        import tree_sitter_c_sharp as tscs  # type: ignore[import-untyped]

        lang = Language(tscs.language())
        _parser = Parser(lang)
        return _parser
    except Exception:
        _TREE_SITTER_AVAILABLE = False
        return None


class CSharpSymbolParser:
    """Parse C# source and extract structured symbols with relationships."""

    def __init__(self, file_path: str, content: str) -> None:
        self.file_path = file_path
        self.content = content
        self.bytes_content = content.encode("utf-8") if isinstance(content, str) else content
        self.lines = content.split("\n")
        self.language = "C#"

        self.symbols: List[GraphNode] = []
        self.relationships: List[dict] = []
        self.diagnostics: List[str] = []

    def parse(self) -> Tuple[List[GraphNode], List[dict], List[str]]:
        if not self.content.strip():
            return [], [], []

        parser = _get_parser()
        if parser is None:
            self.diagnostics.append(
                "C# parser unavailable: tree-sitter-c-sharp package not installed. "
                "Install with: pip install tree-sitter tree-sitter-c-sharp"
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
            if child.type == "using_directive":
                self._extract_using(child, file_id)
            elif child.type == "namespace_declaration":
                self._extract_namespace(child, file_id)
            elif child.type == "class_declaration":
                self._extract_class(child, file_id)
            elif child.type == "interface_declaration":
                self._extract_interface(child, file_id)
            elif child.type == "struct_declaration":
                self._extract_struct(child, file_id)
            elif child.type == "enum_declaration":
                self._extract_enum(child, file_id)

        return self.symbols, self.relationships, self.diagnostics

    def _create_file_node(self) -> str:
        module_name = self.file_path.replace("/", ".").rsplit(".", 1)[0]
        module_name = module_name.replace("\\", ".")
        file_id = make_symbol_id(self.file_path, module_name)
        self.symbols.append(GraphNode(
            id=file_id, name=self.file_path.split("/")[-1],
            qualified_name=module_name, kind="file",
            file_path=self.file_path, language=self.language,
            start_line=1, end_line=len(self.lines),
        ))
        return file_id

    def _extract_using(self, node: Node, parent_id: str) -> None:
        name_node = None
        for child in node.children:
            if child.type in ("identifier", "qualified_name"):
                name_node = child
                break
        if not name_node:
            return
        ns = self._node_text(name_node)
        symbol_id = make_symbol_id(self.file_path, f"import:{ns}")
        start_line, _ = self._node_lines(node)

        self.symbols.append(GraphNode(
            id=symbol_id, name=ns.split(".")[-1], qualified_name=f"import:{ns}",
            kind="import", file_path=self.file_path, language=self.language,
            start_line=start_line, end_line=start_line,
            metadata={"namespace": ns},
        ))
        self.relationships.append({
            "source_id": parent_id, "target_id": symbol_id,
            "relationship": RelationshipType.IMPORTS.value,
            "confidence": ConfidenceLevel.EXACT.value, "source_lines": [start_line],
        })

    def _extract_namespace(self, node: Node, parent_id: str) -> None:
        name_node = self._find_child(node, "identifier")
        if not name_node:
            return
        name = self._node_text(name_node)
        symbol_id, qname = self._make_symbol(name)
        start_line, end_line = self._node_lines(node)

        self.symbols.append(GraphNode(
            id=symbol_id, name=name, qualified_name=qname,
            kind="namespace", file_path=self.file_path, language=self.language,
            start_line=start_line, end_line=end_line, parent_id=parent_id,
        ))
        self.relationships.append({
            "source_id": parent_id, "target_id": symbol_id,
            "relationship": RelationshipType.CONTAINS.value,
            "confidence": ConfidenceLevel.EXACT.value, "source_lines": [start_line],
        })

        decl_list = self._find_child(node, "declaration_list")
        if decl_list:
            self._extract_declaration_list(decl_list, symbol_id)

    def _extract_declaration_list(self, node: Node, parent_id: str) -> None:
        for child in node.children:
            if not child.is_named:
                continue
            if child.type == "class_declaration":
                self._extract_class(child, parent_id)
            elif child.type == "interface_declaration":
                self._extract_interface(child, parent_id)
            elif child.type == "struct_declaration":
                self._extract_struct(child, parent_id)
            elif child.type == "enum_declaration":
                self._extract_enum(child, parent_id)

    def _extract_class(self, node: Node, parent_id: str) -> None:
        name_node = self._find_child(node, "identifier")
        if not name_node:
            return
        name = self._node_text(name_node)

        # Bases
        bases = []
        base_list = self._find_child(node, "base_list")
        if base_list:
            for child in base_list.children:
                if child.type in ("identifier", "qualified_name"):
                    bases.append(self._node_text(child))

        symbol_id, qname = self._make_symbol(name)
        start_line, end_line = self._node_lines(node)

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

        decl_list = self._find_child(node, "declaration_list")
        if decl_list:
            for child in decl_list.children:
                if child.type == "method_declaration":
                    self._extract_method(child, symbol_id, name)
                elif child.type == "property_declaration":
                    self._extract_property(child, symbol_id, name)
                elif child.type == "field_declaration":
                    self._extract_field(child, symbol_id, name)
                elif child.type in ("class_declaration", "interface_declaration"):
                    self._extract_class(child, symbol_id)

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

    def _extract_struct(self, node: Node, parent_id: str) -> None:
        name_node = self._find_child(node, "identifier")
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

    def _extract_enum(self, node: Node, parent_id: str) -> None:
        name_node = self._find_child(node, "identifier")
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

    def _extract_property(self, node: Node, class_id: str, class_name: str) -> None:
        name_node = self._find_child(node, "identifier")
        if not name_node:
            return
        name = self._node_text(name_node)
        symbol_id, qname = self._make_symbol(name, [class_name])
        start_line, _ = self._node_lines(node)

        self.symbols.append(GraphNode(
            id=symbol_id, name=name, qualified_name=qname,
            kind="property", file_path=self.file_path, language=self.language,
            start_line=start_line, end_line=start_line, parent_id=class_id,
        ))
        self.relationships.append({
            "source_id": class_id, "target_id": symbol_id,
            "relationship": RelationshipType.CONTAINS.value,
            "confidence": ConfidenceLevel.EXACT.value, "source_lines": [start_line],
        })

    def _extract_field(self, node: Node, class_id: str, class_name: str) -> None:
        for child in node.children:
            if child.type == "variable_declaration":
                for sub in child.children:
                    if sub.type == "identifier":
                        field_name = self._node_text(sub)
                        f_id, f_qname = self._make_symbol(field_name, [class_name])
                        fl, _ = self._node_lines(sub)
                        self.symbols.append(GraphNode(
                            id=f_id, name=field_name, qualified_name=f_qname,
                            kind="field", file_path=self.file_path, language=self.language,
                            start_line=fl, end_line=fl, parent_id=class_id,
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
        return language.lower() in {"c#", "csharp", "c-sharp"}
