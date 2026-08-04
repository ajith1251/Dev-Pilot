"""
Language Detector — deterministic language identification from file extensions.

No LLM calls. No heuristic guessing beyond extension mapping.
"""

from __future__ import annotations

from typing import Dict, List

from app.models.profile import LanguageEntry
from app.services.repository_scanner import ScannedFile

# Complete extension-to-language mapping
EXTENSION_MAP: Dict[str, str] = {
    # Python
    ".py": "Python",
    ".pyx": "Python",
    ".pyi": "Python",
    ".pyw": "Python",
    ".ipynb": "Jupyter Notebook",
    # JavaScript
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    # TypeScript
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    # Java / JVM
    ".java": "Java",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".scala": "Scala",
    ".groovy": "Groovy",
    ".clj": "Clojure",
    ".cljs": "ClojureScript",
    # Go
    ".go": "Go",
    # Rust
    ".rs": "Rust",
    # C / C++
    ".c": "C",
    ".h": "C",
    ".cpp": "C++",
    ".cxx": "C++",
    ".hpp": "C++",
    ".hxx": "C++",
    ".cc": "C++",
    ".c++": "C++",
    # C#
    ".cs": "C#",
    # Ruby
    ".rb": "Ruby",
    ".erb": "Ruby",
    ".rake": "Ruby",
    ".gemspec": "Ruby",
    # PHP
    ".php": "PHP",
    ".phtml": "PHP",
    ".php3": "PHP",
    ".php4": "PHP",
    ".php5": "PHP",
    # Swift
    ".swift": "Swift",
    # Dart
    ".dart": "Dart",
    # R
    ".r": "R",
    ".rmd": "R",
    ".R": "R",
    # Shell
    ".sh": "Shell",
    ".bash": "Shell",
    ".zsh": "Shell",
    ".fish": "Shell",
    # PowerShell
    ".ps1": "PowerShell",
    ".psm1": "PowerShell",
    ".psd1": "PowerShell",
    # Batch
    ".bat": "Batch",
    ".cmd": "Batch",
    # Web
    ".html": "HTML",
    ".htm": "HTML",
    ".css": "CSS",
    ".scss": "SCSS",
    ".sass": "SCSS",
    ".less": "Less",
    # SQL
    ".sql": "SQL",
    # Markup / Data
    ".json": "JSON",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".toml": "TOML",
    ".xml": "XML",
    ".md": "Markdown",
    ".rst": "reStructuredText",
    ".adoc": "AsciiDoc",
    ".tex": "LaTeX",
    # Config
    ".ini": "INI",
    ".cfg": "INI",
    ".conf": "INI",
    # GraphQL
    ".graphql": "GraphQL",
    ".gql": "GraphQL",
    # Vue / Svelte
    ".vue": "Vue",
    ".svelte": "Svelte",
    # Docker
    ".dockerfile": "Dockerfile",
    # Lua
    ".lua": "Lua",
    # Haskell
    ".hs": "Haskell",
    # Elixir
    ".ex": "Elixir",
    ".exs": "Elixir",
    # Erlang
    ".erl": "Erlang",
    ".hrl": "Erlang",
    # Zig
    ".zig": "Zig",
    # Nim
    ".nim": "Nim",
    # OCaml / F#
    ".ml": "OCaml",
    ".mli": "OCaml",
    ".fs": "F#",
    ".fsx": "F#",
    # Julia
    ".jl": "Julia",
    # Coq
    ".v": "Coq",
    # Assembly
    ".asm": "Assembly",
    ".s": "Assembly",
    ".S": "Assembly",
    # Make
    "Makefile": "Make",
    "makefile": "Make",
    "GNUmakefile": "Make",
    # Terraform
    ".tf": "Terraform",
    ".tfvars": "Terraform",
    # Protocol Buffers
    ".proto": "Protocol Buffers",
    # Solidty
    ".sol": "Solidity",
    # CMake
    ".cmake": "CMake",
    "CMakeLists.txt": "CMake",
}


class LanguageDetector:
    """Deterministic language detection from file extensions."""

    def detect(self, files: List[ScannedFile]) -> List[LanguageEntry]:
        """Detect languages from a list of scanned files.

        Args:
            files: List of scanned files.

        Returns:
            List of LanguageEntry sorted by file_count descending.
        """
        lang_map: Dict[str, LanguageEntry] = {}

        for f in files:
            if f.is_binary:
                continue

            language = self._detect_language(f.name, f.extension)
            if not language:
                continue

            if language in lang_map:
                lang_map[language].file_count += 1
                if f.extension not in lang_map[language].extensions:
                    lang_map[language].extensions.append(f.extension)
                lang_map[language].byte_count += f.size_bytes
            else:
                lang_map[language] = LanguageEntry(
                    name=language,
                    file_count=1,
                    byte_count=f.size_bytes,
                    extensions=[f.extension] if f.extension else [],
                )

        # Calculate percentages
        total = sum(l.file_count for l in lang_map.values())
        if total > 0:
            for lang in lang_map.values():
                lang.percentage = round(lang.file_count / total * 100, 1)

        return sorted(lang_map.values(), key=lambda x: x.file_count, reverse=True)

    @staticmethod
    def _detect_language(file_name: str, extension: str) -> Optional[str]:
        """Detect language from file name or extension.

        Args:
            file_name: Full file name (e.g. 'main.py').
            extension: File extension (e.g. '.py').

        Returns:
            Language name or None if unknown.
        """
        # Check by exact file name (for files without typical extensions)
        if file_name in EXTENSION_MAP:
            return EXTENSION_MAP[file_name]

        # Check by extension
        if extension in EXTENSION_MAP:
            return EXTENSION_MAP[extension]

        return None


def get_supported_languages() -> List[str]:
    """Return the list of all supported language names."""
    return sorted(set(EXTENSION_MAP.values()))
