"""
Repository Scanner — safe, recursive file system traversal.

Responsibilities:
- Recursively traverse a local repository path
- Normalize paths, collect file metadata
- Exclude ignored/generated directories
- Protect against symlink loops, unreadable files, large files
- Record scan statistics
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from app.core.logging import logger

# Directories always excluded from scanning
DEFAULT_IGNORED_DIRS: Set[str] = {
    ".git",
    "node_modules",
    ".next",
    "dist",
    "build",
    ".venv",
    "venv",
    "env",
    ".env",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    ".eggs",
    "eggs",
    ".cache",
    ".yarn",
    ".pnpm-store",
    "target",  # Rust build
    "bin",     # Some build outputs
    "obj",     # C# build
    ".gradle",
    ".idea",
    ".vscode",
    ".vs",
    ".DS_Store",
    "coverage",
    ".coverage",
    "htmlcov",
    ".serverless",
    ".terraform",
    "vendor",  # PHP (but could be real)
}

# File names that indicate sensitive content
SENSITIVE_FILE_NAMES: Set[str] = {
    ".env",
    ".env.local",
    ".env.production",
    ".env.development",
    "credentials.json",
    "credentials.yaml",
    "service-account.json",
    "id_rsa",
    "id_rsa.pub",
    ".npmrc",
    ".netrc",
    ".pgpass",
    "secret",
    "secrets",
}

# Extensions of files whose contents may be sensitive
SENSITIVE_EXTENSIONS: Set[str] = {
    ".pem",
    ".key",
    ".cert",
    ".p12",
    ".pfx",
    ".keystore",
    ".cred",
    ".credentials",
}


@dataclass
class ScannedFile:
    """Metadata about a single scanned file."""

    path: str  # Relative path from repo root
    name: str
    extension: str
    size_bytes: int
    is_binary: bool
    is_symlink: bool
    is_hidden: bool
    depth: int


@dataclass
class ScanResult:
    """Result of a repository scan."""

    files: List[ScannedFile] = field(default_factory=list)
    total_files: int = 0
    total_dirs: int = 0
    total_bytes: int = 0
    total_ignored: int = 0
    total_ignored_dirs: int = 0
    max_depth: int = 0
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    duration_seconds: float = 0.0


class RepositoryScanner:
    """Safe, configurable repository scanner."""

    def __init__(
        self,
        extra_ignored_dirs: Optional[Set[str]] = None,
        max_file_size: int = 10 * 1024 * 1024,  # 10 MB
        max_total_bytes: int = 500 * 1024 * 1024,  # 500 MB
        max_files: int = 100_000,
        max_depth: int = 50,
        follow_symlinks: bool = False,
        respect_gitignore: bool = True,
    ) -> None:
        self.ignored_dirs = DEFAULT_IGNORED_DIRS | (extra_ignored_dirs or set())
        self.max_file_size = max_file_size
        self.max_total_bytes = max_total_bytes
        self.max_files = max_files
        self.max_depth = max_depth
        self.follow_symlinks = follow_symlinks
        self.respect_gitignore = respect_gitignore

        # Track symlinks to detect loops
        self._visited_symlinks: Set[str] = set()

    def scan(self, root_path: str) -> ScanResult:
        """Scan a repository at the given path.

        Args:
            root_path: Absolute path to the repository root.

        Returns:
            ScanResult with all scanned files and statistics.
        """
        start_time = time.time()
        result = ScanResult()
        root = Path(root_path).resolve()

        if not root.is_dir():
            result.errors.append(f"Path does not exist or is not a directory: {root_path}")
            return result

        # Load .gitignore patterns if requested
        gitignore_patterns = self._load_gitignore(root) if self.respect_gitignore else set()

        try:
            self._scan_directory(root, root, "", 0, result, gitignore_patterns)
        except Exception as exc:
            result.errors.append(f"Scan failed: {exc}")
            logger.error("Repository scan failed for %s: %s", root_path, exc)

        result.duration_seconds = round(time.time() - start_time, 3)
        return result

    def _scan_directory(
        self,
        root: Path,
        current: Path,
        rel_path: str,
        depth: int,
        result: ScanResult,
        gitignore_patterns: Set[str],
    ) -> None:
        """Recursively scan a directory."""
        if depth > self.max_depth:
            result.warnings.append(f"Max depth ({self.max_depth}) reached at {rel_path}")
            return

        try:
            entries = sorted(current.iterdir(), key=lambda e: e.name)
        except PermissionError:
            result.errors.append(f"Permission denied: {rel_path}")
            return
        except OSError as exc:
            result.errors.append(f"Cannot read directory {rel_path}: {exc}")
            return

        for entry in entries:
            name = entry.name
            entry_rel = os.path.join(rel_path, name) if rel_path else name

            # Skip if total file limit exceeded
            if result.total_files >= self.max_files:
                result.warnings.append(f"Max file limit ({self.max_files}) reached, truncating scan")
                return
            # Skip if total byte limit exceeded
            if result.total_bytes >= self.max_total_bytes:
                result.warnings.append(f"Max byte limit reached, truncating scan")
                return

            # Check gitignore
            if self._is_gitignored(entry_rel, gitignore_patterns):
                if entry.is_dir():
                    result.total_ignored_dirs += 1
                else:
                    result.total_ignored += 1
                continue

            # Handle symlinks
            if entry.is_symlink():
                if not self.follow_symlinks:
                    result.total_ignored += 1
                    continue
                real_path = str(entry.resolve())
                if real_path in self._visited_symlinks:
                    result.warnings.append(f"Symlink loop detected: {entry_rel}")
                    result.total_ignored += 1
                    continue
                self._visited_symlinks.add(real_path)

            # Skip ignored directory names
            if entry.is_dir() or entry.is_symlink():
                if name in self.ignored_dirs:
                    result.total_ignored_dirs += 1
                    continue

                result.total_dirs += 1
                self._scan_directory(root, entry, entry_rel, depth + 1, result, gitignore_patterns)
                continue

            # It's a file
            result.total_files += 1

            try:
                stat_info = entry.stat()
            except OSError:
                result.errors.append(f"Cannot stat file: {entry_rel}")
                result.total_ignored += 1
                continue

            size = stat_info.st_size

            # Skip large files
            if size > self.max_file_size:
                result.warnings.append(f"Large file skipped ({size} bytes): {entry_rel}")
                result.total_ignored += 1
                continue

            ext = os.path.splitext(name)[1].lower()
            is_binary = self._is_binary(ext)
            is_hidden = name.startswith(".")
            is_symlink = entry.is_symlink()
            result.total_bytes += size

            result.max_depth = max(result.max_depth, depth)
            scanned = ScannedFile(
                path=entry_rel.replace("\\", "/"),
                name=name,
                extension=ext,
                size_bytes=size,
                is_binary=is_binary,
                is_symlink=is_symlink,
                is_hidden=is_hidden,
                depth=depth,
            )
            result.files.append(scanned)

    def _load_gitignore(self, root: Path) -> Set[str]:
        """Load .gitignore patterns (simplified — only directory patterns)."""
        gitignore_path = root / ".gitignore"
        patterns: Set[str] = set()
        if gitignore_path.exists():
            try:
                for line in gitignore_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        # Strip leading / or trailing /
                        pattern = line.lstrip("/").rstrip("/")
                        if pattern:
                            patterns.add(pattern)
            except Exception:
                pass  # Silently ignore malformed .gitignore
        return patterns

    def _is_gitignored(self, rel_path: str, patterns: Set[str]) -> bool:
        """Check simplified gitignore matching (directory-level)."""
        if not patterns:
            return False
        parts = rel_path.replace("\\", "/").split("/")
        for part in parts:
            if part in patterns:
                return True
        return False

    @staticmethod
    def _is_binary(ext: str) -> bool:
        """Check if a file extension indicates binary content."""
        return ext in {
            ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp",
            ".woff", ".woff2", ".ttf", ".eot", ".otf",
            ".mp4", ".avi", ".mov", ".mkv", ".webm",
            ".mp3", ".wav", ".ogg", ".flac", ".aac",
            ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
            ".zip", ".tar", ".gz", ".bz2", ".rar", ".7z", ".zst",
            ".exe", ".dll", ".so", ".dylib", ".class", ".jar", ".war",
            ".pyc", ".pyo", ".pyd",
            ".DS_Store",
            ".o", ".obj", ".a", ".lib",
            ".wasm",
            ".ttf", ".otf",
        }

    @staticmethod
    def is_sensitive_file(file_name: str, rel_path: str) -> bool:
        """Check if a file likely contains sensitive information.

        Returns True without reading file contents.
        """
        if file_name in SENSITIVE_FILE_NAMES:
            return True
        ext = os.path.splitext(file_name)[1].lower()
        if ext in SENSITIVE_EXTENSIONS:
            return True
        # Check if path contains 'secret' or 'credential'
        path_lower = rel_path.lower()
        if "/secret" in path_lower or "credential" in path_lower:
            return True
        return False
