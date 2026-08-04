"""
Java symbol parser — extracts symbols and relationships using tree-sitter.

Extracts:
- Packages
- Imports
- Classes (with extends/implements, generics)
- Interfaces
- Enums
- Methods / constructors
- Fields (constants)
- Annotations/decorators
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
        import tree_sitter_java as tsjava  # type: ignore[import-untyped]

        lang = Language(tsjava.language())
        _parser = Parser(lang)
        return _parser
    except Exception:
        _TREE_SITTER_AVAILABLE = False
        return None


class JavaSymbolParser:
    """Parse Java source and extract structured symbols with relationships."""

    def __init__(self, file_path: str, content: str) -> None:
        self.file_path = file_path
        self.content = content
        self.bytes_content = content.encode("utf-8") if isinstance(content, str) else content
        self.lines = content.split("\n")
        self.language = "Java"

        self.symbols: List[GraphNode] = []
        self.relationships: List[dict] = []
        self.diagnostics: List[str] = []

        # Tracking
        self._package_name: str = ""
        self._current_class_id: Optional[str] = None

    def parse(self) -> Tuple[List[GraphNode], List[dict], List[str]]:
        """Parse the file and extract symbols + relationships."""
        if not self.content.strip():
            return [], [], []

        parser = _get_parser()
        if parser is None:
            self.diagnostics.append(
                "Java parser unavailable: tree-sitter-java package not installed. "
                "Install with: pip install tree-sitter tree-sitter-java"
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

        # Create file/module node
        file_id = self._create_file_node()

        # Extract top-level declarations
        self._extract_children(root, file_id)

        return self.symbols, self.relationships, self.diagnostics

    def _create_file_node(self) -> str:
        """Create a file-level graph node and return its ID."""
        module_name = self.file_path.replace("/", ".").rsplit(".", 1)[0]
        module_name = module_name.replace("\\", ".")
        if self._package_name:
            module_name = f"{self._package_name}.{module_name.rsplit('.', 1)[-1]}"

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
        """Extract the package declaration."""
        for child in root.children:
            if child.type == "package_declaration":
                for sub in child.children:
                    if sub.type == "scoped_identifier":
                        self._package_name = self._node_text(sub)

    # ── Top-level Extraction ──────────────────────────────────

    def _extract_children(self, node: Node, parent_id: str) -> None:
        """Walk direct children and dispatch to type-specific extractors."""
        for child in node.children:
            if not child.is_named:
                continue

            if child.type == "class_declaration":
                self._extract_class(child, parent_id)
            elif child.type == "interface_declaration":
                self._extract_interface(child, parent_id)
            elif child.type == "enum_declaration":
                self._extract_enum(child, parent_id)
            elif child.type == "import_declaration":
                self._extract_import(child, parent_id)
            elif child.type == "annotation_type_declaration":
                self._extract_annotation_type(child, parent_id)
            elif child.type == "record_declaration":
                self._extract_record(child, parent_id)

    # ── Class Extraction ──────────────────────────────────────

    def _extract_class(self, node: Node, parent_id: str) -> None:
        """Extract a class declaration."""
        name_node = self._find_child(node, "identifier")
        if not name_node:
            return
        name = self._node_text(name_node)

        # Determine visibility / abstract / static
        modifiers = self._extract_modifiers(node)
        bases = self._extract_bases(node)

        symbol_id, qname = self._make_symbol(name)
        start_line, end_line = self._node_lines(node)

        meta: Dict[str, Any] = {
            "modifiers": modifiers,
            "extends": bases.get("extends"),
            "implements": bases.get("implements"),
            "is_abstract": "abstract" in modifiers,
        }

        self.symbols.append(GraphNode(
            id=symbol_id,
            name=name,
            qualified_name=qname,
            kind="class",
            file_path=self.file_path,
            language=self.language,
            start_line=start_line,
            end_line=end_line,
            parent_id=parent_id,
            signature=self._node_text(node)[:200],
            metadata=meta,
        ))

        self.relationships.append({
            "source_id": parent_id,
            "target_id": symbol_id,
            "relationship": RelationshipType.CONTAINS.value,
            "confidence": ConfidenceLevel.EXACT.value,
            "source_lines": [start_line],
            "resolution_detail": f"class {name} in parent",
        })

        # Inheritance links
        for base_type, base_list in bases.items():
            for base in base_list:
                self.relationships.append({
                    "source_id": symbol_id,
                    "target_id": f"__external__::{base}",
                    "relationship": RelationshipType.INHERITS.value,
                    "confidence": ConfidenceLevel.HIGH.value,
                    "source_lines": [start_line],
                    "resolution_detail": f"class {name} {base_type} {base}",
                })

        # Extract members from class body
        body = self._find_child(node, "class_body")
        if body:
            self._extract_class_body(body, symbol_id, name)

    def _extract_interface(self, node: Node, parent_id: str) -> None:
        """Extract an interface declaration."""
        name_node = self._find_child(node, "identifier")
        if not name_node:
            return
        name = self._node_text(name_node)
        modifiers = self._extract_modifiers(node)

        # Interface extends
        extends_list: List[str] = []
        for child in node.children:
            if child.type == "extends_interfaces":
                for ext in child.children:
                    if ext.type in ("type_list", "scoped_identifier", "identifier"):
                        extends_list.append(self._node_text(ext))

        symbol_id, qname = self._make_symbol(name)
        start_line, end_line = self._node_lines(node)

        self.symbols.append(GraphNode(
            id=symbol_id,
            name=name,
            qualified_name=qname,
            kind="interface",
            file_path=self.file_path,
            language=self.language,
            start_line=start_line,
            end_line=end_line,
            parent_id=parent_id,
            signature=self._node_text(node)[:200],
            metadata={"modifiers": modifiers, "extends": extends_list},
        ))

        self.relationships.append({
            "source_id": parent_id,
            "target_id": symbol_id,
            "relationship": RelationshipType.CONTAINS.value,
            "confidence": ConfidenceLevel.EXACT.value,
            "source_lines": [start_line],
        })

        for ext in extends_list:
            self.relationships.append({
                "source_id": symbol_id,
                "target_id": f"__external__::{ext}",
                "relationship": RelationshipType.INHERITS.value,
                "confidence": ConfidenceLevel.HIGH.value,
                "source_lines": [start_line],
            })

        # Extract methods from interface body (tree-sitter uses method_declaration type)
        body = self._find_child(node, "interface_body")
        if body:
            for child in body.children:
                if child.type == "method_declaration":
                    self._extract_interface_method(child, symbol_id, name)

    def _extract_enum(self, node: Node, parent_id: str) -> None:
        """Extract an enum declaration."""
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
            kind="enum",
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

    def _extract_record(self, node: Node, parent_id: str) -> None:
        """Extract a Java record declaration (Java 16+)."""
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
            kind="record",
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

    def _extract_annotation_type(self, node: Node, parent_id: str) -> None:
        """Extract an annotation type declaration."""
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
            kind="annotation",
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

    # ── Class Body Extraction ─────────────────────────────────

    def _extract_class_body(self, body: Node, class_id: str, class_name: str) -> None:
        """Extract methods and fields from a class body."""
        for child in body.children:
            if not child.is_named:
                continue

            if child.type == "method_declaration":
                self._extract_method(child, class_id, class_name)
            elif child.type == "constructor_declaration":
                self._extract_constructor(child, class_id, class_name)
            elif child.type == "field_declaration":
                self._extract_field(child, class_id, class_name)
            elif child.type in ("class_declaration", "interface_declaration", "enum_declaration"):
                self._extract_class(child, class_id)
            elif child.type == "annotation_type_declaration":
                self._extract_annotation_type(child, class_id)

    def _extract_interface_method(self, node: Node, iface_id: str, iface_name: str) -> None:
        """Extract a method signature from an interface body."""
        name_node = self._find_child(node, "identifier")
        if not name_node:
            return
        name = self._node_text(name_node)

        symbol_id, qname = self._make_symbol(name, [iface_name])
        start_line, end_line = self._node_lines(node)

        self.symbols.append(GraphNode(
            id=symbol_id,
            name=name,
            qualified_name=qname,
            kind="abstract_method",
            file_path=self.file_path,
            language=self.language,
            start_line=start_line,
            end_line=end_line,
            parent_id=iface_id,
            signature=self._node_text(node)[:200],
        ))

        self.relationships.append({
            "source_id": iface_id,
            "target_id": symbol_id,
            "relationship": RelationshipType.CONTAINS.value,
            "confidence": ConfidenceLevel.EXACT.value,
            "source_lines": [start_line],
            "resolution_detail": f"method {name} in interface {iface_name}",
        })

    def _extract_method(self, node: Node, class_id: str, class_name: str) -> None:
        """Extract a method declaration."""
        name_node = self._find_child(node, "identifier")
        if not name_node:
            return
        name = self._node_text(name_node)
        modifiers = self._extract_modifiers(node)

        symbol_id, qname = self._make_symbol(name, [class_name])
        start_line, end_line = self._node_lines(node)

        is_abstract = "abstract" in modifiers
        is_static = "static" in modifiers
        is_public = "public" in modifiers

        self.symbols.append(GraphNode(
            id=symbol_id,
            name=name,
            qualified_name=qname,
            kind="method" if not is_abstract else "abstract_method",
            file_path=self.file_path,
            language=self.language,
            start_line=start_line,
            end_line=end_line,
            parent_id=class_id,
            signature=self._node_text(node)[:200],
            metadata={
                "modifiers": modifiers,
                "is_static": is_static,
                "is_public": is_public,
                "is_abstract": is_abstract,
            },
        ))

        self.relationships.append({
            "source_id": class_id,
            "target_id": symbol_id,
            "relationship": RelationshipType.CONTAINS.value,
            "confidence": ConfidenceLevel.EXACT.value,
            "source_lines": [start_line],
            "resolution_detail": f"method {name} in {class_name}",
        })

    def _extract_constructor(self, node: Node, class_id: str, class_name: str) -> None:
        """Extract a constructor declaration."""
        name_node = self._find_child(node, "identifier")
        if not name_node:
            return
        name = self._node_text(name_node)
        symbol_id, qname = self._make_symbol(name, [class_name])
        start_line, end_line = self._node_lines(node)

        self.symbols.append(GraphNode(
            id=symbol_id,
            name=name,
            qualified_name=qname,
            kind="constructor",
            file_path=self.file_path,
            language=self.language,
            start_line=start_line,
            end_line=end_line,
            parent_id=class_id,
            signature=self._node_text(node)[:200],
        ))

        self.relationships.append({
            "source_id": class_id,
            "target_id": symbol_id,
            "relationship": RelationshipType.CONTAINS.value,
            "confidence": ConfidenceLevel.EXACT.value,
            "source_lines": [start_line],
        })

    def _extract_field(self, node: Node, class_id: str, class_name: str) -> None:
        """Extract a field declaration (track constants / static fields)."""
        modifiers = self._extract_modifiers(node)
        type_node = self._find_child(node, "type_identifier")
        type_name = self._node_text(type_node) if type_node else ""

        for child in node.children:
            if child.type == "variable_declarator":
                var_name_node = self._find_child(child, "identifier")
                if not var_name_node:
                    continue
                field_name = self._node_text(var_name_node)
                symbol_id, qname = self._make_symbol(field_name, [class_name])
                start_line, end_line = self._node_lines(child)

                is_const = "final" in modifiers or ("static" in modifiers and "final" in modifiers)
                self.symbols.append(GraphNode(
                    id=symbol_id,
                    name=field_name,
                    qualified_name=qname,
                    kind="constant" if is_const else "field",
                    file_path=self.file_path,
                    language=self.language,
                    start_line=start_line,
                    end_line=end_line,
                    parent_id=class_id,
                    signature=f"{' '.join(modifiers)} {type_name} {field_name}" if type_name else field_name,
                    metadata={"modifiers": modifiers, "type": type_name, "is_constant": is_const},
                ))

                self.relationships.append({
                    "source_id": class_id,
                    "target_id": symbol_id,
                    "relationship": RelationshipType.CONTAINS.value,
                    "confidence": ConfidenceLevel.EXACT.value,
                    "source_lines": [start_line],
                })

    # ── Import Extraction ─────────────────────────────────────

    def _extract_import(self, node: Node, parent_id: str) -> None:
        """Extract an import declaration."""
        scoped = self._find_child(node, "scoped_identifier")
        if not scoped:
            return
        import_path = self._node_text(scoped)
        is_static = "static" in node.text.decode("utf-8")

        symbol_id = make_symbol_id(self.file_path, f"import:{import_path}")
        name = import_path.rsplit(".", 1)[-1]

        self.symbols.append(GraphNode(
            id=symbol_id,
            name=name,
            qualified_name=f"import:{import_path}",
            kind="import",
            file_path=self.file_path,
            language=self.language,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            metadata={"import_path": import_path, "is_static": is_static},
        ))

        self.relationships.append({
            "source_id": parent_id,
            "target_id": symbol_id,
            "relationship": RelationshipType.IMPORTS.value,
            "confidence": ConfidenceLevel.EXACT.value,
            "source_lines": [node.start_point[0] + 1],
            "resolution_detail": f"import {import_path}",
        })

    # ── Helpers ───────────────────────────────────────────────

    def _make_symbol(self, name: str, parent_names: Optional[List[str]] = None) -> Tuple[str, str]:
        """Create a deterministic symbol ID and qualified name."""
        qname = normalize_qualified_name(self.file_path, name, parent_names)
        symbol_id = make_symbol_id(self.file_path, qname)
        return symbol_id, qname

    def _node_text(self, node: Node) -> str:
        """Get text content of a node."""
        return self.bytes_content[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

    def _node_lines(self, node: Node) -> Tuple[int, int]:
        return node.start_point[0] + 1, node.end_point[0] + 1

    def _find_child(self, node: Node, child_type: str) -> Optional[Node]:
        """Find first direct child of the given type."""
        for child in node.children:
            if child.type == child_type:
                return child
        return None

    def _extract_modifiers(self, node: Node) -> List[str]:
        """Extract modifier keywords from a declaration."""
        modifiers = []
        for child in node.children:
            if child.is_named and child.type == "modifiers":
                for mod in child.children:
                    if mod.is_named or mod.type in ("public", "private", "protected", "static",
                                                    "final", "abstract", "synchronized", "native"):
                        modifiers.append(self._node_text(mod))
        return modifiers

    def _extract_bases(self, node: Node) -> Dict[str, List[str]]:
        """Extract extends and implements bases."""
        bases: Dict[str, List[str]] = {}
        for child in node.children:
            if child.type == "superclass":
                for sub in child.children:
                    if sub.type in ("type_identifier", "scoped_identifier"):
                        bases.setdefault("extends", []).append(self._node_text(sub))
            elif child.type == "super_interfaces":
                for sub in child.children:
                    if sub.type in ("type_list", "type_identifier", "scoped_identifier"):
                        for type_node in sub.children if sub.children else [sub]:
                            if type_node.type in ("type_identifier", "scoped_identifier", "identifier"):
                                bases.setdefault("implements", []).append(self._node_text(type_node))
        return bases

    def supports_language(self, language: str) -> bool:
        return language.lower() in {"java", "java17", "java21", "openjdk"}
