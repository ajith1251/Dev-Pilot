"""
Incremental Indexer — supports partial re-indexing for Phase 12.

Only re-parses files that have changed, removing stale symbols/edges
and inserting new ones without a full graph rebuild.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple

from app.code_intelligence.parsers.python_parser import PythonSymbolParser
from app.code_intelligence.parsers.ts_parser import TypeScriptJSParser
from app.code_intelligence.semantic_graph import (
    ConfidenceLevel,
    GraphNode,
    RelationshipType,
    SemanticRepositoryGraph,
    make_symbol_id,
)


class FileChangeType(str, Enum):
    """Type of change to a file."""

    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    UNCHANGED = "unchanged"


@dataclass
class FileChange:
    """A detected file change."""

    file_path: str
    change_type: FileChangeType
    old_hash: Optional[str] = None
    new_hash: Optional[str] = None


@dataclass
class IncrementalResult:
    """Result of an incremental index operation."""

    indexed: int = 0
    updated: int = 0
    unchanged: int = 0
    removed: int = 0
    failed: int = 0
    files: Dict[str, FileChangeType] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)


class IncrementalIndexer:
    """Supports incremental re-indexing by tracking file hashes.

    On each index operation, compares current file hashes against
    stored hashes to determine which files need re-parsing.
    """

    def __init__(self) -> None:
        self._file_hashes: Dict[str, str] = {}  # file_path -> content hash

    @staticmethod
    def _parse_file(
        file_path: str, content: str, language: str
    ) -> Tuple[List[GraphNode], List[dict], List[str]]:
        """Parse a source file and extract symbols and relationships."""
        lang_lower = language.lower()
        if "python" in lang_lower:
            parser = PythonSymbolParser(file_path, content)
            return parser.parse()
        elif "typescript" in lang_lower or "javascript" in lang_lower:
            parser = TypeScriptJSParser(file_path, content)
            return parser.parse()
        return [], [], [f"Unsupported language: {language}"]

    def get_file_hash(self, file_path: str) -> Optional[str]:
        return self._file_hashes.get(file_path)

    def set_file_hash(self, file_path: str, content_hash: str) -> None:
        self._file_hashes[file_path] = content_hash

    # ── Change Detection ─────────────────────────────────────────

    def detect_changes(
        self,
        repo_path: str,
        known_files: Optional[List[str]] = None,
    ) -> List[FileChange]:
        """Detect which files have changed since last indexing.

        Args:
            repo_path: Root path of the repository.
            known_files: Previously indexed file list. If None, uses stored hashes.

        Returns:
            List of FileChange objects.
        """
        changes: List[FileChange] = []
        current_files: Dict[str, str] = {}

        # Discover current files
        for dirpath, dirnames, filenames in os.walk(repo_path):
            # Skip hidden and node_modules
            dirnames[:] = [d for d in dirnames
                          if not d.startswith(".") and d != "node_modules"]
            for filename in filenames:
                rel_path = os.path.relpath(
                    os.path.join(dirpath, filename), repo_path
                ).replace("\\", "/")
                full_path = os.path.join(dirpath, filename)
                try:
                    with open(full_path, "rb") as f:
                        content_hash = hashlib.sha256(f.read()).hexdigest()[:32]
                    current_files[rel_path] = content_hash
                except Exception:
                    pass

        # Compare with stored hashes
        for fp, new_hash in current_files.items():
            old_hash = self._file_hashes.get(fp)
            if old_hash is None:
                changes.append(FileChange(
                    file_path=fp, change_type=FileChangeType.ADDED,
                    new_hash=new_hash,
                ))
            elif old_hash != new_hash:
                changes.append(FileChange(
                    file_path=fp, change_type=FileChangeType.MODIFIED,
                    old_hash=old_hash, new_hash=new_hash,
                ))
            else:
                changes.append(FileChange(
                    file_path=fp, change_type=FileChangeType.UNCHANGED,
                    old_hash=old_hash, new_hash=new_hash,
                ))

        # Detect deletions
        for fp in self._file_hashes:
            if fp not in current_files:
                changes.append(FileChange(
                    file_path=fp, change_type=FileChangeType.DELETED,
                    old_hash=self._file_hashes[fp],
                ))

        return changes

    # ── Incremental Update ───────────────────────────────────────

    def update_graph(
        self,
        graph: SemanticRepositoryGraph,
        repo_path: str,
        changes: List[FileChange],
    ) -> IncrementalResult:
        """Update the graph incrementally based on detected changes.

        Args:
            graph: The semantic graph to update.
            repo_path: Root path of the repository.
            changes: List of detected file changes.

        Returns:
            IncrementalResult with statistics.
        """
        result = IncrementalResult()

        for change in changes:
            full_path = os.path.join(repo_path, change.file_path)

            if change.change_type == FileChangeType.UNCHANGED:
                result.unchanged += 1
                result.files[change.file_path] = FileChangeType.UNCHANGED
                continue

            if change.change_type == FileChangeType.DELETED:
                # Remove all symbols from this file
                symbols = graph.symbols_in_file(change.file_path)
                for sym in symbols:
                    graph.remove_node(sym.id)
                self._file_hashes.pop(change.file_path, None)
                result.removed += 1
                result.files[change.file_path] = FileChangeType.DELETED
                continue

            # ADDED or MODIFIED — reparse
            try:
                # Detect language from extension
                ext = os.path.splitext(change.file_path)[1].lower()
                lang_map = {
                    ".py": "Python", ".ts": "TypeScript", ".tsx": "TypeScript",
                    ".js": "JavaScript", ".jsx": "JavaScript",
                }
                language = lang_map.get(ext)
                if not language:
                    result.failed += 1
                    result.warnings.append(f"Unsupported file: {change.file_path}")
                    continue

                with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()

                # Remove old symbols
                if change.change_type == FileChangeType.MODIFIED:
                    old_symbols = graph.symbols_in_file(change.file_path)
                    for sym in old_symbols:
                        graph.remove_node(sym.id)

                # Parse new content
                symbols, relationships, diagnostics = self._parse_file(
                    change.file_path, content, language
                )

                if diagnostics:
                    result.warnings.extend(
                        f"{change.file_path}: {d}" for d in diagnostics
                    )
                    result.failed += 1

                # Add new symbols
                for sym in symbols:
                    graph.add_node(sym)

                # Add new relationships
                for rel in relationships:
                    try:
                        graph.add_edge(
                            source_id=rel["source_id"],
                            target_id=rel["target_id"],
                            relationship=RelationshipType(rel["relationship"]),
                            confidence=ConfidenceLevel(rel.get("confidence", "medium")),
                            source_lines=rel.get("source_lines"),
                            resolution_detail=rel.get("resolution_detail"),
                        )
                    except ValueError:
                        pass

                # Update hash
                self._file_hashes[change.file_path] = change.new_hash or ""

                if change.change_type == FileChangeType.ADDED:
                    result.indexed += 1
                    result.files[change.file_path] = FileChangeType.ADDED
                else:
                    result.updated += 1
                    result.files[change.file_path] = FileChangeType.MODIFIED

            except Exception as exc:
                result.failed += 1
                result.warnings.append(f"Error re-indexing {change.file_path}: {exc}")

        return result
