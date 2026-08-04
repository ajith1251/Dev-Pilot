"""
Python code parser — uses Python's standard library AST module.

Extracts classes, functions, async functions, methods, imports,
and decorators. Pure static analysis — never imports target modules.
"""

from __future__ import annotations

import ast
from typing import Any, Dict, List, Optional, Set

from app.models.rag import CodeSymbol, SymbolKind
from app.rag.parsers.base import CodeParser, ParseResult


class PythonParser(CodeParser):
    """Parse Python source code using stdlib AST."""

    # Names to ignore (stdlib internals, dunder methods)
    IGNORE_NAMES: Set[str] = {"__init__", "__new__", "__str__", "__repr__"}

    def parse(self, file_path: str, content: str) -> ParseResult:
        """Parse Python source and extract symbols."""
        symbols: List[CodeSymbol] = []
        errors: List[str] = []

        if not content.strip():
            return ParseResult(
                file_path=file_path,
                language="Python",
                success=True,
            )

        try:
            tree = ast.parse(content, filename=file_path)
        except SyntaxError as exc:
            errors.append(f"Syntax error in {file_path}: {exc}")
            return ParseResult(
                file_path=file_path,
                language="Python",
                symbols=symbols,
                errors=errors,
                success=False,
            )
        except Exception as exc:
            errors.append(f"Parse error in {file_path}: {exc}")
            return ParseResult(
                file_path=file_path,
                language="Python",
                symbols=symbols,
                errors=errors,
                success=False,
            )

        # Extract top-level symbols and recurse into classes
        self._extract_from_body(
            body=tree.body,
            file_path=file_path,
            parent_names=None,
            symbols=symbols,
            content_lines=content.split("\n"),
        )

        # Also extract imports
        self._extract_imports(tree, file_path, symbols, content.split("\n"))

        return ParseResult(
            file_path=file_path,
            language="Python",
            symbols=symbols,
            errors=errors,
            success=len(errors) == 0,
        )

    def supports_language(self, language: str) -> bool:
        return language.lower() in {"python", "python3", "python2", "py"}

    def _extract_from_body(
        self,
        body: List[ast.stmt],
        file_path: str,
        parent_names: Optional[List[str]],
        symbols: List[CodeSymbol],
        content_lines: List[str],
    ) -> None:
        """Extract symbols from a block of statements."""
        for node in body:
            if isinstance(node, ast.ClassDef):
                self._extract_class(node, file_path, parent_names, symbols, content_lines)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._extract_function(node, file_path, parent_names, symbols, content_lines)

    def _extract_class(
        self,
        node: ast.ClassDef,
        file_path: str,
        parent_names: Optional[List[str]],
        symbols: List[CodeSymbol],
        content_lines: List[str],
    ) -> None:
        """Extract a class definition."""
        name = node.name
        start_line = node.lineno
        end_line = getattr(node, "end_lineno", start_line) or start_line

        parent_names_list = parent_names or []
        qualified_name = self.normalize_qualified_name(file_path, name, parent_names_list)
        symbol_id = self.make_symbol_id(file_path, qualified_name)

        # Extract decorators
        decorators: List[str] = []
        for dec in node.decorator_list:
            if isinstance(dec, ast.Name):
                decorators.append(dec.id)
            elif isinstance(dec, ast.Attribute):
                decorators.append(f"{dec.value}.{dec.attr}")

        # Get first docstring line
        docstring = ast.get_docstring(node)
        first_doc_line = docstring.split("\n")[0] if docstring else None

        # Signature
        bases = []
        for base in node.bases:
            if isinstance(base, ast.Name):
                bases.append(base.id)
            elif isinstance(base, ast.Attribute):
                try:
                    bases.append(f"{base.value.id}.{base.attr}")
                except AttributeError:
                    bases.append(base.attr)
        signature = f"class {name}({', '.join(bases)})" if bases else f"class {name}:"

        symbol = CodeSymbol(
            id=symbol_id,
            name=name,
            qualified_name=qualified_name,
            kind=SymbolKind.CLASS,
            file_path=file_path,
            language="Python",
            start_line=start_line,
            end_line=end_line,
            parent_symbol=None if not parent_names else None,  # Link later
            signature=signature,
            docstring=first_doc_line,
            metadata={
                "decorators": decorators,
                "bases": bases,
                "methods_count": sum(
                    1 for item in node.body
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                ),
            },
        )
        symbols.append(symbol)

        # Extract methods
        child_parents = parent_names_list + [name]
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._extract_method(item, file_path, child_parents, symbols, content_lines, symbol_id)

    def _extract_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        file_path: str,
        parent_names: Optional[List[str]],
        symbols: List[CodeSymbol],
        content_lines: List[str],
    ) -> None:
        """Extract a top-level function."""
        is_async = isinstance(node, ast.AsyncFunctionDef)
        name = node.name
        start_line = node.lineno
        end_line = getattr(node, "end_lineno", start_line) or start_line

        parent_names_list = parent_names or []
        qualified_name = self.normalize_qualified_name(file_path, name, parent_names_list)
        symbol_id = self.make_symbol_id(file_path, qualified_name)

        # Get decorators
        decorators: List[str] = []
        for dec in node.decorator_list:
            if isinstance(dec, ast.Name):
                decorators.append(dec.id)

        # Docstring
        docstring = ast.get_docstring(node)
        first_doc_line = docstring.split("\n")[0] if docstring else None

        # Signature
        args_str = ", ".join(
            arg.arg for arg in node.args.args[:5]  # First 5 args
        )
        if len(node.args.args) > 5:
            args_str += ", ..."
        prefix = "async def" if is_async else "def"
        signature = f"{prefix} {name}({args_str})"

        symbol = CodeSymbol(
            id=symbol_id,
            name=name,
            qualified_name=qualified_name,
            kind=SymbolKind.ASYNC_FUNCTION if is_async else SymbolKind.FUNCTION,
            file_path=file_path,
            language="Python",
            start_line=start_line,
            end_line=end_line,
            parent_symbol=None if not parent_names else None,
            signature=signature,
            docstring=first_doc_line,
            metadata={
                "decorators": decorators,
                "args_count": len(node.args.args),
            },
        )
        symbols.append(symbol)

    def _extract_method(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        file_path: str,
        parent_names: List[str],
        symbols: List[CodeSymbol],
        content_lines: List[str],
        class_symbol_id: str,
    ) -> None:
        """Extract a method within a class."""
        is_async = isinstance(node, ast.AsyncFunctionDef)
        name = node.name
        start_line = node.lineno
        end_line = getattr(node, "end_lineno", start_line) or start_line

        qualified_name = self.normalize_qualified_name(file_path, name, parent_names)
        symbol_id = self.make_symbol_id(file_path, qualified_name)

        decorators: List[str] = []
        for dec in node.decorator_list:
            if isinstance(dec, ast.Name):
                decorators.append(dec.id)
            elif isinstance(dec, ast.Attribute):
                try:
                    decorators.append(f"{dec.value.id}.{dec.attr}")
                except AttributeError:
                    decorators.append(dec.attr)

        docstring = ast.get_docstring(node)
        first_doc_line = docstring.split("\n")[0] if docstring else None

        args_str = ", ".join(
            arg.arg for arg in node.args.args[:5]
        )
        if len(node.args.args) > 5:
            args_str += ", ..."
        prefix = "async def" if is_async else "def"
        signature = f"{prefix} {name}({args_str})"

        symbol = CodeSymbol(
            id=symbol_id,
            name=name,
            qualified_name=qualified_name,
            kind=SymbolKind.ASYNC_METHOD if is_async else SymbolKind.METHOD,
            file_path=file_path,
            language="Python",
            start_line=start_line,
            end_line=end_line,
            parent_symbol=class_symbol_id,
            signature=signature,
            docstring=first_doc_line,
            metadata={
                "decorators": decorators,
                "args_count": len(node.args.args),
                "class_name": parent_names[-1] if parent_names else "",
            },
        )
        symbols.append(symbol)

    def _extract_imports(
        self,
        tree: ast.Module,
        file_path: str,
        symbols: List[CodeSymbol],
        content_lines: List[str],
    ) -> None:
        """Extract import statements."""
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name
                    asname = alias.asname
                    as_str = f" as {asname}" if asname else ""
                    qualified_name = self.normalize_qualified_name(file_path, name, ["imports"])
                    symbol_id = self.make_symbol_id(file_path, qualified_name)
                    symbols.append(CodeSymbol(
                        id=symbol_id,
                        name=name,
                        qualified_name=qualified_name,
                        kind=SymbolKind.IMPORT,
                        file_path=file_path,
                        language="Python",
                        start_line=node.lineno,
                        end_line=node.lineno,
                        signature=f"import {name}{as_str}",
                    ))
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                level = node.level or 0
                prefix = "." * level
                for alias in node.names:
                    name = alias.name
                    asname = alias.asname
                    as_str = f" as {asname}" if asname else ""
                    full_name = f"{prefix}{module}.{name}" if module else f"{prefix}{name}"
                    qualified_name = self.normalize_qualified_name(file_path, full_name, ["imports"])
                    symbol_id = self.make_symbol_id(file_path, qualified_name)
                    symbols.append(CodeSymbol(
                        id=symbol_id,
                        name=full_name,
                        qualified_name=qualified_name,
                        kind=SymbolKind.IMPORT,
                        file_path=file_path,
                        language="Python",
                        start_line=node.lineno,
                        end_line=node.lineno,
                        signature=f"from {'.' * level}{module} import {name}{as_str}",
                    ))
