"""
Analysis tools for repository traversal, language detection,
and structure building.

These tools are used by the RepositoryAnalyzerAgent and can also
be used independently by other agents.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Set, Tuple

from app.models.analysis import (
    DependencyInfo,
    DirectoryNode,
    FileCategory,
    FileInfo,
    FrameworkCategory,
    FrameworkInfo,
    LanguageInfo,
    RepositoryStructure,
)
from app.services.github import GitHubService
from app.tools.base import BaseTool


# ── Language detection ──────────────────────────────────────────

LANGUAGE_MAP: Dict[str, str] = {
    # Python
    ".py": "Python",
    ".pyx": "Python (Cython)",
    ".pyi": "Python",
    ".pyw": "Python",
    # JavaScript / TypeScript
    ".js": "JavaScript",
    ".jsx": "JSX (JavaScript)",
    ".ts": "TypeScript",
    ".tsx": "TSX (TypeScript)",
    ".mjs": "JavaScript (ES Module)",
    ".cjs": "JavaScript (CommonJS)",
    # Java / JVM
    ".java": "Java",
    ".kt": "Kotlin",
    ".kts": "Kotlin Script",
    ".scala": "Scala",
    ".groovy": "Groovy",
    ".clj": "Clojure",
    # Go
    ".go": "Go",
    # Rust
    ".rs": "Rust",
    # C / C++
    ".c": "C",
    ".h": "C/C++ Header",
    ".cpp": "C++",
    ".cxx": "C++",
    ".hpp": "C++ Header",
    ".cc": "C++",
    # C#
    ".cs": "C#",
    ".csproj": "C# Project",
    # Ruby
    ".rb": "Ruby",
    ".erb": "ERB (Ruby)",
    # PHP
    ".php": "PHP",
    ".phtml": "PHP Template",
    # Swift
    ".swift": "Swift",
    # Dart
    ".dart": "Dart",
    # R
    ".r": "R",
    ".rmd": "R Markdown",
    # Shell / Scripts
    ".sh": "Shell",
    ".bash": "Bash",
    ".zsh": "Zsh",
    ".ps1": "PowerShell",
    ".bat": "Batch",
    # Web
    ".html": "HTML",
    ".htm": "HTML",
    ".css": "CSS",
    ".scss": "SCSS",
    ".sass": "Sass",
    ".less": "Less",
    ".vue": "Vue",
    ".svelte": "Svelte",
    # Config / Data
    ".json": "JSON",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".toml": "TOML",
    ".xml": "XML",
    ".ini": "INI",
    ".cfg": "Configuration",
    ".env": "Environment",
    ".md": "Markdown",
    ".rst": "reStructuredText",
    ".sql": "SQL",
    ".graphql": "GraphQL",
    ".gql": "GraphQL",
    # Docker / DevOps
    ".dockerfile": "Dockerfile",
    # Lua
    ".lua": "Lua",
    # Haskell
    ".hs": "Haskell",
    # Elixir
    ".ex": "Elixir",
    ".exs": "Elixir Script",
    # Erlang
    ".erl": "Erlang",
    ".hrl": "Erlang Header",
    # Zig
    ".zig": "Zig",
    # Nim
    ".nim": "Nim",
    # OCaml / F#
    ".ml": "OCaml",
    ".fs": "F#",
    ".fsx": "F# Script",
    # Julia
    ".jl": "Julia",
    # Coq
    ".v": "Coq",
    # LaTeX
    ".tex": "LaTeX",
    ".sty": "LaTeX Style",
    ".cls": "LaTeX Class",
    # Assembly
    ".asm": "Assembly",
    ".s": "Assembly",
}

# ── Config file patterns ────────────────────────────────────────

CONFIG_FILES: Dict[str, str] = {
    # Python
    "setup.py": "Python Setup",
    "setup.cfg": "Python Config",
    "pyproject.toml": "Python Project",
    "Pipfile": "Pipenv",
    "Pipfile.lock": "Pipenv Lock",
    "poetry.lock": "Poetry Lock",
    "requirements.txt": "Python Dependencies",
    "requirements-dev.txt": "Python Dev Dependencies",
    "MANIFEST.in": "Python Manifest",
    # JavaScript / TypeScript
    "package.json": "npm Package",
    "package-lock.json": "npm Lock",
    "yarn.lock": "Yarn Lock",
    "pnpm-lock.yaml": "pnpm Lock",
    "tsconfig.json": "TypeScript Config",
    ".eslintrc.js": "ESLint Config",
    ".eslintrc.json": "ESLint Config",
    ".eslintrc": "ESLint Config",
    ".prettierrc": "Prettier Config",
    ".prettierrc.js": "Prettier Config",
    "babel.config.js": "Babel Config",
    "babel.config.json": "Babel Config",
    "webpack.config.js": "Webpack Config",
    "vite.config.js": "Vite Config",
    "vite.config.ts": "Vite Config",
    "next.config.js": "Next.js Config",
    "nuxt.config.js": "Nuxt Config",
    "jest.config.js": "Jest Config",
    "jest.config.ts": "Jest Config",
    # Go
    "go.mod": "Go Module",
    "go.sum": "Go Checksum",
    # Rust
    "Cargo.toml": "Cargo Config",
    "Cargo.lock": "Cargo Lock",
    # Ruby
    "Gemfile": "Bundler Gemfile",
    "Gemfile.lock": "Bundler Lock",
    # Java
    "pom.xml": "Maven POM",
    "build.gradle": "Gradle Build",
    "build.gradle.kts": "Gradle Kotlin Build",
    "settings.gradle": "Gradle Settings",
    # Others
    "Dockerfile": "Docker",
    "docker-compose.yml": "Docker Compose",
    ".env.example": "Environment Template",
    ".gitignore": "Git Ignore",
    ".gitattributes": "Git Attributes",
    ".editorconfig": "Editor Config",
    "Makefile": "Make Build",
    "CMakeLists.txt": "CMake Build",
    "composer.json": "Composer (PHP)",
    "mix.exs": "Mix (Elixir)",
}

ENTRY_POINT_PATTERNS: Dict[str, str] = {
    "main.py": "Python Entry",
    "app.py": "Python App Entry",
    "cli.py": "Python CLI Entry",
    "manage.py": "Django Manager",
    "wsgi.py": "WSGI Entry",
    "asgi.py": "ASGI Entry",
    "index.js": "Node.js Entry",
    "index.ts": "TypeScript Entry",
    "index.jsx": "React Entry",
    "main.js": "Node.js Entry",
    "app.js": "Node.js App Entry",
    "server.js": "Node.js Server",
    "server.ts": "TypeScript Server",
    "main.go": "Go Entry",
    "main.rs": "Rust Entry (Crate Root)",
    "lib.rs": "Rust Lib Entry",
    "main.java": "Java Entry",
    "Main.java": "Java Entry",
    "Program.cs": "C# Entry",
    "main.swift": "Swift Entry",
    "main.kt": "Kotlin Entry",
}

TEST_FILE_PREFIXES = ("test_", "spec_")
TEST_FILE_SUFFIXES = ("_test", "_spec", ".test.", ".spec.")
TEST_DIR_NAMES = {"test", "tests", "spec", "specs", "__tests__", "test_utils"}

BINARY_EXTENSIONS: Set[str] = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".mp4", ".avi", ".mov", ".mkv",
    ".mp3", ".wav", ".ogg", ".flac",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".zip", ".tar", ".gz", ".bz2", ".rar", ".7z",
    ".exe", ".dll", ".so", ".dylib", ".class", ".jar",
    ".pyc", ".pyo",
    ".DS_Store",
}


def detect_file_category(file_name: str, file_path: str) -> FileCategory:
    """Classify a file by its role in the project."""
    # Build scripts (named files, no extension matching needed)
    if file_name in {"Makefile", "Dockerfile", "CMakeLists.txt"}:
        return FileCategory.BUILD

    # Check against known config files by name
    if file_name in CONFIG_FILES:
        return FileCategory.CONFIG

    # Entry points
    if file_name in ENTRY_POINT_PATTERNS:
        return FileCategory.SOURCE

    # Check for test files by prefix/suffix
    if file_name.startswith(TEST_FILE_PREFIXES):
        return FileCategory.TEST
    for suffix in TEST_FILE_SUFFIXES:
        if suffix in file_name:
            return FileCategory.TEST

    # Check parent path for test directories
    if any(f"/{d}/" in f"/{file_path}/" for d in TEST_DIR_NAMES):
        return FileCategory.TEST

    ext = os.path.splitext(file_name)[1].lower()

    # Documentation
    if ext in {".md", ".rst", ".txt", ".adoc"}:
        return FileCategory.DOCUMENTATION

    # Config by extension
    if ext in {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".env"}:
        return FileCategory.CONFIG

    # Scripts
    if ext in {".sh", ".bash", ".ps1", ".bat"}:
        return FileCategory.SCRIPT

    # Templates
    if ext in {".html", ".htm", ".vue", ".svelte", ".jinja", ".jinja2", ".j2", ".hbs"}:
        return FileCategory.TEMPLATE

    # Source code
    if ext in LANGUAGE_MAP:
        return FileCategory.SOURCE

    return FileCategory.UNKNOWN


# ── Tool: ListGitHubDirectory ───────────────────────────────────


class ListGitHubDirectoryTool(BaseTool):
    """List the contents of a GitHub repository directory."""

    def __init__(self) -> None:
        super().__init__(
            name="list_github_directory",
            description="List files and directories in a GitHub repository path",
        )
        self._github = GitHubService()

    async def execute(self, owner: str, repo: str, path: str = "") -> Any:
        return await self._github.get_repo_contents(owner, repo, path)


class FetchGitHubFileTool(BaseTool):
    """Fetch a file's content from a GitHub repository."""

    def __init__(self) -> None:
        super().__init__(
            name="fetch_github_file",
            description="Read a file's content from a GitHub repository",
        )
        self._github = GitHubService()

    async def execute(self, owner: str, repo: str, path: str) -> Any:
        return await self._github.get_file_content(owner, repo, path)


