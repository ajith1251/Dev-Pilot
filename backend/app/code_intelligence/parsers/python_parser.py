"""
Python symbol parser — extracts symbols and relationships using stdlib AST.

Extends the Phase 5 PythonParser with:
- Relationship extraction (calls, inheritance, imports)
- Export detection
- Decorator tracking
- Comprehensive metadata
"""

from __future__ import annotations

import ast
from typing import Any, Dict, List, Optional, Set, Tuple

from app.code_intelligence.semantic_graph import (
    ConfidenceLevel,
    GraphNode,
    RelationshipType,
    make_symbol_id,
    normalize_qualified_name,
)


class PythonSymbolParser:
    """Parse Python source and extract structured symbols with relationships.

    Pure static analysis — never imports or executes target modules.
    """

    def __init__(self, file_path: str, content: str) -> None:
        self.file_path = file_path
        self.content = content
        self.lines = content.split("\n")
        self.tree: Optional[ast.Module] = None
        self.symbols: List[GraphNode] = []
        self.relationships: List[dict] = []
        self.diagnostics: List[str] = []

    def parse(self) -> Tuple[List[GraphNode], List[dict], List[str]]:
        """Parse the file and extract symbols + relationships.

        Returns:
            Tuple of (symbols, relationships, diagnostics)
        """
        if not self.content.strip():
            return [], [], []

        try:
            self.tree = ast.parse(self.content, filename=self.file_path)
        except SyntaxError as exc:
            self.diagnostics.append(f"Syntax error: {exc}")
            return [], [], self.diagnostics
        except Exception as exc:
            self.diagnostics.append(f"Parse error: {exc}")
            return [], [], self.diagnostics

        # Extract module-level docstring
        module_doc = ast.get_docstring(self.tree)
        module_name = self.file_path.replace("/", ".").rsplit(".", 1)[0]
        module_name = module_name.replace("\\", ".")

        # Create module node
        mod_id = make_symbol_id(self.file_path, module_name)
        self.symbols.append(GraphNode(
            id=mod_id,
            name=self.file_path.split("/")[-1] or self.file_path,
            qualified_name=module_name,
            kind="module",
            file_path=self.file_path,
            language="Python",
            start_line=1,
            end_line=len(self.lines),
            docstring=module_doc.split("\n")[0] if module_doc else None,
        ))

        # Extract top-level symbols
        self._extract_body(self.tree.body, parent_names=None)

        # Extract imports
        self._extract_imports()

        # Extract calls and references
        self._extract_calls()

        return self.symbols, self.relationships, self.diagnostics

    def _extract_body(
        self,
        body: List[ast.stmt],
        parent_names: Optional[List[str]] = None,
        parent_id: Optional[str] = None,
    ) -> None:
        """Extract symbols from a block of statements."""
        for node in body:
            if isinstance(node, ast.ClassDef):
                self._extract_class(node, parent_names, parent_id)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._extract_function(node, parent_names, parent_id)
            elif isinstance(node, ast.Assign):
                self._extract_assignments(node, parent_names, parent_id)

    def _extract_class(
        self,
        node: ast.ClassDef,
        parent_names: Optional[List[str]] = None,
        parent_id: Optional[str] = None,
    ) -> None:
        """Extract a class definition and its methods."""
        name = node.name
        pnames = parent_names or []
        qualified_name = normalize_qualified_name(self.file_path, name, pnames)
        symbol_id = make_symbol_id(self.file_path, qualified_name)

        start_line = node.lineno
        end_line = getattr(node, "end_lineno", start_line) or start_line

        # Decorators
        decorators = self._extract_decorators(node.decorator_list)

        # Bases (inheritance)
        bases = []
        for base in node.bases:
            base_name = self._ast_name_to_string(base)
            if base_name:
                bases.append(base_name)

        # Docstring
        doc = ast.get_docstring(node)
        first_doc = doc.split("\n")[0] if doc else None

        # Signature
        base_str = f"({', '.join(bases)})" if bases else ":"
        signature = f"class {name}{base_str}"

        # Metadata
        meta: Dict[str, Any] = {
            "decorators": decorators,
            "bases": bases,
            "methods_count": sum(
                1 for item in node.body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            ),
            "is_exception": any(
                b in bases for b in ("Exception", "BaseException", "ValueError", "TypeError", "RuntimeError")
            ),
            "is_abstract": "ABC" in bases or any("abstract" in d for d in decorators),
        }

        node_obj = GraphNode(
            id=symbol_id,
            name=name,
            qualified_name=qualified_name,
            kind="class",
            file_path=self.file_path,
            language="Python",
            start_line=start_line,
            end_line=end_line,
            parent_id=parent_id,
            signature=signature,
            docstring=first_doc,
            metadata=meta,
        )
        self.symbols.append(node_obj)

        # Parent relationship
        if parent_id:
            self.relationships.append({
                "source_id": parent_id,
                "target_id": symbol_id,
                "relationship": RelationshipType.CONTAINS.value,
                "confidence": ConfidenceLevel.EXACT.value,
                "source_lines": [start_line],
                "resolution_detail": "class definition inside parent",
            })

        # Inheritance relationships
        for base_name in bases:
            # Add relationship to base class (may be unresolved)
            self.relationships.append({
                "source_id": symbol_id,
                "target_id": f"__external__::{base_name}",
                "relationship": RelationshipType.INHERITS.value,
                "confidence": ConfidenceLevel.HIGH.value,
                "source_lines": [start_line],
                "resolution_detail": f"class {name} inherits {base_name}",
            })

        # Extract methods
        child_parents = pnames + [name]
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._extract_method(item, child_parents, symbol_id, decorators)
            elif isinstance(item, ast.ClassDef):
                self._extract_class(item, child_parents, symbol_id)

    def _extract_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        parent_names: Optional[List[str]] = None,
        parent_id: Optional[str] = None,
    ) -> None:
        """Extract a top-level function."""
        name = node.name
        is_async = isinstance(node, ast.AsyncFunctionDef)
        pnames = parent_names or []
        qualified_name = normalize_qualified_name(self.file_path, name, pnames)
        symbol_id = make_symbol_id(self.file_path, qualified_name)

        start_line = node.lineno
        end_line = getattr(node, "end_lineno", start_line) or start_line

        decorators = self._extract_decorators(node.decorator_list)
        doc = ast.get_docstring(node)
        first_doc = doc.split("\n")[0] if doc else None

        # Signature
        args = [arg.arg for arg in node.args.args[:8]]
        if len(node.args.args) > 8:
            args.append("...")
        prefix = "async def" if is_async else "def"
        signature = f"{prefix} {name}({', '.join(args)})"

        node_obj = GraphNode(
            id=symbol_id,
            name=name,
            qualified_name=qualified_name,
            kind="async_function" if is_async else "function",
            file_path=self.file_path,
            language="Python",
            start_line=start_line,
            end_line=end_line,
            parent_id=parent_id,
            signature=signature,
            docstring=first_doc,
            metadata={
                "decorators": decorators,
                "args_count": len(node.args.args),
                "is_async": is_async,
            },
        )
        self.symbols.append(node_obj)

        if parent_id:
            self.relationships.append({
                "source_id": parent_id,
                "target_id": symbol_id,
                "relationship": RelationshipType.CONTAINS.value,
                "confidence": ConfidenceLevel.EXACT.value,
                "source_lines": [start_line],
                "resolution_detail": "function definition inside parent",
            })

        # Extract inner classes/functions
        self._extract_inner(node, pnames + [name], symbol_id)

    def _extract_method(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        parent_names: List[str],
        class_symbol_id: str,
        class_decorators: Optional[List[str]] = None,
    ) -> None:
        """Extract a method within a class."""
        name = node.name
        is_async = isinstance(node, ast.AsyncFunctionDef)
        qualified_name = normalize_qualified_name(self.file_path, name, parent_names)
        symbol_id = make_symbol_id(self.file_path, qualified_name)

        start_line = node.lineno
        end_line = getattr(node, "end_lineno", start_line) or start_line

        decorators = self._extract_decorators(node.decorator_list)
        doc = ast.get_docstring(node)
        first_doc = doc.split("\n")[0] if doc else None

        args = [arg.arg for arg in node.args.args[:8]]
        if len(node.args.args) > 8:
            args.append("...")
        prefix = "async def" if is_async else "def"
        signature = f"{prefix} {name}({', '.join(args)})"

        # Determine if this is a special method
        is_magic = name.startswith("__") and name.endswith("__")
        is_property = "property" in decorators
        is_static = "staticmethod" in decorators
        is_classmethod = "classmethod" in decorators

        method_kind = "method"
        if is_async:
            method_kind = "async_method"
        if is_magic:
            method_kind = "magic_method"
        if is_property:
            method_kind = "property"

        node_obj = GraphNode(
            id=symbol_id,
            name=name,
            qualified_name=qualified_name,
            kind=method_kind,
            file_path=self.file_path,
            language="Python",
            start_line=start_line,
            end_line=end_line,
            parent_id=class_symbol_id,
            signature=signature,
            docstring=first_doc,
            metadata={
                "decorators": decorators,
                "args_count": len(node.args.args),
                "is_async": is_async,
                "is_magic": is_magic,
                "is_property": is_property,
                "is_static": is_static,
                "is_classmethod": is_classmethod,
            },
        )
        self.symbols.append(node_obj)

        # CONTAINS relationship from class
        self.relationships.append({
            "source_id": class_symbol_id,
            "target_id": symbol_id,
            "relationship": RelationshipType.CONTAINS.value,
            "confidence": ConfidenceLevel.EXACT.value,
            "source_lines": [start_line],
            "resolution_detail": f"method {name} in class",
        })

    def _extract_assignments(
        self,
        node: ast.Assign,
        parent_names: Optional[List[str]] = None,
        parent_id: Optional[str] = None,
    ) -> None:
        """Extract significant assignments (constants, exports)."""
        for target in node.targets:
            if isinstance(target, ast.Name):
                name = target.id
                # Only track all-caps constants and __all__
                if name.isupper() or name == "__all__":
                    pnames = parent_names or []
                    qualified_name = normalize_qualified_name(self.file_path, name, pnames)
                    symbol_id = make_symbol_id(self.file_path, qualified_name)
                    # Determine constant type for metadata (never store raw values to avoid secrets leakage)
                    const_type = "unknown"
                    if isinstance(node.value, ast.Constant):
                        if isinstance(node.value.value, str):
                            const_type = "string"
                        elif isinstance(node.value.value, (int, float)):
                            const_type = "numeric"
                        elif isinstance(node.value.value, bool):
                            const_type = "boolean"
                        elif node.value.value is None:
                            const_type = "none"
                    elif isinstance(node.value, (ast.List, ast.Tuple)):
                        const_type = "collection"
                    elif isinstance(node.value, ast.Dict):
                        const_type = "dict"
                    self.symbols.append(GraphNode(
                        id=symbol_id,
                        name=name,
                        qualified_name=qualified_name,
                        kind="constant" if name.isupper() else "variable",
                        file_path=self.file_path,
                        language="Python",
                        start_line=node.lineno,
                        end_line=getattr(node, "end_lineno", node.lineno) or node.lineno,
                        parent_id=parent_id,
                        metadata={"const_type": const_type},
                    ))

    def _extract_imports(self) -> None:
        """Extract import statements as symbols and IMPORTS relationships."""
        if not self.tree:
            return

        module_id = self.symbols[0].id if self.symbols else ""

        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imp_name = alias.name
                    imp_as = alias.asname or imp_name
                    qualified_name = normalize_qualified_name(self.file_path, imp_name, ["imports"])
                    symbol_id = make_symbol_id(self.file_path, qualified_name)

                    self.symbols.append(GraphNode(
                        id=symbol_id,
                        name=imp_as,
                        qualified_name=qualified_name,
                        kind="import",
                        file_path=self.file_path,
                        language="Python",
                        start_line=node.lineno,
                        end_line=node.lineno,
                        signature=f"import {imp_name}",
                        metadata={"as_name": alias.asname},
                    ))

                    # IMPORTS relationship
                    self.relationships.append({
                        "source_id": module_id,
                        "target_id": symbol_id,
                        "relationship": RelationshipType.IMPORTS.value,
                        "confidence": ConfidenceLevel.EXACT.value,
                        "source_lines": [node.lineno],
                        "resolution_detail": f"import {imp_name}",
                    })

            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                level = node.level or 0
                prefix = "." * level
                for alias in node.names:
                    name = alias.name
                    full_name = f"{prefix}{module}.{name}" if module else f"{prefix}{name}"
                    qualified_name = normalize_qualified_name(self.file_path, full_name, ["imports"])
                    symbol_id = make_symbol_id(self.file_path, qualified_name)

                    self.symbols.append(GraphNode(
                        id=symbol_id,
                        name=alias.asname or name,
                        qualified_name=qualified_name,
                        kind="import",
                        file_path=self.file_path,
                        language="Python",
                        start_line=node.lineno,
                        end_line=node.lineno,
                        signature=f"from {'.' * level}{module} import {name}",
                        metadata={"module": module, "name": name, "as_name": alias.asname},
                    ))

                    # IMPORTS relationship
                    self.relationships.append({
                        "source_id": module_id,
                        "target_id": symbol_id,
                        "relationship": RelationshipType.IMPORTS.value,
                        "confidence": ConfidenceLevel.EXACT.value,
                        "source_lines": [node.lineno],
                        "resolution_detail": f"from {module} import {name}",
                    })

    def _extract_calls(self) -> None:
        """Extract function/method call relationships."""
        if not self.tree:
            return

        # Build symbol lookup: name -> node_id
        name_to_id: Dict[str, str] = {}
        for sym in self.symbols:
            name_to_id[sym.name] = sym.id

        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call):
                call_name = self._ast_name_to_string(node.func)
                if call_name:
                    # Find the calling function
                    caller_id = self._find_enclosing_symbol(node.lineno)
                    if caller_id:
                        target_id = name_to_id.get(call_name, f"__external__::{call_name}")
                        self.relationships.append({
                            "source_id": caller_id,
                            "target_id": target_id,
                            "relationship": RelationshipType.CALLS.value,
                            "confidence": ConfidenceLevel.MEDIUM if target_id.startswith("__external__") else ConfidenceLevel.HIGH,
                            "source_lines": [node.lineno],
                            "resolution_detail": f"calls {call_name}",
                        })

    def _extract_inner(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        parent_names: List[str],
        parent_id: str,
    ) -> None:
        """Extract inner classes/functions within a function body."""
        for item in node.body:
            if isinstance(item, ast.ClassDef):
                self._extract_class(item, parent_names, parent_id)

    def _extract_decorators(self, decorator_list: List[ast.expr]) -> List[str]:
        """Extract decorator names from AST nodes."""
        decorators = []
        for dec in decorator_list:
            name = self._ast_name_to_string(dec)
            if name:
                decorators.append(name)
        return decorators

    def _ast_name_to_string(self, node: ast.expr) -> Optional[str]:
        """Convert an AST expression to a string name."""
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            try:
                base = self._ast_name_to_string(node.value)
                if base:
                    return f"{base}.{node.attr}"
                return node.attr
            except Exception:
                return node.attr
        elif isinstance(node, ast.Subscript):
            try:
                value = self._ast_name_to_string(node.value)
                return value
            except Exception:
                return None
        elif isinstance(node, ast.Call):
            return self._ast_name_to_string(node.func)
        elif isinstance(node, ast.Constant):
            if isinstance(node.value, str):
                return node.value
        return None

    def _find_enclosing_symbol(self, line_number: int) -> Optional[str]:
        """Find the symbol ID of the enclosing function/class at a given line."""
        candidates = []
        for sym in self.symbols:
            if sym.kind in ("function", "async_function", "method", "async_method", "class"):
                if sym.start_line <= line_number <= sym.end_line:
                    candidates.append((sym.end_line - sym.start_line, sym.id))
        if candidates:
            # Return the most deeply nested (smallest range)
            candidates.sort()
            return candidates[0][1]
        return None

    def supports_language(self, language: str) -> bool:
        return language.lower() in {"python", "python3", "py"}
