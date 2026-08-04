"""
C/C++ symbol parser — extracts symbols and relationships using tree-sitter.

Handles both C and C++ (detected by file extension). Extracts:
- Preprocessor includes (imports)
- Functions
- Structs (with fields)
- Classes (C++: with methods, inheritance, access specifiers)
- Templates (C++)
- Enums
- Typedefs
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

_C_PARSER: Optional[Any] = None
_CPP_PARSER: Optional[Any] = None
_C_TREE_SITTER_AVAILABLE: bool = True
_CPP_TREE_SITTER_AVAILABLE: bool = True


def _get_parser(language: str) -> Optional[Any]:
    """Lazy-initialized tree-sitter parser for C or C++.

    Gracefully degrades if corresponding tree-sitter packages are not installed.
    Returns None if unavailable — caller handles the None case.
    """
    global _C_PARSER, _CPP_PARSER, _C_TREE_SITTER_AVAILABLE, _CPP_TREE_SITTER_AVAILABLE

    if language == "C":
        if _C_PARSER is not None:
            return _C_PARSER
        if not _C_TREE_SITTER_AVAILABLE:
            return None
        try:
            from tree_sitter import Language, Parser  # type: ignore[import-untyped]
            import tree_sitter_c as tsc  # type: ignore[import-untyped]
            lang = Language(tsc.language())
            _C_PARSER = Parser(lang)
            return _C_PARSER
        except Exception:
            _C_TREE_SITTER_AVAILABLE = False
            return None
    else:
        if _CPP_PARSER is not None:
            return _CPP_PARSER
        if not _CPP_TREE_SITTER_AVAILABLE:
            return None
        try:
            from tree_sitter import Language, Parser  # type: ignore[import-untyped]
            import tree_sitter_cpp as tscpp  # type: ignore[import-untyped]
            lang = Language(tscpp.language())
            _CPP_PARSER = Parser(lang)
            return _CPP_PARSER
        except Exception:
            _CPP_TREE_SITTER_AVAILABLE = False
            return None


_LANG_EXT_MAP = {".c": "C", ".h": "C", ".cpp": "C++", ".cc": "C++",
                  ".cxx": "C++", ".hpp": "C++", ".hh": "C++", ".hxx": "C++",
                  ".c++": "C++", ".h++": "C++"}


class CppSymbolParser:
    """Parse C/C++ source and extract structured symbols with relationships."""

    def __init__(self, file_path: str, content: str) -> None:
        self.file_path = file_path
        self.content = content
        self.bytes_content = content.encode("utf-8") if isinstance(content, str) else content
        self.lines = content.split("\n")

        ext = (file_path.rsplit(".", 1)[-1].lower() if "." in file_path else "")
        ext = f".{ext}"
        self.language = _LANG_EXT_MAP.get(ext, "C++")

        self.symbols: List[GraphNode] = []
        self.relationships: List[dict] = []
        self.diagnostics: List[str] = []
        self._current_class_id: Optional[str] = None
        self._current_class_name: str = ""
        self._type_ids: Dict[str, str] = {}

    def parse(self) -> Tuple[List[GraphNode], List[dict], List[str]]:
        if not self.content.strip():
            return [], [], []

        parser = _get_parser(self.language)
        if parser is None:
            pkg = "tree-sitter-c" if self.language == "C" else "tree-sitter-cpp"
            self.diagnostics.append(
                f"C/C++ parser unavailable: {pkg} package not installed. "
                f"Install with: pip install tree-sitter {pkg}"
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

        # First pass: collect struct/class/type names
        self._collect_type_names(root)

        # Second pass: extract all constructs
        for child in root.children:
            if not child.is_named:
                continue
            if child.type == "function_definition":
                self._extract_function(child, file_id)
            elif child.type == "struct_specifier":
                self._extract_struct(child, file_id)
            elif child.type == "class_specifier":
                self._extract_class(child, file_id)
            elif child.type == "preproc_include":
                self._extract_include(child, file_id)
            elif child.type == "template_declaration":
                self._extract_template(child, file_id)
            elif child.type == "enum_specifier":
                self._extract_enum(child, file_id)
            elif child.type == "type_definition":
                self._extract_typedef(child, file_id)
            elif child.type == "declaration" and self.language == "C":
                self._extract_c_declaration(child, file_id)

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

    def _collect_type_names(self, root: Node) -> None:
        for child in root.children:
            if child.type == "struct_specifier":
                name_node = self._find_child(child, "type_identifier")
                if name_node:
                    name = self._node_text(name_node)
                    qname = normalize_qualified_name(self.file_path, name)
                    self._type_ids[name] = make_symbol_id(self.file_path, qname)
            elif child.type == "class_specifier":
                name_node = self._find_child(child, "type_identifier")
                if name_node:
                    name = self._node_text(name_node)
                    qname = normalize_qualified_name(self.file_path, name)
                    self._type_ids[name] = make_symbol_id(self.file_path, qname)

    def _extract_function(self, node: Node, parent_id: str) -> None:
        declarator = self._find_child(node, "function_declarator")
        if not declarator:
            return
        name_node = self._find_child(declarator, "identifier")
        if not name_node:
            return
        name = self._node_text(name_node)
        symbol_id, qname = self._make_symbol(name)
        start_line, end_line = self._node_lines(node)

        self.symbols.append(GraphNode(
            id=symbol_id, name=name, qualified_name=qname,
            kind="function", file_path=self.file_path, language=self.language,
            start_line=start_line, end_line=end_line, parent_id=parent_id,
            signature=self._node_text(node)[:200],
        ))
        self.relationships.append({
            "source_id": parent_id, "target_id": symbol_id,
            "relationship": RelationshipType.CONTAINS.value,
            "confidence": ConfidenceLevel.EXACT.value,
            "source_lines": [start_line],
        })

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

        # Extract fields
        field_list = self._find_child(node, "field_declaration_list")
        if field_list:
            for child in field_list.children:
                if child.type == "field_declaration":
                    self._extract_field(child, symbol_id, name)

        self._type_ids[name] = symbol_id

    def _extract_class(self, node: Node, parent_id: str) -> None:
        name_node = self._find_child(node, "type_identifier")
        if not name_node:
            return
        name = self._node_text(name_node)
        symbol_id, qname = self._make_symbol(name)
        start_line, end_line = self._node_lines(node)

        # Determine bases
        bases = []
        for child in node.children:
            if child.type == "base_class_clause":
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

        # Extract class members
        field_list = self._find_child(node, "field_declaration_list")
        if field_list:
            self._extract_class_body(field_list, symbol_id, name)

        self._type_ids[name] = symbol_id

    def _extract_class_body(self, node: Node, class_id: str, class_name: str) -> None:
        for child in node.children:
            if child.type == "function_definition":
                self._extract_method(child, class_id, class_name)
            elif child.type == "field_declaration":
                self._extract_field(child, class_id, class_name)

    def _extract_method(self, node: Node, class_id: str, class_name: str) -> None:
        declarator = self._find_child(node, "function_declarator")
        if not declarator:
            return
        name_node = self._find_child(declarator, "identifier")
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

    def _extract_field(self, node: Node, parent_id: str, parent_name: str) -> None:
        for child in node.children:
            if child.type == "field_identifier":
                field_name = self._node_text(child)
                f_id, f_qname = self._make_symbol(field_name, [parent_name])
                fl, _ = self._node_lines(child)
                self.symbols.append(GraphNode(
                    id=f_id, name=field_name, qualified_name=f_qname,
                    kind="field", file_path=self.file_path, language=self.language,
                    start_line=fl, end_line=fl, parent_id=parent_id,
                ))
                self.relationships.append({
                    "source_id": parent_id, "target_id": f_id,
                    "relationship": RelationshipType.CONTAINS.value,
                    "confidence": ConfidenceLevel.EXACT.value, "source_lines": [fl],
                })

    def _extract_include(self, node: Node, parent_id: str) -> None:
        path_node = self._find_child(node, "system_lib_string")
        if not path_node:
            path_node = self._find_child(node, "string_literal")
        if not path_node:
            return
        path = self._node_text(path_node).strip('"').strip("<>")
        name = path.split("/")[-1].replace(".h", "")
        symbol_id = make_symbol_id(self.file_path, f"import:{path}")
        start_line, _ = self._node_lines(node)

        self.symbols.append(GraphNode(
            id=symbol_id, name=name, qualified_name=f"import:{path}",
            kind="import", file_path=self.file_path, language=self.language,
            start_line=start_line, end_line=start_line,
            metadata={"include_path": path},
        ))
        self.relationships.append({
            "source_id": parent_id, "target_id": symbol_id,
            "relationship": RelationshipType.IMPORTS.value,
            "confidence": ConfidenceLevel.EXACT.value, "source_lines": [start_line],
        })

    def _extract_template(self, node: Node, parent_id: str) -> None:
        for child in node.children:
            if child.type == "class_specifier":
                self._extract_class(child, parent_id)
            elif child.type == "function_definition":
                self._extract_function(child, parent_id)
            elif child.type == "struct_specifier":
                self._extract_struct(child, parent_id)

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

    def _extract_typedef(self, node: Node, parent_id: str) -> None:
        for child in node.children:
            if child.type == "type_identifier":
                name = self._node_text(child)
                symbol_id, qname = self._make_symbol(name)
                start_line, _ = self._node_lines(child)
                self.symbols.append(GraphNode(
                    id=symbol_id, name=name, qualified_name=qname,
                    kind="type", file_path=self.file_path, language=self.language,
                    start_line=start_line, end_line=start_line, parent_id=parent_id,
                ))

    def _extract_c_declaration(self, node: Node, parent_id: str) -> None:
        for child in node.children:
            if child.type == "function_declarator":
                name_node = self._find_child(child, "identifier")
                if name_node:
                    name = self._node_text(name_node)
                    symbol_id, qname = self._make_symbol(name)
                    sl, _ = self._node_lines(child)
                    self.symbols.append(GraphNode(
                        id=symbol_id, name=name, qualified_name=qname,
                        kind="function", file_path=self.file_path, language=self.language,
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
        return language.lower() in {"c", "c++", "cpp", "cxx", "c/c++"}