# ── Structure Builder ───────────────────────────────────────────


async def build_repo_structure(
    owner: str,
    repo: str,
    branch: Optional[str] = None,
    max_depth: int = 10,
    github: Optional[GitHubService] = None,
) -> RepositoryStructure:
    """Recursively build a directory tree for a GitHub repository.

    Args:
        owner: Repository owner.
        repo: Repository name.
        branch: Branch to analyze.
        max_depth: Maximum directory recursion depth.
        github: Optional pre-configured GitHubService instance.

    Returns:
        A RepositoryStructure with the full tree, file/dir counts, and depth.
    """
    github = github or GitHubService()

    async def _recurse(
        path: str,
        depth: int = 0,
    ) -> Tuple[DirectoryNode, int, int]:
        """Recursively build a directory node.

        Returns:
            Tuple of (node, file_count, dir_count).
        """
        file_count = 0
        dir_count = 0

        try:
            contents = await github.get_repo_contents(owner, repo, path)
        except Exception:
            return (
                DirectoryNode(
                    path=path,
                    name=os.path.basename(path) if path else repo,
                ),
                0,
                0,
            )

        dirs: List[DirectoryNode] = []
        files: List[FileInfo] = []

        for item in contents:
            item_name = item.get("name", "")
            item_path = item.get("path", "")
            item_type = item.get("type", "file")

            # Skip common generated/cache directories
            if item_name.startswith(".") and item_name not in {
                ".env",
                ".env.example",
                ".gitignore",
                ".gitattributes",
                ".editorconfig",
            }:
                if item_type == "dir" and item_name in {
                    ".git", "__pycache__", "node_modules",
                    ".next", ".venv", ".mypy_cache", ".pytest_cache",
                    ".ruff_cache", "venv", ".tox",
                }:
                    continue
                if item_name.startswith(".") and item_type == "dir":
                    continue

            if item_type == "dir":
                dir_count += 1
                if depth < max_depth:
                    child_node, cf, cd = await _recurse(item_path, depth + 1)
                    dirs.append(child_node)
                    file_count += cf
                    dir_count += cd
                else:
                    dirs.append(
                        DirectoryNode(
                            path=item_path,
                            name=item_name,
                        )
                    )
            else:
                file_count += 1
                ext = os.path.splitext(item_name)[1].lower()
                size = item.get("size", 0)
                language = LANGUAGE_MAP.get(ext)
                is_binary = ext in BINARY_EXTENSIONS

                file_info = FileInfo(
                    path=item_path,
                    name=item_name,
                    extension=ext,
                    size_bytes=size,
                    category=detect_file_category(item_name, item_path),
                    language=language,
                    is_binary=is_binary,
                )
                files.append(file_info)

        node = DirectoryNode(
            path=path,
            name=os.path.basename(path) if path else repo,
            directories=sorted(dirs, key=lambda d: d.name),
            files=sorted(files, key=lambda f: f.name),
        )

        return node, file_count, dir_count

    root_node, total_files, total_dirs = await _recurse("")

    return RepositoryStructure(
        tree=root_node,
        total_files=total_files,
        total_dirs=total_dirs,
        depth=max_depth,
    )


