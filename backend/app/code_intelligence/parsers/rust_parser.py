"""
Rust symbol parser — extracts symbols and relationships using tree-sitter.

Extracts:
- Use declarations (imports)
- Structs (with fields)
- Enums (with variants)
- Traits
- Implementations (impl blocks)
- Functions
- Constants / Statics
- Type aliases
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
        import tree_sitter_rust as tsrust  # type: ignore[import-untyped]

        lang = Language(tsrust.language())
        _parser = Parser(lang)
        return _parser
    except Exception:
        _TREE_SITTER_AVAILABLE = False
        return None


class RustSymbolParser:
    """Parse Rust source and extract structured symbols with relationships."""

    def __init__(self, file_path: str, content: str) -> None:
        self.file_path = file_path
        self.content = content
        self.bytes_content = content.encode("utf-8") if isinstance(content, str) else content
        self.lines = content.split("\n")
        self.language = "Rust"

        self.symbols: List[GraphNode] = []
        self.relationships: List[dict] = []
        self.diagnostics: List[str] = []

        # Type tracking for impl blocks
        self._type_ids: Dict[str, str] = {}  # name -> symbol_id

    def parse(self) -> Tuple[List[GraphNode], List[dict], List[str]]:
        if not self.content.strip():
            return [], [], []

        parser = _get_parser()
        if parser is None:
            self.diagnostics.append(
                "Rust parser unavailable: tree-sitter-rust package not installed. "
                "Install with: pip install tree-sitter tree-sitter-rust"
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

        # Pass 1: collect struct/enum/trait names for impl resolution
        for child in root.children:
            if child.type in ("struct_item", "enum_item", "trait_item", "type_item"):
                name_node = self._find_child(child, "type_identifier")
                if not name_node:
                    name_node = self._find_child(child, "identifier")
                if name_node:
                    name = self._node_text(name_node)
                    qname = normalize_qualified_name(self.file_path, name)
                    sid = make_symbol_id(self.file_path, qname)
                    self._type_ids[name] = sid

        # Pass 2: extract all top-level items
        for child in root.children:
            if not child.is_named:
                continue
            if child.type == "function_item":
                self._extract_function(child, file_id)
            elif child.type == "struct_item":
                self._extract_struct(child, file_id)
            elif child.type == "enum_item":
                self._extract_enum(child, file_id)
            elif child.type == "trait_item":
                self._extract_trait(child, file_id)
            elif child.type == "impl_item":
                self._extract_impl(child, file_id)
            elif child.type == "use_declaration":
                self._extract_use(child, file_id)
            elif child.type == "const_item":
                self._extract_const(child, file_id)
            elif child.type == "static_item":
                self._extract_static(child, file_id)
            elif child.type == "type_item":
                self._extract_type_alias(child, file_id)
            elif child.type == "mod_item":
                self._extract_module(child, file_id)

        return self.symbols, self.relationships, self.diagnostics

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

    # ── Functions ─────────────────────────────────────────────

    def _extract_function(self, node: Node, parent_id: str) -> None:
        """Extract a function item."""
        name_node = self._find_child(node, "identifier")
        if not name_node:
            return
        name = self._node_text(name_node)
        visibility = "pub" if self._is_pub(node) else "private"

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
            metadata={"visibility": visibility},
        ))

        self.relationships.append({
            "source_id": parent_id,
            "target_id": symbol_id,
            "relationship": RelationshipType.CONTAINS.value,
            "confidence": ConfidenceLevel.EXACT.value,
            "source_lines": [start_line],
            "resolution_detail": f"fn {name}",
        })

    # ── Structs ───────────────────────────────────────────────

    def _extract_struct(self, node: Node, parent_id: str) -> None:
        """Extract a struct item with fields."""
        name_node = self._find_child(node, "type_identifier")
        if not name_node:
            return
        name = self._node_text(name_node)
        visibility = "pub" if self._is_pub(node) else "private"

        symbol_id, qname = self._make_symbol(name)
        start_line, end_line = self._node_lines(node)

        self.symbols.append(GraphNode(
            id=symbol_id,
            name=name,
            qualified_name=qname,
            kind="struct",
            file_path=self.file_path,
            language=self.language,
            start_line=start_line,
            end_line=end_line,
            parent_id=parent_id,
            signature=self._node_text(node)[:200],
            metadata={"visibility": visibility},
        ))

        self.relationships.append({
            "source_id": parent_id,
            "target_id": symbol_id,
            "relationship": RelationshipType.CONTAINS.value,
            "confidence": ConfidenceLevel.EXACT.value,
            "source_lines": [start_line],
        })

        # Extract fields
        field_decl_list = self._find_child(node, "field_declaration_list")
        if field_decl_list:
            self._extract_struct_fields(field_decl_list, symbol_id, name)

        # Record for impl resolution
        self._type_ids[name] = symbol_id

    def _extract_struct_fields(self, node: Node, struct_id: str, struct_name: str) -> None:
        """Extract fields from a struct's field_declaration_list."""
        for child in node.children:
            if child.type == "field_declaration":
                name_node = self._find_child(child, "field_identifier")
                if not name_node:
                    continue
                field_name = self._node_text(name_node)
                f_id, f_qname = self._make_symbol(field_name, [struct_name])
                fl, _ = self._node_lines(child)

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

    # ── Enums ─────────────────────────────────────────────────

    def _extract_enum(self, node: Node, parent_id: str) -> None:
        """Extract an enum item with variants."""
        name_node = self._find_child(node, "type_identifier")
        if not name_node:
            return
        name = self._node_text(name_node)
        visibility = "pub" if self._is_pub(node) else "private"

        symbol_id, qname = self._make_symbol(name)
        start_line, end_line = self._node_lines(node)

        self.symbols.append(GraphNode(
            id=symbol_id,
            name=name,
            qualified_name=qname,
            kind="enum",
            file_path=self.file_path,
            language=self.language,
            start_line=start_line,
            end_line=end_line,
            parent_id=parent_id,
            metadata={"visibility": visibility},
        ))

        self.relationships.append({
            "source_id": parent_id,
            "target_id": symbol_id,
            "relationship": RelationshipType.CONTAINS.value,
            "confidence": ConfidenceLevel.EXACT.value,
            "source_lines": [start_line],
        })

        # Extract variants (tree-sitter-rust uses 'enum_variant_list' not 'enum_body')
        enum_variant_list = self._find_child(node, "enum_variant_list")
        if enum_variant_list:
            for variant in enum_variant_list.children:
                if variant.type == "enum_variant":
                    var_name_node = self._find_child(variant, "identifier")
                    if var_name_node:
                        var_name = self._node_text(var_name_node)
                        v_id, v_qname = self._make_symbol(var_name, [name])
                        vl, _ = self._node_lines(variant)

                        self.symbols.append(GraphNode(
                            id=v_id,
                            name=var_name,
                            qualified_name=v_qname,
                            kind="enum_variant",
                            file_path=self.file_path,
                            language=self.language,
                            start_line=vl,
                            end_line=vl,
                            parent_id=symbol_id,
                        ))

                        self.relationships.append({
                            "source_id": symbol_id,
                            "target_id": v_id,
                            "relationship": RelationshipType.CONTAINS.value,
                            "confidence": ConfidenceLevel.EXACT.value,
                            "source_lines": [vl],
                        })

        self._type_ids[name] = symbol_id

    # ── Traits ────────────────────────────────────────────────

    def _extract_trait(self, node: Node, parent_id: str) -> None:
        """Extract a trait item."""
        name_node = self._find_child(node, "type_identifier")
        if not name_node:
            return
        name = self._node_text(name_node)
        visibility = "pub" if self._is_pub(node) else "private"

        symbol_id, qname = self._make_symbol(name)
        start_line, end_line = self._node_lines(node)

        self.symbols.append(GraphNode(
            id=symbol_id,
            name=name,
            qualified_name=qname,
            kind="trait",
            file_path=self.file_path,
            language=self.language,
            start_line=start_line,
            end_line=end_line,
            parent_id=parent_id,
            metadata={"visibility": visibility},
        ))

        self.relationships.append({
            "source_id": parent_id,
            "target_id": symbol_id,
            "relationship": RelationshipType.CONTAINS.value,
            "confidence": ConfidenceLevel.EXACT.value,
            "source_lines": [start_line],
        })

        self._type_ids[name] = symbol_id

    # ── Impl Blocks ───────────────────────────────────────────

    def _extract_impl(self, node: Node, parent_id: str) -> None:
        """Extract an impl block (inherent or trait impl)."""
        # Determine what type this impl is for
        for child in node.children:
            if child.type == "type_identifier":
                impl_type = self._node_text(child)
                impl_type_id = self._type_ids.get(impl_type)

                # Extract methods in this impl block
                decl_list = self._find_child(node, "declaration_list")
                if decl_list:
                    for decl in decl_list.children:
                        if decl.type == "function_item":
                            self._extract_impl_method(decl, impl_type, impl_type_id, parent_id)
                break

    def _extract_impl_method(self, node: Node, impl_type: str, type_id: Optional[str], parent_id: str) -> None:
        """Extract a method inside an impl block."""
        name_node = self._find_child(node, "identifier")
        if not name_node:
            return
        name = self._node_text(name_node)

        symbol_id, qname = self._make_symbol(name, [impl_type])
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
            metadata={"impl_type": impl_type},
        ))

        # Link to the type via MEMBER_OF
        if type_id:
            self.relationships.append({
                "source_id": symbol_id,
                "target_id": type_id,
                "relationship": RelationshipType.MEMBER_OF.value,
                "confidence": ConfidenceLevel.HIGH.value,
                "source_lines": [start_line],
                "resolution_detail": f"method {name} on {impl_type}",
            })

    # ── Use Declarations ──────────────────────────────────────

    def _extract_use(self, node: Node, parent_id: str) -> None:
        """Extract a use declaration (import)."""
        path_text = self._node_text(node).removeprefix("use ").removesuffix(";")
        if not path_text:
            return

        # Handle use with alias
        name = path_text.split("::").pop()
        if " as " in name:
            parts = name.split(" as ")
            name = parts[1].strip()

        symbol_id = make_symbol_id(self.file_path, f"import:{path_text}")
        start_line, _ = self._node_lines(node)

        self.symbols.append(GraphNode(
            id=symbol_id,
            name=name,
            qualified_name=f"import:{path_text}",
            kind="import",
            file_path=self.file_path,
            language=self.language,
            start_line=start_line,
            end_line=start_line,
            metadata={"import_path": path_text},
        ))

        self.relationships.append({
            "source_id": parent_id,
            "target_id": symbol_id,
            "relationship": RelationshipType.IMPORTS.value,
            "confidence": ConfidenceLevel.EXACT.value,
            "source_lines": [start_line],
            "resolution_detail": f"use {path_text}",
        })

    # ── Constants / Statics ───────────────────────────────────

    def _extract_const(self, node: Node, parent_id: str) -> None:
        """Extract a const item."""
        name_node = self._find_child(node, "identifier")
        if not name_node:
            return
        name = self._node_text(name_node)
        symbol_id, qname = self._make_symbol(name)
        start_line, _ = self._node_lines(node)

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
        ))

        self.relationships.append({
            "source_id": parent_id,
            "target_id": symbol_id,
            "relationship": RelationshipType.CONTAINS.value,
            "confidence": ConfidenceLevel.EXACT.value,
            "source_lines": [start_line],
        })

    def _extract_static(self, node: Node, parent_id: str) -> None:
        """Extract a static item."""
        name_node = self._find_child(node, "identifier")
        if not name_node:
            return
        name = self._node_text(name_node)
        mutable = "mut" in self._node_text(node)

        symbol_id, qname = self._make_symbol(name)
        start_line, _ = self._node_lines(node)

        self.symbols.append(GraphNode(
            id=symbol_id,
            name=name,
            qualified_name=qname,
            kind="static_variable",
            file_path=self.file_path,
            language=self.language,
            start_line=start_line,
            end_line=start_line,
            parent_id=parent_id,
            metadata={"mutable": mutable},
        ))

        self.relationships.append({
            "source_id": parent_id,
            "target_id": symbol_id,
            "relationship": RelationshipType.CONTAINS.value,
            "confidence": ConfidenceLevel.EXACT.value,
            "source_lines": [start_line],
        })

    # ── Type Aliases ──────────────────────────────────────────

    def _extract_type_alias(self, node: Node, parent_id: str) -> None:
        """Extract a type alias (type X = ...)."""
        name_node = self._find_child(node, "type_identifier")
        if not name_node:
            return
        name = self._node_text(name_node)
        symbol_id, qname = self._make_symbol(name)
        start_line, _ = self._node_lines(node)

        self.symbols.append(GraphNode(
            id=symbol_id,
            name=name,
            qualified_name=qname,
            kind="type",
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

        self._type_ids[name] = symbol_id

    # ── Module Items ──────────────────────────────────────────

    def _extract_module(self, node: Node, parent_id: str) -> None:
        """Extract a module item (mod name; or mod name { ... })."""
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
            kind="module",
            file_path=self.file_path,
            language=self.language,
            start_line=start_line,
            end_line=end_line,
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

    def _is_pub(self, node: Node) -> bool:
        """Check if a Rust item has pub visibility."""
        for child in node.children:
            if child.type == "visibility_modifier" or (not child.is_named and child.type == "pub"):
                return True
        return False

    def supports_language(self, language: str) -> bool:
        return language.lower() in {"rust", "rs"}
