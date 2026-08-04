"""
TypeScript/JavaScript symbol parser — extracts symbols and relationships.

Uses robust regex + brace-matching for structural parsing.
Compatible with the Python/FastAPI architecture (no native extensions needed).

Extracts:
- Classes (with extends/implements)
- Functions (async, regular, arrow assigned to const)
- Methods
- Interfaces
- Type aliases
- Enums
- Imports (named, default, namespace, re-exports)
- Exports
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from app.code_intelligence.semantic_graph import (
    ConfidenceLevel,
    GraphNode,
    RelationshipType,
    make_symbol_id,
    normalize_qualified_name,
)

# Regex patterns
CLASS_PATTERN = re.compile(
    r"^(?:export\s+)?(?:abstract\s+)?class\s+(\w+)(?:\s+extends\s+(\w+(?:\.\w+)*))?(?:\s+implements\s+(\w+(?:\s*,\s*\w+)*))?"
)
INTERFACE_PATTERN = re.compile(
    r"^(?:export\s+)?interface\s+(\w+)(?:\s+extends\s+(\w+(?:\.\w+)*(?:\s*,\s*\w+(?:\.\w+)*)*))?"
)
TYPE_PATTERN = re.compile(r"^(?:export\s+)?type\s+(\w+)\s*=")
ENUM_PATTERN = re.compile(r"^(?:export\s+)?(?:const\s+)?enum\s+(\w+)")
FUNCTION_PATTERN = re.compile(
    r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\("
)
ASYNC_FUNCTION_PATTERN = re.compile(
    r"^(?:export\s+)?async\s+function\s+(\w+)\s*\("
)
ARROW_FUNCTION_PATTERN = re.compile(
    r"^(?:export\s+)?const\s+(\w+)\s*[=:]\s*(?:async\s+)?\(?.*?\)?\s*=>"
)
METHOD_PATTERN = re.compile(
    r"^(?:async\s+)?(\w+)\s*\([^)]*\)\s*[{:}]"
)
GETTER_SETTER_PATTERN = re.compile(
    r"^(?:get|set)\s+(\w+)\s*\([^)]*\)\s*[:{]"
)
CONSTRUCTOR_PATTERN = re.compile(r"^constructor\s*\(")

IMPORT_NAMED_PATTERN = re.compile(
    r"^import\s+\{([^}]+)\}\s+from\s+['\"]([^'\"]+)['\"]"
)
IMPORT_DEFAULT_PATTERN = re.compile(
    r"^import\s+(\w+)\s+from\s+['\"]([^'\"]+)['\"]"
)
IMPORT_NAMESPACE_PATTERN = re.compile(
    r"^import\s+\*\s+as\s+(\w+)\s+from\s+['\"]([^'\"]+)['\"]"
)
IMPORT_SIDE_EFFECT_PATTERN = re.compile(
    r"^import\s+['\"]([^'\"]+)['\"]"
)
EXPORT_DEFAULT_PATTERN = re.compile(
    r"^export\s+default\s+(?:class|function|const)\s+(\w+)"
)

# Decorator pattern (TypeScript)
DECORATOR_PATTERN = re.compile(r"^@(\w+(?:\.\w+)*)")


class TypeScriptJSParser:
    """Parse TypeScript and JavaScript source and extract structured symbols.

    Uses regex + brace-matching for structural analysis.
    Does NOT execute or import target code.
    """

    def __init__(self, file_path: str, content: str) -> None:
        self.file_path = file_path
        self.content = content
        self.lines = content.split("\n")
        self.language = self._detect_language(file_path)

        self.symbols: List[GraphNode] = []
        self.relationships: List[dict] = []
        self.diagnostics: List[str] = []

        # Brace/depth tracking
        self._brace_depths: List[int] = []
        self._parent_stack: List[Optional[str]] = []
        self._current_class_id: Optional[str] = None
        self._decorator_buffer: List[str] = []

    def parse(self) -> Tuple[List[GraphNode], List[dict], List[str]]:
        """Parse the file and extract symbols + relationships.

        Returns:
            Tuple of (symbols, relationships, diagnostics)
        """
        if not self.content.strip():
            return [], [], []

        # Precompute brace depths
        self._compute_brace_depths()

        # Create file/module node
        module_name = self.file_path.replace("/", ".").rsplit(".", 1)[0]
        module_name = module_name.replace("\\", ".")

        mod_id = make_symbol_id(self.file_path, module_name)
        self.symbols.append(GraphNode(
            id=mod_id,
            name=self.file_path.split("/")[-1] or self.file_path,
            qualified_name=module_name,
            kind="file",
            file_path=self.file_path,
            language=self.language,
            start_line=1,
            end_line=len(self.lines),
        ))

        # Pass 1: Collect decorators
        self._collect_decorators()

        # Pass 2: Extract top-level constructs
        self._extract_top_level(mod_id)

        # Pass 3: Extract imports
        self._extract_imports(mod_id)

        return self.symbols, self.relationships, self.diagnostics

    def _detect_language(self, file_path: str) -> str:
        """Detect language from file extension."""
        ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
        mapping = {
            "ts": "TypeScript",
            "tsx": "TypeScript React",
            "js": "JavaScript",
            "jsx": "JavaScript React",
            "mjs": "JavaScript",
            "cjs": "JavaScript",
            "mts": "TypeScript",
            "cts": "TypeScript",
        }
        return mapping.get(ext, "TypeScript")

    def _compute_brace_depths(self) -> None:
        """Compute brace depth for each line to help with scope estimation."""
        depth = 0
        for line in self.lines:
            depth += line.count("{") - line.count("}")
            self._brace_depths.append(depth)

    def _collect_decorators(self) -> None:
        """Collect decorator lines (TypeScript decorators like @Injectable)."""
        self._class_decorators: Dict[int, List[str]] = {}
        for i, line in enumerate(self.lines):
            stripped = line.strip()
            match = DECORATOR_PATTERN.match(stripped)
            if match:
                decorator_line = i + 1
                self._class_decorators.setdefault(decorator_line + 1, []).append(match.group(1))

    def _extract_top_level(self, module_id: str) -> None:
        """Extract top-level constructs: classes, functions, interfaces, etc."""
        i = 0
        while i < len(self.lines):
            stripped = self.lines[i].strip()
            line_num = i + 1

            # Skip empty lines, comments
            if not stripped or stripped.startswith("//") or stripped.startswith("/*"):
                i += 1
                continue

            # Collect decorators before class/functions
            decorators = self._class_decorators.get(line_num, [])

            # Check for class
            class_match = CLASS_PATTERN.match(stripped)
            if class_match:
                self._extract_class(class_match, decorators, line_num, i, module_id)
                i += 1
                continue

            # Check for interface
            iface_match = INTERFACE_PATTERN.match(stripped)
            if iface_match:
                self._extract_interface(iface_match, line_num, i, module_id)
                i += 1
                continue

            # Check for type alias
            type_match = TYPE_PATTERN.match(stripped)
            if type_match:
                self._extract_type(type_match, line_num, i, module_id)
                i += 1
                continue

            # Check for enum
            enum_match = ENUM_PATTERN.match(stripped)
            if enum_match:
                self._extract_enum(enum_match, line_num, i, module_id)
                i += 1
                continue

            # Check for export default
            export_match = EXPORT_DEFAULT_PATTERN.match(stripped)
            if export_match:
                self._extract_export_default(export_match, line_num, i, module_id)
                i += 1
                continue

            # Check for function
            func_match = FUNCTION_PATTERN.match(stripped)
            if func_match:
                self._extract_function(func_match, line_num, i, module_id, decorators)
                i += 1
                continue

            # Check for arrow function assigned to const
            arrow_match = ARROW_FUNCTION_PATTERN.match(stripped)
            if arrow_match:
                self._extract_arrow_function(arrow_match, line_num, i, module_id, decorators)
                i += 1
                continue

            i += 1

    def _extract_class(
        self,
        match: re.Match,
        decorators: List[str],
        line_num: int,
        line_index: int,
        module_id: str,
    ) -> None:
        """Extract a class definition."""
        name = match.group(1)
        extends = match.group(2)
        implements_str = match.group(3)

        end_line = self._find_block_end(line_index)
        qualified_name = normalize_qualified_name(self.file_path, name)
        symbol_id = make_symbol_id(self.file_path, qualified_name)

        # Build extends/implements list
        bases = []
        if extends:
            bases.append(extends)
        if implements_str:
            bases.extend([b.strip() for b in implements_str.split(",")])

        is_abstract = "abstract" in self.lines[line_index]

        meta: Dict[str, Any] = {
            "decorators": decorators,
            "extends": extends,
            "implements": [b.strip() for b in implements_str.split(",")] if implements_str else [],
            "is_abstract": is_abstract,
            "methods_count": 0,
        }

        node = GraphNode(
            id=symbol_id,
            name=name,
            qualified_name=qualified_name,
            kind="class",
            file_path=self.file_path,
            language=self.language,
            start_line=line_num,
            end_line=end_line,
            signature=self.lines[line_index][:200],
            metadata=meta,
        )
        self.symbols.append(node)

        # CONTAINS from module
        self.relationships.append({
            "source_id": module_id,
            "target_id": symbol_id,
            "relationship": RelationshipType.CONTAINS.value,
            "confidence": ConfidenceLevel.EXACT.value,
            "source_lines": [line_num],
            "resolution_detail": f"class {name} in file",
        })

        # Inheritance relationships
        for base in bases:
            self.relationships.append({
                "source_id": symbol_id,
                "target_id": f"__external__::{base}",
                "relationship": RelationshipType.INHERITS.value,
                "confidence": ConfidenceLevel.HIGH.value,
                "source_lines": [line_num],
                "resolution_detail": f"class {name} inherits {base}",
            })

        # Extract methods inside class body
        self._extract_class_methods(name, line_index, end_line, symbol_id)

    def _extract_class_methods(self, class_name: str, start_idx: int, end_idx: int, class_id: str) -> None:
        """Extract methods within a class body."""
        for i in range(start_idx + 1, min(end_idx, len(self.lines))):
            stripped = self.lines[i].strip()
            if not stripped or stripped.startswith("//") or stripped == "}":
                continue

            line_num = i + 1

            # Constructor
            if CONSTRUCTOR_PATTERN.match(stripped):
                self._extract_constructor(i, class_name, class_id)
                continue

            # Method
            method_match = METHOD_PATTERN.match(stripped)
            if method_match and not stripped.startswith(("if ", "for ", "while ", "switch ", "return ")):
                method_name = method_match.group(1)
                # Skip non-method keywords
                if method_name in ("if", "for", "while", "switch", "return", "try", "catch", "else"):
                    continue
                self._extract_method(method_name, i, class_name, class_id)
                continue

            # Getter/setter
            gs_match = GETTER_SETTER_PATTERN.match(stripped)
            if gs_match:
                self._extract_getter_setter(gs_match, i, class_name, class_id)

    def _extract_method(self, method_name: str, line_index: int, class_name: str, class_id: str) -> None:
        """Extract a method."""
        end_line = self._find_block_end(line_index, single_line=True)
        line_num = line_index + 1

        qualified_name = normalize_qualified_name(self.file_path, method_name, [class_name])
        symbol_id = make_symbol_id(self.file_path, qualified_name)

        node = GraphNode(
            id=symbol_id,
            name=method_name,
            qualified_name=qualified_name,
            kind="method",
            file_path=self.file_path,
            language=self.language,
            start_line=line_num,
            end_line=end_line,
            parent_id=class_id,
            signature=self.lines[line_index][:200],
            metadata={"is_constructor": method_name == "constructor", "class_name": class_name},
        )
        self.symbols.append(node)

        self.relationships.append({
            "source_id": class_id,
            "target_id": symbol_id,
            "relationship": RelationshipType.CONTAINS.value,
            "confidence": ConfidenceLevel.EXACT.value,
            "source_lines": [line_num],
            "resolution_detail": f"method {method_name} in class {class_name}",
        })

    def _extract_constructor(self, line_index: int, class_name: str, class_id: str) -> None:
        """Extract constructor method."""
        end_line = self._find_block_end(line_index, single_line=True)
        line_num = line_index + 1

        qualified_name = normalize_qualified_name(self.file_path, "constructor", [class_name])
        symbol_id = make_symbol_id(self.file_path, qualified_name)

        node = GraphNode(
            id=symbol_id,
            name="constructor",
            qualified_name=qualified_name,
            kind="constructor",
            file_path=self.file_path,
            language=self.language,
            start_line=line_num,
            end_line=end_line,
            parent_id=class_id,
            signature=self.lines[line_index][:200],
            metadata={"class_name": class_name},
        )
        self.symbols.append(node)

        self.relationships.append({
            "source_id": class_id,
            "target_id": symbol_id,
            "relationship": RelationshipType.CONTAINS.value,
            "confidence": ConfidenceLevel.EXACT.value,
            "source_lines": [line_num],
            "resolution_detail": f"constructor in class {class_name}",
        })

    def _extract_getter_setter(self, match: re.Match, line_index: int, class_name: str, class_id: str) -> None:
        """Extract getter/setter."""
        name = match.group(1)
        end_line = self._find_block_end(line_index, single_line=True)
        line_num = line_index + 1
        gs_type = "getter" if "get " in self.lines[line_index] else "setter"

        qualified_name = normalize_qualified_name(self.file_path, name, [class_name])
        symbol_id = make_symbol_id(self.file_path, qualified_name)

        node = GraphNode(
            id=symbol_id,
            name=name,
            qualified_name=qualified_name,
            kind=gs_type,
            file_path=self.file_path,
            language=self.language,
            start_line=line_num,
            end_line=end_line,
            parent_id=class_id,
            signature=self.lines[line_index][:200],
            metadata={"class_name": class_name},
        )
        self.symbols.append(node)

        self.relationships.append({
            "source_id": class_id,
            "target_id": symbol_id,
            "relationship": RelationshipType.CONTAINS.value,
            "confidence": ConfidenceLevel.EXACT.value,
            "source_lines": [line_num],
            "resolution_detail": f"{gs_type} {name} in class {class_name}",
        })

    def _extract_interface(
        self,
        match: re.Match,
        line_num: int,
        line_index: int,
        module_id: str,
    ) -> None:
        """Extract an interface definition."""
        name = match.group(1)
        extends = match.group(2)

        end_line = self._find_block_end(line_index)
        qualified_name = normalize_qualified_name(self.file_path, name)
        symbol_id = make_symbol_id(self.file_path, qualified_name)

        extends_list = [e.strip() for e in extends.split(",")] if extends else []

        node = GraphNode(
            id=symbol_id,
            name=name,
            qualified_name=qualified_name,
            kind="interface",
            file_path=self.file_path,
            language=self.language,
            start_line=line_num,
            end_line=end_line,
            signature=self.lines[line_index][:200],
            metadata={"extends": extends_list},
        )
        self.symbols.append(node)

        self.relationships.append({
            "source_id": module_id,
            "target_id": symbol_id,
            "relationship": RelationshipType.CONTAINS.value,
            "confidence": ConfidenceLevel.EXACT.value,
            "source_lines": [line_num],
            "resolution_detail": f"interface {name} in file",
        })

        for ext in extends_list:
            self.relationships.append({
                "source_id": symbol_id,
                "target_id": f"__external__::{ext}",
                "relationship": RelationshipType.INHERITS.value,
                "confidence": ConfidenceLevel.HIGH.value,
                "source_lines": [line_num],
                "resolution_detail": f"interface {name} extends {ext}",
            })

    def _extract_type(
        self,
        match: re.Match,
        line_num: int,
        line_index: int,
        module_id: str,
    ) -> None:
        """Extract a type alias."""
        name = match.group(1)

        qualified_name = normalize_qualified_name(self.file_path, name)
        symbol_id = make_symbol_id(self.file_path, qualified_name)

        node = GraphNode(
            id=symbol_id,
            name=name,
            qualified_name=qualified_name,
            kind="type",
            file_path=self.file_path,
            language=self.language,
            start_line=line_num,
            end_line=line_num,
            signature=self.lines[line_index][:200],
        )
        self.symbols.append(node)

        self.relationships.append({
            "source_id": module_id,
            "target_id": symbol_id,
            "relationship": RelationshipType.CONTAINS.value,
            "confidence": ConfidenceLevel.EXACT.value,
            "source_lines": [line_num],
            "resolution_detail": f"type alias {name} in file",
        })

    def _extract_enum(
        self,
        match: re.Match,
        line_num: int,
        line_index: int,
        module_id: str,
    ) -> None:
        """Extract an enum."""
        name = match.group(1)
        end_line = self._find_block_end(line_index)

        qualified_name = normalize_qualified_name(self.file_path, name)
        symbol_id = make_symbol_id(self.file_path, qualified_name)

        node = GraphNode(
            id=symbol_id,
            name=name,
            qualified_name=qualified_name,
            kind="enum",
            file_path=self.file_path,
            language=self.language,
            start_line=line_num,
            end_line=end_line,
            signature=self.lines[line_index][:200],
        )
        self.symbols.append(node)

        self.relationships.append({
            "source_id": module_id,
            "target_id": symbol_id,
            "relationship": RelationshipType.CONTAINS.value,
            "confidence": ConfidenceLevel.EXACT.value,
            "source_lines": [line_num],
            "resolution_detail": f"enum {name} in file",
        })

    def _extract_function(
        self,
        match: re.Match,
        line_num: int,
        line_index: int,
        module_id: str,
        decorators: List[str],
    ) -> None:
        """Extract a function."""
        name = match.group(1)
        end_line = self._find_block_end(line_index)

        is_async = bool(ASYNC_FUNCTION_PATTERN.match(self.lines[line_index]))

        qualified_name = normalize_qualified_name(self.file_path, name)
        symbol_id = make_symbol_id(self.file_path, qualified_name)

        node = GraphNode(
            id=symbol_id,
            name=name,
            qualified_name=qualified_name,
            kind="async_function" if is_async else "function",
            file_path=self.file_path,
            language=self.language,
            start_line=line_num,
            end_line=end_line,
            signature=self.lines[line_index][:200],
            metadata={"decorators": decorators, "is_async": is_async},
        )
        self.symbols.append(node)

        self.relationships.append({
            "source_id": module_id,
            "target_id": symbol_id,
            "relationship": RelationshipType.CONTAINS.value,
            "confidence": ConfidenceLevel.EXACT.value,
            "source_lines": [line_num],
            "resolution_detail": f"function {name} in file",
        })

    def _extract_arrow_function(
        self,
        match: re.Match,
        line_num: int,
        line_index: int,
        module_id: str,
        decorators: List[str],
    ) -> None:
        """Extract an arrow function assigned to a const."""
        name = match.group(1)
        # Arrow functions may span multiple lines - find end
        end_line = self._find_arrow_end(line_index)

        qualified_name = normalize_qualified_name(self.file_path, name)
        symbol_id = make_symbol_id(self.file_path, qualified_name)

        node = GraphNode(
            id=symbol_id,
            name=name,
            qualified_name=qualified_name,
            kind="function",
            file_path=self.file_path,
            language=self.language,
            start_line=line_num,
            end_line=end_line,
            signature=self.lines[line_index][:200],
            metadata={"decorators": decorators, "is_arrow": True},
        )
        self.symbols.append(node)

        self.relationships.append({
            "source_id": module_id,
            "target_id": symbol_id,
            "relationship": RelationshipType.CONTAINS.value,
            "confidence": ConfidenceLevel.EXACT.value,
            "source_lines": [line_num],
            "resolution_detail": f"arrow function {name}",
        })

    def _extract_export_default(
        self,
        match: re.Match,
        line_num: int,
        line_index: int,
        module_id: str,
    ) -> None:
        """Extract an export default declaration."""
        name = match.group(1)
        qualified_name = normalize_qualified_name(self.file_path, name)
        symbol_id = make_symbol_id(self.file_path, qualified_name)

        # Find if there's already a symbol with this name
        existing = [s for s in self.symbols if s.name == name]
        if existing:
            # Add EXPORTS relationship to existing symbol
            self.relationships.append({
                "source_id": module_id,
                "target_id": existing[0].id,
                "relationship": RelationshipType.EXPORTS.value,
                "confidence": ConfidenceLevel.EXACT.value,
                "source_lines": [line_num],
                "resolution_detail": f"export default {name}",
            })

    def _extract_imports(self, module_id: str) -> None:
        """Extract import statements."""
        for i, line in enumerate(self.lines):
            stripped = line.strip()
            line_num = i + 1

            # Named imports: import { X, Y } from 'module'
            named_match = IMPORT_NAMED_PATTERN.match(stripped)
            if named_match:
                names = [n.strip() for n in named_match.group(1).split(",")]
                source = named_match.group(2)
                for imp_name in names:
                    # Handle aliases: X as Y
                    actual_name = imp_name
                    alias = None
                    if " as " in imp_name:
                        parts = imp_name.split(" as ")
                        actual_name = parts[0].strip()
                        alias = parts[1].strip()

                    name = alias or actual_name
                    qualified_name = normalize_qualified_name(self.file_path, f"import:{source}.{name}")
                    symbol_id = make_symbol_id(self.file_path, qualified_name)

                    self.symbols.append(GraphNode(
                        id=symbol_id,
                        name=name,
                        qualified_name=qualified_name,
                        kind="import",
                        file_path=self.file_path,
                        language=self.language,
                        start_line=line_num,
                        end_line=line_num,
                        signature=f"import {{ {actual_name} }} from '{source}'",
                        metadata={"source": source, "original_name": actual_name},
                    ))

                    self.relationships.append({
                        "source_id": module_id,
                        "target_id": symbol_id,
                        "relationship": RelationshipType.IMPORTS.value,
                        "confidence": ConfidenceLevel.EXACT.value,
                        "source_lines": [line_num],
                        "resolution_detail": f"import {actual_name} from {source}",
                    })
                continue

            # Default imports: import X from 'module'
            default_match = IMPORT_DEFAULT_PATTERN.match(stripped)
            if default_match and "{" not in stripped:
                name = default_match.group(1)
                source = default_match.group(2)
                qualified_name = normalize_qualified_name(self.file_path, f"import:{source}.{name}")
                symbol_id = make_symbol_id(self.file_path, qualified_name)

                self.symbols.append(GraphNode(
                    id=symbol_id,
                    name=name,
                    qualified_name=qualified_name,
                    kind="import",
                    file_path=self.file_path,
                    language=self.language,
                    start_line=line_num,
                    end_line=line_num,
                    signature=f"import {name} from '{source}'",
                    metadata={"source": source},
                ))

                self.relationships.append({
                    "source_id": module_id,
                    "target_id": symbol_id,
                    "relationship": RelationshipType.IMPORTS.value,
                    "confidence": ConfidenceLevel.EXACT.value,
                    "source_lines": [line_num],
                    "resolution_detail": f"import {name} from {source}",
                })
                continue

            # Namespace imports: import * as X from 'module'
            ns_match = IMPORT_NAMESPACE_PATTERN.match(stripped)
            if ns_match:
                name = ns_match.group(1)
                source = ns_match.group(2)
                qualified_name = normalize_qualified_name(self.file_path, f"import:{source}.*")
                symbol_id = make_symbol_id(self.file_path, qualified_name)

                self.symbols.append(GraphNode(
                    id=symbol_id,
                    name=name,
                    qualified_name=qualified_name,
                    kind="import",
                    file_path=self.file_path,
                    language=self.language,
                    start_line=line_num,
                    end_line=line_num,
                    signature=f"import * as {name} from '{source}'",
                    metadata={"source": source},
                ))

                self.relationships.append({
                    "source_id": module_id,
                    "target_id": symbol_id,
                    "relationship": RelationshipType.IMPORTS.value,
                    "confidence": ConfidenceLevel.EXACT.value,
                    "source_lines": [line_num],
                    "resolution_detail": f"import * as {name} from {source}",
                })

    def _find_block_end(self, start_index: int, single_line: bool = False) -> int:
        """Find the end line of a brace-delimited block starting at start_index."""
        if start_index >= len(self.lines):
            return start_index + 1

        # Check if the line itself ends with a block
        first_line = self.lines[start_index].strip()
        if "{" in first_line:
            # Find matching closing brace
            depth = 0
            for i in range(start_index, len(self.lines)):
                depth += self.lines[i].count("{") - self.lines[i].count("}")
                if depth <= 0 and "{" in self.lines[start_index]:
                    return i + 1
            return len(self.lines)
        elif first_line.endswith(";") or first_line.endswith(":") or single_line:
            return start_index + 1
        else:
            # Check next few lines for braces
            for i in range(start_index + 1, min(start_index + 5, len(self.lines))):
                if "{" in self.lines[i]:
                    return self._find_block_end(i)
            return start_index + 1

    def _find_arrow_end(self, start_index: int) -> int:
        """Find the end of an arrow function."""
        depth = 0
        started = False
        for i in range(start_index, len(self.lines)):
            line = self.lines[i]
            if "=>" in line:
                started = True
            if started:
                depth += line.count("{") - line.count("}")
                if depth <= 0 and "{" in line:
                    return i + 1
                if depth <= 0 and ";" in line:
                    return i + 1
        return len(self.lines)

    def supports_language(self, language: str) -> bool:
        lang = language.lower()
        return lang in {"typescript", "javascript", "ts", "js", "tsx", "jsx", "mjs", "cjs"}