def detect_languages(files: List[FileInfo]) -> List[LanguageInfo]:
    """Aggregate language statistics from a flat list of files.

    Args:
        files: All files in the repository.

    Returns:
        List of LanguageInfo sorted by file_count descending.
    """
    lang_map: Dict[str, LanguageInfo] = {}

    for f in files:
        if not f.language or f.is_binary:
            continue

        if f.language in lang_map:
            lang_map[f.language].file_count += 1
            if f.extension not in lang_map[f.language].extensions:
                lang_map[f.language].extensions.append(f.extension)
            lang_map[f.language].total_bytes += f.size_bytes
        else:
            lang_map[f.language] = LanguageInfo(
                name=f.language,
                file_count=1,
                extensions=[f.extension],
                total_bytes=f.size_bytes,
            )

    return sorted(lang_map.values(), key=lambda x: x.file_count, reverse=True)


def detect_frameworks(
    files: List[FileInfo],
    dependencies: List[DependencyInfo],
) -> List[FrameworkInfo]:
    """Detect frameworks and tools based on files and dependencies.

    Args:
        files: All files in the repository.
        dependencies: Detected dependencies.

    Returns:
        List of FrameworkInfo with confidence scores.
    """
    found: Dict[str, FrameworkInfo] = {}
    file_names = {f.name for f in files}
    file_paths = {f.path for f in files}

    # Check for web frameworks by config files
    if "next.config.js" in file_names or "next.config.ts" in file_names:
        found["Next.js"] = FrameworkInfo(
            name="Next.js",
            category=FrameworkCategory.FRONTEND,
            confidence=0.95,
            evidence=["next.config.js / next.config.ts"],
        )
    if "vite.config.js" in file_names or "vite.config.ts" in file_names:
        found["Vite"] = FrameworkInfo(
            name="Vite",
            category=FrameworkCategory.FRONTEND,
            confidence=0.95,
            evidence=["vite.config.js / vite.config.ts"],
        )
    if "manage.py" in file_names:
        found["Django"] = FrameworkInfo(
            name="Django",
            category=FrameworkCategory.BACKEND,
            confidence=0.95,
            evidence=["manage.py"],
        )
    if "requirements.txt" in file_names:
        found["pip"] = FrameworkInfo(
            name="pip",
            category=FrameworkCategory.PACKAGE_MANAGER,
            confidence=0.9,
            evidence=["requirements.txt"],
        )
    if "poetry.lock" in file_names or "pyproject.toml" in file_names:
        found["Poetry"] = FrameworkInfo(
            name="Poetry",
            category=FrameworkCategory.PACKAGE_MANAGER,
            confidence=0.85,
            evidence=["pyproject.toml"],
        )
    if "package.json" in file_names:
        found["npm"] = FrameworkInfo(
            name="npm",
            category=FrameworkCategory.PACKAGE_MANAGER,
            confidence=0.9,
            evidence=["package.json"],
        )
    if "Cargo.toml" in file_names:
        found["Cargo"] = FrameworkInfo(
            name="Cargo",
            category=FrameworkCategory.BUILD_TOOL,
            confidence=0.95,
            evidence=["Cargo.toml"],
        )
    if "go.mod" in file_names:
        found["Go Modules"] = FrameworkInfo(
            name="Go Modules",
            category=FrameworkCategory.PACKAGE_MANAGER,
            confidence=0.95,
            evidence=["go.mod"],
        )
    if "Dockerfile" in file_names:
        found["Docker"] = FrameworkInfo(
            name="Docker",
            category=FrameworkCategory.DEVOPS,
            confidence=0.95,
            evidence=["Dockerfile"],
        )
    for path in file_paths:
        if "workflows" in path and path.endswith(".yml"):
            found["GitHub Actions"] = FrameworkInfo(
                name="GitHub Actions",
                category=FrameworkCategory.DEVOPS,
                confidence=0.9,
                evidence=[path],
            )
            break

    return sorted(found.values(), key=lambda x: x.confidence, reverse=True)


