"""
Safe Patch Engine — Phase 6

Deterministic file mutation engine with:
- Dry-run mode
- Unified diff generation (std lib only)
- Atomic file writes
- Pre-apply snapshots with rollback
- Content hash verification
- Size limits
- No shell execution, no repository code execution
"""

import difflib
import hashlib
import os
import shutil
from pathlib import Path
from typing import Dict, List, Optional

from app.core.exceptions import (
    PatchApplicationError,
    PatchRollbackError,
)
from app.models.coding import (
    FileChange,
    FileOperation,
    PatchApplicationResult,
    PatchSet,
    PatchStatus,
    PatchValidationResult,
)
from app.services.patch_validator import PatchValidator


class SafePatchEngine:
    """Deterministic safe patch engine for Phase 6.

    Applies validated FileChange operations to a writable workspace.
    Supports dry-run, diff generation, rollback, and atomic writes.
    Never executes repository code or shell commands.
    """

    def __init__(
        self,
        workspace_root: str,
        validator: Optional[PatchValidator] = None,
        max_file_size: int = 500_000,
    ):
        self._workspace_root = Path(workspace_root).resolve()
        self._validator = validator or PatchValidator(
            workspace_root=str(self._workspace_root)
        )
        self._max_file_size = max_file_size

    # ── Public API ──────────────────────────────────────────────────────────

    def dry_run(self, patch_set: PatchSet) -> PatchApplicationResult:
        """Validate and simulate a patch without modifying any files."""
        validation = self._validator.validate_with_workspace(
            patch_set, str(self._workspace_root)
        )
        if not validation.is_valid:
            return self._make_result(
                patch_set.patch_id,
                PatchStatus.REJECTED,
                dry_run=True,
                errors=validation.errors,
                warnings=validation.warnings,
            )

        # Generate diffs without touching files
        diffs = []
        for change in patch_set.changes:
            diff = self._generate_diff(change, simulate=True)
            if diff:
                diffs.append(diff)

        return self._make_result(
            patch_set.patch_id,
            PatchStatus.DRY_RUN,
            dry_run=True,
            changes_attempted=len(patch_set.changes),
            diff="\n".join(diffs) if diffs else "",
            warnings=validation.warnings,
        )

    def apply(self, patch_set: PatchSet) -> PatchApplicationResult:
        """Validate and apply a patch to the workspace.

        1. Validate the patch against actual workspace state
        2. Snapshot affected files
        3. Apply each change transactionally
        4. Rollback entirely on any failure
        5. Return structured result
        """
        # Step 1: Validate against actual workspace
        validation = self._validator.validate_with_workspace(
            patch_set, str(self._workspace_root)
        )
        if not validation.is_valid:
            return self._make_result(
                patch_set.patch_id,
                PatchStatus.REJECTED,
                errors=validation.errors,
                warnings=validation.warnings,
            )

        # Step 2: Take snapshot before any changes
        snapshot = self._take_snapshot(patch_set)

        # Step 3: Apply changes sequentially
        applied: List[str] = []
        files_created: List[str] = []
        files_modified: List[str] = []
        files_deleted: List[str] = []
        errors: List[str] = []
        diffs: List[str] = []

        try:
            for change in patch_set.changes:
                diff = self._apply_single_change(change)
                if diff:
                    diffs.append(diff)
                applied.append(change.change_id)
                if change.operation == FileOperation.CREATE:
                    files_created.append(change.path)
                elif change.operation == FileOperation.MODIFY:
                    files_modified.append(change.path)
                elif change.operation == FileOperation.DELETE:
                    files_deleted.append(change.path)

        except (PatchApplicationError, OSError, ValueError) as exc:
            self._rollback(snapshot)
            return self._make_result(
                patch_set.patch_id,
                PatchStatus.ROLLED_BACK,
                changes_attempted=len(patch_set.changes),
                changes_applied=len(applied),
                files_created=files_created,
                files_modified=files_modified,
                files_deleted=files_deleted,
                diff="\n".join(diffs) if diffs else "",
                errors=[str(exc)],
                rolled_back=True,
                warnings=validation.warnings,
            )

        return self._make_result(
            patch_set.patch_id,
            PatchStatus.APPLIED,
            dry_run=False,
            changes_attempted=len(patch_set.changes),
            changes_applied=len(applied),
            files_created=files_created,
            files_modified=files_modified,
            files_deleted=files_deleted,
            diff="\n".join(diffs) if diffs else "",
            warnings=validation.warnings,
        )

    # ── Internal: Single Change Application ─────────────────────────────────

    def _apply_single_change(self, change: FileChange) -> Optional[str]:
        """Apply a single validated FileChange. Returns a diff string."""
        target = (self._workspace_root / change.path).resolve()
        if not self._is_safe_path(target):
            raise PatchApplicationError(f"Path traversal detected: {change.path}")

        if change.operation == FileOperation.CREATE:
            return self._apply_create(target, change)
        elif change.operation == FileOperation.MODIFY:
            return self._apply_modify(target, change)
        elif change.operation == FileOperation.DELETE:
            return self._apply_delete(target, change)
        else:
            raise PatchApplicationError(f"Unsupported operation: {change.operation}")

    def _apply_create(self, target: Path, change: FileChange) -> Optional[str]:
        """Create a new file atomically."""
        if not change.new_content:
            raise PatchApplicationError(f"CREATE {change.path} requires new_content")
        target.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(target, change.new_content)
        return self._generate_diff_for_content(None, change.new_content, change.path)

    def _apply_modify(self, target: Path, change: FileChange) -> Optional[str]:
        """Modify an existing file atomically with hash verification."""
        if not change.new_content:
            raise PatchApplicationError(f"MODIFY {change.path} requires new_content")
        if not target.exists():
            raise PatchApplicationError(f"MODIFY target does not exist: {change.path}")

        try:
            current_content = target.read_bytes()
        except (OSError, PermissionError) as exc:
            raise PatchApplicationError(f"Cannot read {change.path}: {exc}")

        if change.original_hash:
            current_hash = hashlib.sha256(current_content).hexdigest()
            if current_hash != change.original_hash:
                raise PatchApplicationError(
                    f"Content hash mismatch for {change.path}: "
                    f"expected {change.original_hash}, got {current_hash}"
                )

        self._atomic_write(target, change.new_content)
        return self._generate_diff_for_content(
            current_content.decode("utf-8", errors="replace"),
            change.new_content,
            change.path,
        )

    def _apply_delete(self, target: Path, change: FileChange) -> Optional[str]:
        """Delete an existing file."""
        if not target.exists():
            raise PatchApplicationError(f"DELETE target does not exist: {change.path}")

        # Hash verification
        if change.original_hash:
            try:
                current_content = target.read_bytes()
                current_hash = hashlib.sha256(current_content).hexdigest()
                if current_hash != change.original_hash:
                    raise PatchApplicationError(
                        f"Content hash mismatch for DELETE {change.path}: "
                        f"expected {change.original_hash}, got {current_hash}"
                    )
            except (OSError, PermissionError) as exc:
                raise PatchApplicationError(
                    f"Cannot read {change.path} for DELETE verification: {exc}"
                )

        try:
            old_content = target.read_text("utf-8", errors="replace")
        except Exception:
            old_content = None

        target.unlink()
        return self._generate_diff_for_content(old_content, None, change.path)

    # ── Atomic Writes ───────────────────────────────────────────────────────

    def _atomic_write(self, target: Path, content: str) -> None:
        """Write content atomically: temp file -> rename."""
        content_bytes = content.encode("utf-8")
        if len(content_bytes) > self._max_file_size:
            raise PatchApplicationError(
                f"Content exceeds max file size "
                f"({len(content_bytes)} > {self._max_file_size})"
            )

        # Preserve CRLF if existing file uses it
        if target.exists():
            try:
                existing = target.read_bytes()
                if b"\r\n" in existing[:8192]:
                    content_bytes = (
                        content.replace("\r\n", "\n")
                        .replace("\n", "\r\n")
                        .encode("utf-8")
                    )
            except Exception:
                pass

        tmp_path = target.parent / f".{target.name}.devpilot_tmp"
        try:
            with open(tmp_path, "wb") as f:
                f.write(content_bytes)
                f.flush()
                os.fsync(f.fileno())
            shutil.move(str(tmp_path), str(target))
        except (OSError, shutil.Error) as exc:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            raise PatchApplicationError(f"Atomic write failed for {target}: {exc}")

    # ── Public: Rollback ───────────────────────────────────────────────────

    def rollback(self, snapshot: Dict[str, Optional[bytes]]) -> None:
        """Public rollback — restore files from a previously taken snapshot.

        Args:
            snapshot: Dictionary mapping relative paths to original byte content.
                      Files with None original_content will be deleted.
        """
        self._rollback(snapshot)

    def snapshot(self, patch_set: PatchSet) -> Dict[str, Optional[bytes]]:
        """Public snapshot — capture pre-apply state of affected files.

        Args:
            patch_set: The patch whose changes should be snapshotted.

        Returns:
            Dict mapping relative file paths to original byte content.
        """
        return self._take_snapshot(patch_set)

    # ── Snapshots & Rollback ───────────────────────────────────────────────

    def _take_snapshot(self, patch_set: PatchSet) -> Dict[str, Optional[bytes]]:
        """Capture pre-apply state of all affected files."""
        snapshot: Dict[str, Optional[bytes]] = {}
        for change in patch_set.changes:
            target = (self._workspace_root / change.path).resolve()
            if not self._is_safe_path(target):
                continue
            if target.exists():
                try:
                    snapshot[change.path] = target.read_bytes()
                except (OSError, PermissionError) as exc:
                    raise PatchApplicationError(
                        f"Cannot snapshot {change.path}: {exc}"
                    )
            else:
                snapshot[change.path] = None
        return snapshot

    def _rollback(self, snapshot: Dict[str, Optional[bytes]]) -> None:
        """Restore all files to pre-apply state from snapshot."""
        errors: List[str] = []
        for rel_path, original_content in snapshot.items():
            target = (self._workspace_root / rel_path).resolve()
            if not self._is_safe_path(target):
                errors.append(f"Rollback blocked unsafe path: {rel_path}")
                continue
            try:
                if original_content is not None:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(original_content)
                else:
                    if target.exists():
                        target.unlink()
            except (OSError, PermissionError) as exc:
                errors.append(f"Rollback failed for {rel_path}: {exc}")

        if errors:
            raise PatchRollbackError(
                f"Rollback completed with errors: {'; '.join(errors)}",
                details={"errors": errors, "snapshot_keys": list(snapshot.keys())},
            )

    # ── Diff Generation ────────────────────────────────────────────────────

    def _generate_diff(
        self, change: FileChange, simulate: bool = False
    ) -> Optional[str]:
        """Generate a unified diff for a single change."""
        target = (self._workspace_root / change.path).resolve()
        old_content = None
        if simulate and target.exists():
            try:
                old_content = target.read_text("utf-8", errors="replace")
            except Exception:
                old_content = None
        return self._generate_diff_for_content(
            old_content, change.new_content, change.path
        )

    def _generate_diff_for_content(
        self,
        old_content: Optional[str],
        new_content: Optional[str],
        rel_path: str,
    ) -> Optional[str]:
        """Generate a unified diff string from old/new content."""
        old_lines = []
        new_lines = []
        old_header = f"a/{rel_path}" if old_content is not None else "/dev/null"
        new_header = f"b/{rel_path}" if new_content is not None else "/dev/null"

        if old_content is not None:
            old_lines = old_content.splitlines(keepends=True)
        if new_content is not None:
            new_lines = new_content.splitlines(keepends=True)

        diff_lines = list(
            difflib.unified_diff(
                old_lines, new_lines, fromfile=old_header, tofile=new_header,
            )
        )
        return "".join(diff_lines) if diff_lines else None

    # ── Path Safety ─────────────────────────────────────────────────────────

    def _is_safe_path(self, target: Path) -> bool:
        """Verify resolved target is inside the workspace root."""
        try:
            target.resolve().relative_to(self._workspace_root)
            return True
        except ValueError:
            return False

    # ── Result Builder ──────────────────────────────────────────────────────

    def _make_result(
        self,
        patch_id: str,
        status: PatchStatus,
        dry_run: bool = False,
        changes_attempted: int = 0,
        changes_applied: int = 0,
        files_created: Optional[List[str]] = None,
        files_modified: Optional[List[str]] = None,
        files_deleted: Optional[List[str]] = None,
        diff: str = "",
        errors: Optional[List[str]] = None,
        warnings: Optional[List[str]] = None,
        rolled_back: bool = False,
    ) -> PatchApplicationResult:
        return PatchApplicationResult(
            patch_id=patch_id,
            status=status,
            dry_run=dry_run,
            changes_attempted=changes_attempted,
            changes_applied=changes_applied,
            files_created=files_created or [],
            files_modified=files_modified or [],
            files_deleted=files_deleted or [],
            diff=diff,
            errors=errors or [],
            warnings=warnings or [],
            rolled_back=rolled_back,
        )
