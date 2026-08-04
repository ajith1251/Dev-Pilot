"""
Go symbol parser — extracts symbols and relationships using tree-sitter.

Extracts:
- Package declaration
- Imports
- Functions
- Methods (with receiver type)
- Struct types (with fields)
- Interface types
- Constants / Variables
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
        import tree_sitter_go as tsgo  # type: ignore[import-untyped]

        lang = Language(tsgo.language())
        _parser = Parser(lang)
        return _parser
    except Exception:
        _TREE_SITTER_AVAILABLE = False
        return None


class GoSymbolParser:
    """Parse Go source and extract structured symbols with relationships."""

    def __init__(self, file_path: str, content: str) -> None:
        self.file_path = file_path
        self.content = content
        self.bytes_content = content.encode("utf-8") if isinstance(content, str) else content
        self.lines = content.split("\n")
        self.language = "Go"

        self.symbols: List[GraphNode] = []
        self.relationships: List[dict] = []
        self.diagnostics: List[str] = []

        self._package_name: str = ""
        self._type_names: Dict[str, str] = {}  # type_node_text -> symbol_id

    def parse(self) -> Tuple[List[GraphNode], List[dict], List[str]]:
        if not self.content.strip():
            return [], [], []

        parser = _get_parser()
        if parser is None:
            self.diagnostics.append(
                "Go parser unavailable: tree-sitter-go package not installed. "
                "Install with: pip install tree-sitter tree-sitter-go"
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

        # Extract package
        self._extract_package(root)

        file_id = self._create_file_node()

        # Pass 1: collect type names for method receiver resolution
        for child in root.children:
            if child.type == "type_declaration":
                self._collect_type_names(child)

        # Pass 2: extract all top-level constructs
        for child in root.children:
            if not child.is_named:
                continue
            if child.type == "function_declaration":
                self._extract_function(child, file_id)
            elif child.type == "method_declaration":
                self._extract_method(child, file_id)
            elif child.type == "type_declaration":
                self._extract_type_decl(child, file_id)
            elif child.type == "import_declaration":
                self._extract_import(child, file_id)
            elif child.type == "const_declaration":
                self._extract_const(child, file_id)
            elif child.type == "var_declaration":
                self._extract_var(child, file_id)

        return self.symbols, self.relationships, self.diagnostics

    # ── Setup ─────────────────────────────────────────────────

    def _create_file_node(self) -> str:
        module_name = self.file_path.replace("/", ".").rsplit(".", 1)[0]
        module_name = module_name.replace("\\", ".")
        file_id = make_symbol_id(self.file_path, module_name)
        self.symbols.append(GraphNode(
            id=file_id,
            name=self.file_path.split("/")[-1] or self.file_path,
            qualified_name=module_name,
            kind="file",
            file_path=self.file_path,
            language=self.language,
            start_line=1,
            end_line=len(self.lines),
        ))
        return file_id

    def _extract_package(self, root: Node) -> None:
        for child in root.children:
            if child.type == "package_clause":
                for sub in child.children:
                    if sub.type == "package_identifier":
                        self._package_name = self._node_text(sub)

    def _collect_type_names(self, node: Node) -> None:
        """Pre-scan type declarations to build a name→id map for method receivers."""
        for child in node.children:
            if child.type == "type_spec":
                name_node = self._find_child(child, "type_identifier")
                if name_node:
                    name = self._node_text(name_node)
                    qname = normalize_qualified_name(self.file_path, name)
                    sid = make_symbol_id(self.file_path, qname)
                    self._type_names[name] = sid

    # ── Functions ─────────────────────────────────────────────

    def _extract_function(self, node: Node, parent_id: str) -> None:
        """Extract a top-level function declaration."""
        name_node = self._find_child(node, "identifier")
        if not name_node:
            return
        name = self._node_text(name_node)
        symbol_id, qname = self._make_symbol(name)
        start_line, end_line = self._node_lines(node)

        self.symbols.append(GraphNode(
            id=symbol_id,
            name=name,
            qualified_name=qname,
            kind="function",
            file_path=self.file_path,
            language=self.language,
            start_line=start_line,
            end_line=end_line,
            parent_id=parent_id,
            signature=self._node_text(node)[:200],
            metadata={"package": self._package_name},
        ))

        self.relationships.append({
            "source_id": parent_id,
            "target_id": symbol_id,
            "relationship": RelationshipType.CONTAINS.value,
            "confidence": ConfidenceLevel.EXACT.value,
            "source_lines": [start_line],
            "resolution_detail": f"function {name}",
        })

    @staticmethod
    def _extract_receiver_type(receiver: Node) -> str:
        """Extract the receiver type name from a method's receiver parameter list.

        tree-sitter Go nests the receiver type inside parameter_declaration:

        Value receiver  (u User)    → parameter_list → parameter_declaration → type_identifier
        Pointer receiver (u *User)   → parameter_list → parameter_declaration → pointer_type → type_identifier
        """
        for child in receiver.children:
            if child.type != "parameter_declaration":
                continue
            for decl_child in child.children:
                if decl_child.type == "type_identifier":
                    return decl_child.text.decode("utf-8", errors="replace")
                elif decl_child.type == "pointer_type":
                    for ptr_child in decl_child.children:
                        if ptr_child.type == "type_identifier":
                            return ptr_child.text.decode("utf-8", errors="replace")
                elif decl_child.type == "qualified_type":
                    # qualified_type like pkg.Type — extract just the type name
                    return decl_child.text.decode("utf-8", errors="replace")
        return ""

    def _extract_method(self, node: Node, parent_id: str) -> None:
        """Extract a method declaration with receiver."""
        name_node = self._find_child(node, "field_identifier")
        if not name_node:
            return
        name = self._node_text(name_node)

        # Extract receiver type by recursing into parameter_declaration
        receiver = self._find_child(node, "parameter_list")
        receiver_type = self._extract_receiver_type(receiver) if receiver else ""

        parent_names = [receiver_type] if receiver_type else []
        symbol_id, qname = self._make_symbol(name, parent_names)
        start_line, end_line = self._node_lines(node)

        self.symbols.append(GraphNode(
            id=symbol_id,
            name=name,
            qualified_name=qname,
            kind="method",
            file_path=self.file_path,
            language=self.language,
            start_line=start_line,
            end_line=end_line,
            parent_id=parent_id,
            signature=self._node_text(node)[:200],
            metadata={"receiver_type": receiver_type, "package": self._package_name},
        ))

        self.relationships.append({
            "source_id": parent_id,
            "target_id": symbol_id,
            "relationship": RelationshipType.CONTAINS.value,
            "confidence": ConfidenceLevel.EXACT.value,
            "source_lines": [start_line],
            "resolution_detail": f"method {name} on {receiver_type}" if receiver_type else f"method {name}",
        })

        # Link method to its receiver type if known
        if receiver_type and receiver_type in self._type_names:
            self.relationships.append({
                "source_id": symbol_id,
                "target_id": self._type_names[receiver_type],
                "relationship": RelationshipType.MEMBER_OF.value,
                "confidence": ConfidenceLevel.HIGH.value,
                "source_lines": [start_line],
                "resolution_detail": f"method {name} belongs to {receiver_type}",
            })

    # ── Types ─────────────────────────────────────────────────

    def _extract_type_decl(self, node: Node, parent_id: str) -> None:
        """Extract a type declaration (struct, interface, or type alias)."""
        for child in node.children:
            if child.type == "type_spec":
                name_node = self._find_child(child, "type_identifier")
                if not name_node:
                    continue
                name = self._node_text(name_node)

                # Determine the type kind
                type_node = None
                for sub in child.children:
                    if sub.type in ("struct_type", "interface_type"):
                        type_node = sub
                        break

                symbol_id, qname = self._make_symbol(name)
                start_line, end_line = self._node_lines(child)

                if type_node and type_node.type == "struct_type":
                    kind = "struct"
                    fields = self._extract_struct_fields(type_node, symbol_id, name)
                elif type_node and type_node.type == "interface_type":
                    kind = "interface"
                    methods = self._extract_interface_methods(type_node, symbol_id, name)
                    # Track for INHERITS from implementing types
                    self._type_names[name] = symbol_id
                else:
                    kind = "type"

                self.symbols.append(GraphNode(
                    id=symbol_id,
                    name=name,
                    qualified_name=qname,
                    kind=kind,
                    file_path=self.file_path,
                    language=self.language,
                    start_line=start_line,
                    end_line=end_line,
                    parent_id=parent_id,
                    signature=f"type {name} {kind}" if kind in ("struct", "interface") else f"type {name}",
                ))

                self.relationships.append({
                    "source_id": parent_id,
                    "target_id": symbol_id,
                    "relationship": RelationshipType.CONTAINS.value,
                    "confidence": ConfidenceLevel.EXACT.value,
                    "source_lines": [start_line],
                })

    def _extract_struct_fields(self, node: Node, struct_id: str, struct_name: str) -> None:
        """Extract fields from a struct type."""
        field_decl_list = self._find_child(node, "field_declaration_list")
        if not field_decl_list:
            return
        for field_node in field_decl_list.children:
            if field_node.type == "field_declaration":
                for sub in field_node.children:
                    if sub.type == "field_identifier":
                        field_name = self._node_text(sub)
                        f_id, f_qname = self._make_symbol(field_name, [struct_name])
                        fl, _ = self._node_lines(sub)

                        self.symbols.append(GraphNode(
                            id=f_id,
                            name=field_name,
                            qualified_name=f_qname,
                            kind="field",
                            file_path=self.file_path,
                            language=self.language,
                            start_line=fl,
                            end_line=fl,
                            parent_id=struct_id,
                        ))

                        self.relationships.append({
                            "source_id": struct_id,
                            "target_id": f_id,
                            "relationship": RelationshipType.CONTAINS.value,
                            "confidence": ConfidenceLevel.EXACT.value,
                            "source_lines": [fl],
                        })

    def _extract_interface_methods(self, node: Node, iface_id: str, iface_name: str) -> None:
        """Extract method signatures from an interface type.

        tree-sitter-go uses 'method_elem' for interface method declarations.
        """
        method_elems = [c for c in node.children if c.type == "method_elem"]
        for ms in method_elems:
            name_node = self._find_child(ms, "field_identifier")
            if not name_node:
                continue
            method_name = self._node_text(name_node)
            m_id, m_qname = self._make_symbol(method_name, [iface_name])
            ml, _ = self._node_lines(ms)

            self.symbols.append(GraphNode(
                id=m_id,
                name=method_name,
                qualified_name=m_qname,
                kind="abstract_method",
                file_path=self.file_path,
                language=self.language,
                start_line=ml,
                end_line=ml,
                parent_id=iface_id,
            ))

            self.relationships.append({
                "source_id": iface_id,
                "target_id": m_id,
                "relationship": RelationshipType.CONTAINS.value,
                "confidence": ConfidenceLevel.EXACT.value,
                "source_lines": [ml],
            })

    # ── Imports ───────────────────────────────────────────────

    def _extract_import(self, node: Node, parent_id: str) -> None:
        """Extract import declarations.

        Handles both single imports (import \"fmt\") and grouped imports
        (import (\"fmt\" \"os\")). Grouped imports have an intermediate
        import_spec_list node.
        """
        # Collect all import_spec nodes (possibly nested in import_spec_list)
        import_specs: List[Node] = []
        for child in node.children:
            if child.type == "import_spec":
                import_specs.append(child)
            elif child.type == "import_spec_list":
                for sub in child.children:
                    if sub.type == "import_spec":
                        import_specs.append(sub)

        for child in import_specs:
            path_node = self._find_child(child, "interpreted_string_literal")
            if not path_node:
                continue
            import_path = self._node_text(path_node).strip('"')
            name = import_path.split("/")[-1]

            # Check for alias
            alias_node = self._find_child(child, "package_identifier")
            if alias_node:
                name = self._node_text(alias_node)

            symbol_id = make_symbol_id(self.file_path, f"import:{import_path}")
            start_line, _ = self._node_lines(child)

            self.symbols.append(GraphNode(
                id=symbol_id,
                name=name,
                qualified_name=f"import:{import_path}",
                kind="import",
                file_path=self.file_path,
                language=self.language,
                start_line=start_line,
                end_line=start_line,
                metadata={"import_path": import_path},
            ))

            self.relationships.append({
                "source_id": parent_id,
                "target_id": symbol_id,
                "relationship": RelationshipType.IMPORTS.value,
                "confidence": ConfidenceLevel.EXACT.value,
                "source_lines": [start_line],
                "resolution_detail": f"import {import_path}",
            })

    # ── Constants / Variables ─────────────────────────────────

    def _extract_const(self, node: Node, parent_id: str) -> None:
        """Extract const declarations."""
        for child in node.children:
            if child.type == "const_spec":
                name_node = self._find_child(child, "identifier")
                if not name_node:
                    continue
                name = self._node_text(name_node)
                symbol_id, qname = self._make_symbol(name)
                start_line, _ = self._node_lines(child)

                self.symbols.append(GraphNode(
                    id=symbol_id,
                    name=name,
                    qualified_name=qname,
                    kind="constant",
                    file_path=self.file_path,
                    language=self.language,
                    start_line=start_line,
                    end_line=start_line,
                    parent_id=parent_id,
                    metadata={"package": self._package_name},
                ))

                self.relationships.append({
                    "source_id": parent_id,
                    "target_id": symbol_id,
                    "relationship": RelationshipType.CONTAINS.value,
                    "confidence": ConfidenceLevel.EXACT.value,
                    "source_lines": [start_line],
                })

    def _extract_var(self, node: Node, parent_id: str) -> None:
        """Extract var declarations (top-level variables)."""
        for child in node.children:
            if child.type == "var_spec":
                name_node = self._find_child(child, "identifier")
                if not name_node:
                    continue
                name = self._node_text(name_node)
                symbol_id, qname = self._make_symbol(name)
                start_line, _ = self._node_lines(child)

                self.symbols.append(GraphNode(
                    id=symbol_id,
                    name=name,
                    qualified_name=qname,
                    kind="variable",
                    file_path=self.file_path,
                    language=self.language,
                    start_line=start_line,
                    end_line=start_line,
                    parent_id=parent_id,
                ))

                self.relationships.append({
                    "source_id": parent_id,
                    "target_id": symbol_id,
                    "relationship": RelationshipType.CONTAINS.value,
                    "confidence": ConfidenceLevel.EXACT.value,
                    "source_lines": [start_line],
                })

    # ── Helpers ───────────────────────────────────────────────

    def _make_symbol(self, name: str, parent_names: Optional[List[str]] = None) -> Tuple[str, str]:
        qname = normalize_qualified_name(self.file_path, name, parent_names)
        symbol_id = make_symbol_id(self.file_path, qname)
        return symbol_id, qname

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
        return language.lower() in {"go", "golang"}