def detect_dependencies(files: List[FileInfo]) -> List[DependencyInfo]:
    """Detect package dependencies from config/lock files.

    Args:
        files: All files in the repository.

    Returns:
        List of high-level DependencyInfo by package manager presence.
    """
    deps: List[DependencyInfo] = []
    file_names = {f.name for f in files}

    if "requirements.txt" in file_names:
        deps.append(
            DependencyInfo(name="Python packages", manager="pip", version_spec=None)
        )
    if "package.json" in file_names:
        deps.append(
            DependencyInfo(
                name="npm packages", manager="npm", version_spec=None
            )
        )
    if "Cargo.toml" in file_names:
        deps.append(
            DependencyInfo(name="Cargo crates", manager="cargo", version_spec=None)
        )
    if "go.mod" in file_names:
        deps.append(DependencyInfo(name="Go modules", manager="go", version_spec=None))
    if "Gemfile" in file_names:
        deps.append(
            DependencyInfo(name="Ruby gems", manager="bundler", version_spec=None)
        )

    return deps


def collect_files(node: DirectoryNode) -> List[FileInfo]:
    """Flatten a directory tree into a list of all files.

    Args:
        node: Root directory node.

    Returns:
        Flat list of all FileInfo objects.
    """
    result: List[FileInfo] = list(node.files)
    for child in node.directories:
        result.extend(collect_files(child))
    return result


def find_files_by_category(
    files: List[FileInfo], category: FileCategory
) -> List[FileInfo]:
    """Filter files by their category.

    Args:
        files: List of files.
        category: Category to filter by.

    Returns:
        Files matching the given category.
    """
    return [f for f in files if f.category == category]


def find_entry_points(files: List[FileInfo]) -> List[FileInfo]:
    """Find likely entry point files."""
    result: List[FileInfo] = []
    file_names = {f.name for f in files}

    for name, _ in ENTRY_POINT_PATTERNS.items():
        if name in file_names:
            matching = [f for f in files if f.name == name]
            result.extend(matching)

    # Also look for root-level source files as potential entry points
    for f in files:
        if f.path == f.name and f.category == FileCategory.SOURCE:
            if f not in result:
                result.append(f)

    return result


def compute_max_depth(node: DirectoryNode, current: int = 0) -> int:
    """Compute the maximum depth of a directory tree."""
    if not node.directories:
        return current
    return max(compute_max_depth(child, current + 1) for child in node.directories)
