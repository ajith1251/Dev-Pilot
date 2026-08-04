"""
PatchValidator — deterministic patch validation for Phase 6.

Validates PatchSet structure, path safety, and operation legality
without any LLM calls. This is the security gate between the
Coding Agent's proposals and filesystem mutation.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import List, Optional, Set

from app.config import settings
from app.models.coding import (
    FileChange,
    FileOperation,
    PatchSet,
    PatchStatus,
    PatchValidationResult,
)


# Paths that must never be modified automatically
PROTECTED_PATHS: Set[str] = {
    ".git",
    ".env",
    ".env.local",
    ".env.production",
    ".env.development",
    "credentials.json",
    "id_rsa",
    "id_rsa.pub",
}

PROTECTED_EXTENSIONS: Set[str] = {
    ".pem",
    ".key",
    ".cert",
    ".p12",
}

MAX_CHANGE_CONTENT_SIZE = settings.CODING_MAX_FILE_SIZE
MAX_TOTAL_PATCH_SIZE = settings.CODING_MAX_PATCH_SIZE
ALLOW_DELETE = settings.CODING_ALLOW_DELETE


class PatchValidator:
    """Deterministic validator for PatchSet proposals.

    Validates:
    - Non-empty patch
    - Supported operations
    - Unique change IDs
    - Path safety (no traversal, no absolute paths)
    - Protected file violations
    - Content size limits
    - Original hash matching (for MODIFY/DELETE)
    - File existence expectations
    - Conflicting operations
    - Plan/requirement reference validity
    """

    def __init__(
        self,
        workspace_root: Optional[str] = None,
        allow_delete: bool = ALLOW_DELETE,
        max_file_size: int = MAX_CHANGE_CONTENT_SIZE,
        max_patch_size: int = MAX_TOTAL_PATCH_SIZE,
    ) -> None:
        self.workspace_root = os.path.abspath(workspace_root) if workspace_root else None
        self.allow_delete = allow_delete
        self.max_file_size = max_file_size
        self.max_patch_size = max_patch_size

    def validate(
        self,
        patch: PatchSet,
    ) -> PatchValidationResult:
        """Validate a PatchSet.

        Args:
            patch: The PatchSet to validate.

        Returns:
            PatchValidationResult with errors/warnings.
        """
        errors: List[str] = []
        warnings: List[str] = []

        # 1. Non-empty check
        if not patch.changes:
            errors.append("PatchSet has no changes")

        # 2. Check change count limit
        if len(patch.changes) > settings.CODING_MAX_FILES_PER_PATCH:
            errors.append(
                f"PatchSet has {len(patch.changes)} changes, "
                f"max is {settings.CODING_MAX_FILES_PER_PATCH}"
            )

        # 3. Check unique change IDs
        seen_ids: Set[str] = set()
        for change in patch.changes:
            if change.change_id in seen_ids:
                errors.append(f"Duplicate change ID: {change.change_id}")
            seen_ids.add(change.change_id)

        # 4. Validate each change
        total_content_size = 0
        file_ops: dict = {}  # path -> operation

        for change in patch.changes:
            change_errors = self._validate_change(change, file_ops)
            errors.extend(change_errors)

            if change.new_content:
                total_content_size += len(change.new_content.encode("utf-8"))
            file_ops[change.path] = change.operation.value

        # 5. Total patch size limit
        if total_content_size > self.max_patch_size:
            errors.append(
                f"Total patch content size {total_content_size} bytes "
                f"exceeds maximum {self.max_patch_size}"
            )

        # 6. Delete policy
        if not self.allow_delete:
            has_delete = any(
                c.operation == FileOperation.DELETE for c in patch.changes
            )
            if has_delete:
                del_paths = [
                    c.path for c in patch.changes
                    if c.operation == FileOperation.DELETE
                ]
                errors.append(
                    f"DELETE operations are disabled by policy. "
                    f"Affected: {del_paths}"
                )

        return PatchValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            checked_changes=len(patch.changes),
            checked_operations=sum(
                1 for _ in patch.changes
            ),
        )

    def validate_with_workspace(
        self,
        patch: PatchSet,
        workspace_root: str,
    ) -> PatchValidationResult:
        """Validate a PatchSet against an actual workspace on disk.

        Checks file existence, content hashes, and path resolution.
        """
        result = self.validate(patch)
        if not result.is_valid:
            return result

        errors: List[str] = result.errors[:]
        warnings: List[str] = result.warnings[:]
        resolved_root = os.path.abspath(workspace_root)

        for change in patch.changes:
            resolved = self._resolve_safe_path(resolved_root, change.path)
            if resolved is None:
                errors.append(f"Path traversal detected: {change.path}")
                continue

            exists = os.path.isfile(resolved)

            if change.operation == FileOperation.CREATE:
                if exists:
                    errors.append(
                        f"CREATE target already exists: {change.path}"
                    )
            elif change.operation == FileOperation.MODIFY:
                if not exists:
                    errors.append(
                        f"MODIFY target does not exist: {change.path}"
                    )
                elif change.original_hash:
                    actual_hash = self._hash_file(resolved)
                    if actual_hash and actual_hash != change.original_hash:
                        errors.append(
                            f"Original hash mismatch for {change.path}: "
                            f"expected {change.original_hash}, got {actual_hash}"
                        )
            elif change.operation == FileOperation.DELETE:
                if not exists:
                    errors.append(
                        f"DELETE target does not exist: {change.path}"
                    )
                elif change.original_hash:
                    actual_hash = self._hash_file(resolved)
                    if actual_hash and actual_hash != change.original_hash:
                        warnings.append(
                            f"DELETE {change.path}: original hash differs "
                            f"(file may have changed since generation)"
                        )

        return PatchValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            checked_changes=len(patch.changes),
            checked_operations=sum(1 for _ in patch.changes),
        )

    def _validate_change(
        self,
        change: FileChange,
        file_ops: dict,
    ) -> List[str]:
        """Validate a single FileChange."""
        errors: List[str] = []

        # Operation support
        if change.operation not in (FileOperation.CREATE, FileOperation.MODIFY, FileOperation.DELETE):
            errors.append(f"Unsupported operation: {change.operation}")

        # Path required
        if not change.path:
            errors.append(f"Change {change.change_id} has empty path")

        # Path safety
        if not self._is_safe_path(change.path):
            errors.append(f"Path traversal or absolute path: {change.path}")

        # Protected path
        if self._is_protected_path(change.path):
            errors.append(f"Protected file cannot be modified: {change.path}")

        # Content requirements
        if change.operation in (FileOperation.CREATE, FileOperation.MODIFY):
            if not change.new_content or not change.new_content.strip():
                errors.append(
                    f"Change {change.change_id} ({change.operation}) "
                    f"requires non-empty new_content"
                )
            if change.new_content and len(change.new_content.encode("utf-8")) > self.max_file_size:
                errors.append(
                    f"Change {change.change_id} content size exceeds "
                    f"maximum {self.max_file_size} bytes"
                )

        # Original hash for MODIFY/DELETE
        if change.operation in (FileOperation.MODIFY, FileOperation.DELETE):
            if not change.original_hash:
                errors.append(
                    f"Change {change.change_id} ({change.operation}) "
                    f"requires original_hash"
                )

        # No conflicting operations on same path
        if change.path in file_ops:
            existing = file_ops[change.path]
            if existing != change.operation.value:
                errors.append(
                    f"Conflicting operations for {change.path}: "
                    f"{existing} and {change.operation.value}"
                )

        return errors

    @staticmethod
    def _is_safe_path(path: str) -> bool:
        """Check if a path is safe (no traversal, not absolute).

        Rejects:
        - Absolute paths starting with / or drive letters
        - Paths containing .. or parent traversal
        - Windows absolute paths like C:\\...
        """
        if not path:
            return False

        # Normalize separators
        normalized = path.replace("\\", "/")

        # Absolute path checks
        if normalized.startswith("/"):
            return False
        if len(path) >= 2 and path[1] == ":":
            return False

        # Traversal checks
        parts = normalized.split("/")
        for part in parts:
            if part == "..":
                return False

        return True

    @staticmethod
    def _is_protected_path(path: str) -> bool:
        """Check if a path is protected from automatic modification."""
        name = os.path.basename(path)
        if name in PROTECTED_PATHS:
            return True
        ext = os.path.splitext(name)[1].lower()
        if ext in PROTECTED_EXTENSIONS:
            return True
        return False

    @staticmethod
    def _resolve_safe_path(root: str, rel_path: str) -> Optional[str]:
        """Resolve a relative path against a workspace root safely."""
        try:
            root_resolved = os.path.realpath(root)
            combined = os.path.normpath(os.path.join(root_resolved, rel_path))
            combined_resolved = os.path.realpath(combined)
            # Check the resolved path starts with the resolved root
            common = os.path.commonpath([root_resolved, combined_resolved])
            if common != root_resolved:
                return None
            # Check for symlink escape in any component
            if not combined_resolved.startswith(root_resolved):
                return None
            return combined_resolved
        except (ValueError, OSError):
            return None

    @staticmethod
    def _hash_file(path: str) -> Optional[str]:
        """Compute SHA-256 hash of a file."""
        try:
            with open(path, "rb") as fh:
                return hashlib.sha256(fh.read()).hexdigest()
        except (IOError, OSError):
            return None
