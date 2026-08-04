"""
Tree Generator — create compact, deterministic repository tree representations.

Supports both structured and text-based tree output suitable for agent context.
"""

from __future__ import annotations

import os
from typing import List, Set

from app.models.profile import RepositoryTree
from app.services.repository_scanner import ScannedFile


class TreeGenerator:
    """Generate compact repository tree representations."""

    # Directories to always collapse in compact mode
    COLLAPSIBLE_DIRS: Set[str] = {
        "__pycache__",
        "node_modules",
        ".git",
        ".next",
        ".venv",
        "venv",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
    }

    # File extensions to show in compact mode
    # (others are collapsed into counts)
    IMPORTANT_EXTENSIONS: Set[str] = {
        ".py", ".js", ".jsx", ".ts", ".tsx",
        ".json", ".yaml", ".yml", ".toml",
        ".md", ".rst",
        ".html", ".css", ".scss",
        ".go", ".rs", ".java", ".cs",
        ".sh", ".ps1",
        ".sql",
        ".vue", ".svelte",
    }

    def generate(
        self,
        files: List[ScannedFile],
        root_name: str = ".",
        max_depth: int = 5,
        max_files_per_dir: int = 15,
    ) -> RepositoryTree:
        """Generate a compact text-based repository tree.

        Args:
            files: List of scanned files.
            root_name: Name of the repository root.
            max_depth: Maximum directory depth to show.
            max_files_per_dir: Maximum files to show per directory.

        Returns:
            RepositoryTree with compact text representation.
        """
        # Build tree structure from file paths
        tree: dict = {}
        total_shown = 0
        max_actual_depth = 0

        for f in files:
            parts = f.path.replace("\\", "/").split("/")
            depth = len(parts)

            # Skip if beyond max depth
            if len(parts) > max_depth:
                continue

            current = tree
            for i, part in enumerate(parts):
                if i == len(parts) - 1:
                    # It's a file
                    if "files" not in current:
                        current["files"] = []
                    if f.is_binary or f.extension not in self.IMPORTANT_EXTENSIONS:
                        continue  # Skip binary and non-important files
                    current["files"].append(f.name)
                else:
                    # It's a directory
                    if "dirs" not in current:
                        current["dirs"] = {}
                    if part not in current["dirs"]:
                        current["dirs"][part] = {}
                    current = current["dirs"][part]

        # Build text representation
        lines: List[str] = [f"{root_name}/"]
        self._build_text(tree, lines, "", max_files_per_dir)

        text = "\n".join(lines)
        dir_count = sum(1 for l in lines if l.rstrip().endswith("/"))
        file_count = sum(1 for l in lines if not l.rstrip().endswith("/") and not l.strip().startswith("("))

        return RepositoryTree(
            text=text,
            max_depth=max_depth,
            total_dirs_shown=dir_count,
            total_files_shown=file_count,
        )

    def _build_text(
        self,
        node: dict,
        lines: List[str],
        prefix: str,
        max_files: int,
    ) -> None:
        """Recursively build tree text representation."""
        dirs = node.get("dirs", {})
        files = node.get("files", [])

        # Sort directories and files
        sorted_dirs = sorted(dirs.keys())
        sorted_files = sorted(files)

        all_items = []
        for d in sorted_dirs:
            all_items.append(("dir", d))
        for f in sorted_files[:max_files]:
            all_items.append(("file", f))

        if len(sorted_files) > max_files:
            all_items.append(("info", f"... ({len(sorted_files)} files)"))

        for i, (item_type, item_name) in enumerate(all_items):
            is_last = i == len(all_items) - 1
            connector = "└── " if is_last else "├── "
            new_prefix = "    " if is_last else "│   "

            if item_type == "dir":
                lines.append(f"{prefix}{connector}{item_name}/")
                self._build_text(dirs[item_name], lines, prefix + new_prefix, max_files)
            elif item_type == "file":
                lines.append(f"{prefix}{connector}{item_name}")
            elif item_type == "info":
                lines.append(f"{prefix}{connector}{item_name}")
