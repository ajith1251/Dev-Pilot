"""
Base parser abstraction for language-aware code parsing.

Each language parser implements the CodeParser interface to extract
symbols from source code. Parsers must be static-only — never execute
repository code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional

from app.models.rag import CodeSymbol, SymbolKind


@dataclass
class ParseResult:
    """Result of parsing a single source file."""

    file_path: str
    language: str
    symbols: List[CodeSymbol] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    success: bool = True


class CodeParser(ABC):
    """Abstract base for language-specific code parsers."""

    @abstractmethod
    def parse(self, file_path: str, content: str) -> ParseResult:
        """Parse source code and extract symbols.

        Args:
            file_path: Relative path of the file (for symbol IDs).
            content: Source code content as string.

        Returns:
            ParseResult with extracted symbols or errors.
        """
        ...

    @abstractmethod
    def supports_language(self, language: str) -> bool:
        """Whether this parser supports the given language."""
        ...

    @staticmethod
    def make_symbol_id(file_path: str, qualified_name: str) -> str:
        """Create a deterministic symbol ID.

        Format: file_path::qualified_name
        """
        return f"{file_path}::{qualified_name}"

    @staticmethod
    def normalize_qualified_name(
        file_path: str,
        name: str,
        parent_names: Optional[List[str]] = None,
    ) -> str:
        """Build a qualified name from file path and parent hierarchy."""
        # Use file path as module prefix
        module = file_path.replace("/", ".").rsplit(".", 1)[0] if "." in file_path else file_path
        module = module.replace("\\", ".")

        if parent_names:
            return f"{module}.{'.'.join(parent_names)}.{name}"
        return f"{module}.{name}"
