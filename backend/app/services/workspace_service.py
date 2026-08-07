"""
Workspace Service — Phase 6

Creates safe isolated writable copies of source repositories for patch application.

- Copies source repository to a temporary workspace
- Preserves cleanup behavior
- Never modifies original source
- Path-safety validated throughout
"""

import hashlib
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Optional, Set
from uuid import uuid4

from app.core.exceptions import WorkspaceError


class CodingWorkspace:
    """A controlled writable workspace isolated from the source repository."""

    def __init__(
        self,
        workspace_id: str,
        source_repository: str,
        root_path: str,
        writable: bool = True,
    ):
        self.workspace_id = workspace_id
        self.source_repository = source_repository
        self.root_path = root_path
        self.writable = writable
        self.created_at = time.time()

    @property
    def root(self) -> Path:
        return Path(self.root_path).resolve()

    def to_dict(self) -> dict:
        return {
            "workspace_id": self.workspace_id,
            "source_repository": self.source_repository,
            "root_path": self.root_path,
            "writable": self.writable,
            "created_at": self.created_at,
        }


class WorkspaceService:
    """Manages lifecycle of isolated writable coding workspaces.

    - Creates safe copies of source repositories
    - Never modifies original sources
    - Provides cleanup
    - Validates paths throughout
    - Optionally persists workspace metadata to a RunStore for cross-session recovery

    When a run_store is provided, workspace registrations are written to the
    database so that get_workspace() can find workspaces from a previous session.
    """

    # Files/directories to exclude from workspace copy
    EXCLUDED_PATTERNS: Set[str] = {
        ".git",
        ".gitattributes",
        ".gitignore",
        ".env",
        ".env.local",
        ".env.production",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".eggs",
        ".DS_Store",
        "*.pyc",
    }

    def __init__(
        self,
        base_dir: Optional[str] = None,
        run_store: Optional[Any] = None,
    ):
        self._base_dir = base_dir
        self._run_store = run_store

    def create_workspace(
        self,
        source_path: str,
        workspace_id: Optional[str] = None,
    ) -> CodingWorkspace:
        """Create an isolated writable workspace from a source repository.

        1. Validates source path exists and is safe
        2. Creates a temporary workspace directory
        3. Copies source files (excluding sensitive/system dirs)
        4. Returns a CodingWorkspace with a unique ID
        """
        source = Path(source_path).resolve()

        if not source.exists():
            raise WorkspaceError(f"Source repository does not exist: {source_path}")
        if not source.is_dir():
            raise WorkspaceError(f"Source is not a directory: {source_path}")

        wid = workspace_id or f"ws-{uuid4().hex[:12]}"

        # Determine workspace root
        if self._base_dir:
            ws_root = Path(self._base_dir).resolve() / wid
        else:
            ws_root = Path(tempfile.mkdtemp(prefix=f"devpilot_{wid}_"))

        ws_root.mkdir(parents=True, exist_ok=True)

        # Copy source to workspace
        self._copy_source(source, ws_root)

        workspace = CodingWorkspace(
            workspace_id=wid,
            source_repository=str(source),
            root_path=str(ws_root),
            writable=True,
        )

        # Persist workspace metadata if a run_store is configured
        if self._run_store is not None and hasattr(self._run_store, "save_workspace"):
            import asyncio
            try:
                loop = asyncio.get_running_loop()
                asyncio.run_coroutine_threadsafe(
                    self._run_store.save_workspace(
                        workspace_id=wid,
                        root_path=str(ws_root),
                        source_repository=str(source),
                        writable=True,
                        workspace_type="coding",
                    ),
                    loop,
                )
            except Exception:
                pass  # Non-critical; workspace still works in-memory

        return workspace

    def cleanup_workspace(self, workspace: CodingWorkspace) -> None:
        """Remove a workspace directory and all its contents."""
        root = workspace.root
        if root.exists() and root.is_dir():
            try:
                shutil.rmtree(root, ignore_errors=True)
            except (OSError, PermissionError) as exc:
                raise WorkspaceError(
                    f"Failed to clean up workspace {workspace.workspace_id}: {exc}"
                )

    def cleanup_stale_workspaces(self, max_age_seconds: float = 86400.0) -> int:
        """Remove abandoned workspace directories older than ``max_age_seconds``.

        Phase 20B resource management: a crashed process can leave
        ``devpilot_ws-*_`` temp directories behind; this scans the configured
        base directory (or the system temp dir) for stale ones and removes
        them. Returns the number of directories removed. Never raises —
        unremovable entries are skipped.
        """
        base = Path(self._base_dir) if self._base_dir else Path(tempfile.gettempdir())
        removed = 0
        if not base.is_dir():
            return 0
        cutoff = time.time() - max(0.0, float(max_age_seconds))
        for candidate in base.glob("devpilot_*"):
            if not candidate.is_dir():
                continue
            try:
                if candidate.stat().st_mtime <= cutoff:
                    shutil.rmtree(candidate, ignore_errors=True)
                    if not candidate.exists():
                        removed += 1
            except OSError:
                continue
        return removed

    def _copy_source(self, source: Path, dest: Path) -> None:
        """Copy source repository to destination, excluding sensitive files."""
        try:
            shutil.copytree(
                str(source),
                str(dest),
                symlinks=False,
                ignore=self._make_ignore_filter(),
                dirs_exist_ok=True,
            )
        except shutil.Error as exc:
            raise WorkspaceError(f"Failed to copy source repository: {exc}")

    def _make_ignore_filter(self):
        """Create a shutil.ignore_patterns filter for excluded patterns."""
        return shutil.ignore_patterns(*self.EXCLUDED_PATTERNS)

    def get_workspace(self, workspace_id: str) -> Optional[CodingWorkspace]:
        """Retrieve a workspace by ID (from active registry).

        When a persistent RunStore is available, queries the database
        for workspaces created in a previous session.

        Note: The workspace may still point to a valid filesystem path
        even if the process was restarted, allowing reuse.

        Returns None if the workspace is not found or the path no longer exists.
        """
        # Check persistent store first (for cross-session recovery)
        if self._run_store is not None and hasattr(self._run_store, "get_workspace"):
            import asyncio
            try:
                loop = asyncio.get_running_loop()
                future = asyncio.run_coroutine_threadsafe(
                    self._run_store.get_workspace(workspace_id),
                    loop,
                )
                result = future.result(timeout=5)
                if result:
                    # Verify the workspace path still exists on disk
                    root = Path(result["root_path"])
                    if root.exists() and root.is_dir():
                        return CodingWorkspace(
                            workspace_id=result["workspace_id"],
                            source_repository=result.get("source_repository", "") or "",
                            root_path=result["root_path"],
                            writable=result.get("writable", True),
                        )
            except Exception:
                pass

        return None

    def fingerprint_source(self, source_path: str) -> str:
        """Compute a deterministic fingerprint of a source repository.

        Uses sorted list of file paths and their sizes/hashes to detect changes.
        """
        source = Path(source_path).resolve()
        if not source.is_dir():
            return ""

        hasher = hashlib.sha256()

        # Collect all files, sorted for determinism
        files = sorted(
            p.relative_to(source)
            for p in source.rglob("*")
            if p.is_file()
            and not any(part.startswith(".") for part in p.relative_to(source).parts)
        )

        for rel_path in files:
            hasher.update(str(rel_path).encode("utf-8"))
            try:
                stat = (source / rel_path).stat()
                hasher.update(str(stat.st_size).encode("utf-8"))
                hasher.update(str(stat.st_mtime).encode("utf-8"))
            except OSError:
                pass

        return hasher.hexdigest()[:16]
