"""
Fallback parser — lightweight deterministic parsing for languages without
dedicated AST-based parsers.

Uses line-based heuristics to extract function/class-like definitions.
Does NOT attempt to be a complete parser. Provides graceful degradation
when no language-specific parser is available.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Set

from app.models.rag import CodeSymbol, SymbolKind
from app.rag.parsers.base import CodeParser, ParseResult

# Regex patterns for common function/class definitions across languages
FUNCTION_PATTERNS: Dict[str, List[str]] = {
    "JavaScript": [
        r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(",
        r"^(?:export\s+)?(?:async\s+)?\(?\w+\)?\s*=>\s*{",
        r"^(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s+)?\(?.*?\)?\s*=>",
    ],
    "TypeScript": [
        r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*[<\(]",
        r"^(?:export\s+)?(?:async\s+)?\(?\w+\)?\s*=>\s*{",
        r"^(?:export\s+)?const\s+(\w+)\s*:\s*\w+\s*=\s*(?:async\s+)?\(",
        r"^(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s+)?\(?.*?\)?\s*=>",
    ],
    "Java": [
        r"^(?:public|private|protected|static)?\s*(?:public|private|protected|static)?\s*\w+\s+(\w+)\s*\(",
    ],
    "Go": [
        r"^func\s+(\w+)\s*\(",
        r"^func\s+\([\w\s\*]+\)\s+(\w+)\s*\(",
    ],
    "Rust": [
        r"^fn\s+(\w+)\s*[<\(]",
    ],
}

CLASS_PATTERNS: Dict[str, List[str]] = {
    "JavaScript": [
        r"^(?:export\s+)?class\s+(\w+)",
    ],
    "TypeScript": [
        r"^(?:export\s+)?(?:abstract\s+)?class\s+(\w+)",
        r"^(?:export\s+)?interface\s+(\w+)",
        r"^(?:export\s+)?type\s+(\w+)\s*=",
    ],
    "Java": [
        r"^(?:public|private|protected)?\s*(?:abstract|final)?\s*class\s+(\w+)",
        r"^(?:public|private|protected)?\s*interface\s+(\w+)",
    ],
    "Go": [
        r"^type\s+(\w+)\s+struct",
        r"^type\s+(\w+)\s+interface",
    ],
    "Rust": [
        r"^struct\s+(\w+)",
        r"^enum\s+(\w+)",
        r"^trait\s+(\w+)",
        r"^impl\s+(\w+)",
    ],
}


class FallbackParser(CodeParser):
    """Lightweight line-based parser for languages without a dedicated parser.

    Provides best-effort symbol extraction using regex patterns.
    Does NOT guarantee complete or correct extraction for all syntax.
    """

    # Languages this fallback can attempt to parse with reasonable heuristics
    SUPPORTED_LANGUAGES: Set[str] = {
        "javascript", "typescript", "java", "go", "rust",
    }

    def __init__(self) -> None:
        self._function_patterns = FUNCTION_PATTERNS
        self._class_patterns = CLASS_PATTERNS

    def parse(self, file_path: str, content: str) -> ParseResult:
        """Parse source code using fallback heuristics."""
        symbols: List[CodeSymbol] = []
        lines = content.split("\n")

        # Detect language from file extension if possible
        language = self._detect_language(file_path)
        if not language:
            # Use generic heuristics
            language = "unknown"

        func_patterns = self._function_patterns.get(language, [])
        class_patterns = self._class_patterns.get(language, [])

        # Scan lines for patterns
        brace_depth = 0
        line_braces: List[int] = []

        for i, line in enumerate(lines, 1):
            stripped = line.strip()

            # Track brace depth for boundary estimation
            brace_depth += stripped.count("{") - stripped.count("}")
            line_braces.append(brace_depth)

            # Check for class definitions
            for pattern in class_patterns:
                match = re.search(pattern, stripped)
                if match:
                    name = match.group(1)
                    start_line = i
                    # Estimate end line (until brace depth returns to starting level)
                    start_depth = brace_depth
                    end_line = self._find_brace_end(lines, i, start_depth)
                    qualified_name = self.normalize_qualified_name(file_path, name)
                    symbol_id = self.make_symbol_id(file_path, qualified_name)

                    kind = SymbolKind.CLASS
                    if "interface" in pattern or "interface" in stripped:
                        kind = SymbolKind.INTERFACE
                    elif "type" in pattern and language in {"typescript", "typescriptreact"}:
                        kind = SymbolKind.TYPE

                    symbols.append(CodeSymbol(
                        id=symbol_id,
                        name=name,
                        qualified_name=qualified_name,
                        kind=kind,
                        file_path=file_path,
                        language=language,
                        start_line=start_line,
                        end_line=end_line if end_line else start_line + 10,
                        signature=stripped[:120] if len(stripped) > 120 else stripped,
                    ))

            # Check for function definitions
            for pattern in func_patterns:
                match = re.search(pattern, stripped)
                if match:
                    name = match.group(1)
                    start_line = i
                    start_depth = brace_depth
                    end_line = self._find_brace_end(lines, i, start_depth)
                    qualified_name = self.normalize_qualified_name(file_path, name)
                    symbol_id = self.make_symbol_id(file_path, qualified_name)

                    symbols.append(CodeSymbol(
                        id=symbol_id,
                        name=name,
                        qualified_name=qualified_name,
                        kind=SymbolKind.FUNCTION,
                        file_path=file_path,
                        language=language,
                        start_line=start_line,
                        end_line=end_line if end_line else start_line + 10,
                        signature=stripped[:120] if len(stripped) > 120 else stripped,
                    ))

        return ParseResult(
            file_path=file_path,
            language=language,
            symbols=symbols,
            success=True,
        )

    def supports_language(self, language: str) -> bool:
        return language.lower() in self.SUPPORTED_LANGUAGES

    def _detect_language(self, file_path: str) -> Optional[str]:
        """Detect language from file extension."""
        ext_map = {
            ".js": "JavaScript",
            ".jsx": "JavaScript",
            ".ts": "TypeScript",
            ".tsx": "TypeScript",
            ".java": "Java",
            ".go": "Go",
            ".rs": "Rust",
        }
        import os
        ext = os.path.splitext(file_path)[1].lower()
        return ext_map.get(ext)

    def _find_brace_end(self, lines: List[str], start: int, base_depth: int) -> Optional[int]:
        """Estimate where a brace-delimited block ends."""
        depth = 0
        # Start from the line with the opening brace
        for i in range(start - 1, len(lines)):
            depth += lines[i].count("{") - lines[i].count("}")
            if depth <= 0 and i > start - 1:
                return i + 1
        return len(lines)
