"""
Repair Policy — Phase 8 deterministic safety validation.

Validates repair proposals against policy rules to prevent:
- Test tampering (deleting/skipping/weakening tests)
- Configuration weakening (disabling verification)
- Path safety violations (outside workspace, unsafe symlinks)
- Scope violations (too many files, oversized patches)
- Protected file mutations
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from app.models.coding import FileChange, FileOperation, PatchSet
from app.models.repair import RepairProposal, RepairProposalStatus


# ── Protected file patterns ─────────────────────────────────────

# Test files that should not be deleted or weakened
TEST_FILE_PATTERNS: List[str] = [
    r"tests?/",
    r"__tests?__/",
    r"test_.*\.py$",
    r".*_test\.py$",
    r".*\.test\.(ts|tsx|js|jsx)$",
    r".*\.spec\.(ts|tsx|js|jsx)$",
]

# Configuration files that should not be weakened
CONFIG_FILE_PATTERNS: List[str] = [
    r"pytest\.ini$",
    r"setup\.cfg$",
    r"pyproject\.toml$",
    r"tox\.ini$",
    r".coveragerc$",
    r"jest\.config.*\.(js|ts|mjs)$",
    r"vitest\.config.*\.(js|ts|mjs)$",
    r"\.eslintrc.*$",
    r"tsconfig\.json$",
    r"\.prettierrc.*$",
    r"mypy\.ini$",
    r"\.flake8$",
]

# Dangerous patterns in test file content changes
TEST_WEAKENING_PATTERNS: List[str] = [
    r"@pytest\.mark\.skip\b",
    r"@pytest\.mark\.xfail\b",
    r"@unittest\.skip\b",
    r"@unittest\.expectedFailure\b",
    r"\.skip\s*=\s*True",
    r"\.skipIf\s*\(",
    r"\.skipUnless\s*\(",
    r"raise\s+unittest\.SkipTest",
    r"pytest\.skip\s*\(",
    r"pytest\.mark\.skip",
    r"pytest\.mark\.xfail",
    r"#\s*skip\s*tests?",
    r"collect_ignore\s*=",
    r"__test__\s*=\s*False",
]

# Weakening patterns in config files
CONFIG_WEAKENING_PATTERNS: List[str] = [
    r"ignore\s*=.*test",
    r"norecursedirs\s*=\s*.*test",
    r"collect_ignore\s*=",
    r"exclude\s*=.*test",
    r"testmatch\s*=",
    r"testpaths\s*=",
]

# Assertion weakening patterns
ASSERTION_WEAKENING_PATTERNS: List[str] = [
    r"assert\s+True\s*$",
    r"assert\s+1\s*$",
    r"assert\s+True\s*#",
    r"#\s*TODO:.*assert",
    r"assert\s+False,?\s*['\"].*['\"]\s*$",
]

# Dangerous shell patterns in any file
DANGEROUS_CONTENT_PATTERNS: List[str] = [
    r"os\.system\s*\(",
    r"subprocess\.(call|Popen|run)\s*\(",
    r"eval\s*\(",
    r"exec\s*\(",
    r"__import__\s*\(",
    r"compile\s*\(.*\s*\)",
]


class RepairPolicyValidationResult:
    """Result of a repair policy validation."""

    def __init__(
        self,
        is_allowed: bool = True,
        reasons: Optional[List[str]] = None,
        warnings: Optional[List[str]] = None,
    ):
        self.is_allowed = is_allowed
        self.reasons = reasons or []
        self.warnings = warnings or []

    def __bool__(self) -> bool:
        return self.is_allowed

    def merge(self, other: "RepairPolicyValidationResult") -> "RepairPolicyValidationResult":
        """Merge another validation result into this one."""
        self.is_allowed = self.is_allowed and other.is_allowed
        self.reasons.extend(other.reasons)
        self.warnings.extend(other.warnings)
        return self


class RepairPolicy:
    """Deterministic policy validator for repair proposals.

    Validates:
    - Test tampering (deletion, skipping, assertion weakening)
    - Configuration weakening (disabling verification)
    - Path safety (workspace boundary)
    - Scope limits (max files, max bytes)
    - Protected files
    - Dangerous content patterns
    """

    def __init__(
        self,
        max_files_per_repair: int = 10,
        max_bytes_per_repair: int = 500_000,
        max_changed_lines: int = 500,
        allow_test_modification: bool = False,
        allow_config_modification: bool = False,
    ):
        self._max_files_per_repair = max_files_per_repair
        self._max_bytes_per_repair = max_bytes_per_repair
        self._max_changed_lines = max_changed_lines
        self._allow_test_modification = allow_test_modification
        self._allow_config_modification = allow_config_modification

    def validate(
        self,
        proposal: RepairProposal,
        workspace_root: str,
    ) -> RepairPolicyValidationResult:
        """Validate a repair proposal against all policy rules.

        Args:
            proposal: The repair proposal to validate.
            workspace_root: The absolute path to the workspace root.

        Returns:
            ValidationResult with is_allowed, reasons, and warnings.
        """
        result = RepairPolicyValidationResult(is_allowed=True)

        # If no patch, nothing to validate
        if not proposal.patch or not proposal.patch.changes:
            return result

        patch = proposal.patch

        # 1. Scope validation
        result.merge(self._validate_scope(patch))

        # 2. Path safety validation
        result.merge(self._validate_paths(patch, workspace_root))

        # 3. Test tampering validation
        result.merge(self._validate_test_tampering(patch))

        # 4. Config weakening validation
        result.merge(self._validate_config_weakening(patch))

        # 5. Dangerous content validation
        result.merge(self._validate_dangerous_content(patch))

        # 6. Protected file validation
        result.merge(self._validate_protected_files(patch))

        return result

    # ── Scope Validation ────────────────────────────────────────

    def _validate_scope(self, patch: PatchSet) -> RepairPolicyValidationResult:
        """Validate that the repair is within scope limits."""
        result = RepairPolicyValidationResult(is_allowed=True)

        # Check file count
        if len(patch.changes) > self._max_files_per_repair:
            result.is_allowed = False
            result.reasons.append(
                f"Repair affects {len(patch.changes)} files, "
                f"exceeds max of {self._max_files_per_repair}"
            )

        # Check total size
        total_bytes = sum(
            len(change.new_content or "") for change in patch.changes
        )
        if total_bytes > self._max_bytes_per_repair:
            result.is_allowed = False
            result.reasons.append(
                f"Repair total size {total_bytes} bytes, "
                f"exceeds max of {self._max_bytes_per_repair}"
            )

        return result

    # ── Path Safety Validation ──────────────────────────────────

    def _validate_paths(
        self, patch: PatchSet, workspace_root: str
    ) -> RepairPolicyValidationResult:
        """Validate that all paths are within the workspace."""
        result = RepairPolicyValidationResult(is_allowed=True)
        ws = Path(workspace_root).resolve()

        for change in patch.changes:
            try:
                change_path = Path(change.path)

                # Reject absolute paths outside workspace
                if change_path.is_absolute():
                    result.is_allowed = False
                    result.reasons.append(
                        f"Absolute path outside workspace: {change.path}"
                    )
                    continue

                # Reject parent directory traversal
                resolved = (ws / change_path).resolve()
                try:
                    resolved.relative_to(ws)
                except ValueError:
                    result.is_allowed = False
                    result.reasons.append(
                        f"Path escapes workspace: {change.path}"
                    )
                    continue

                # Reject symlink escape (basic check)
                try:
                    if resolved.is_symlink():
                        target = resolved.readlink()
                        target_resolved = (resolved.parent / target).resolve()
                        try:
                            target_resolved.relative_to(ws)
                        except ValueError:
                            result.is_allowed = False
                            result.reasons.append(
                                f"Symlink escapes workspace: {change.path}"
                            )
                except (OSError, RuntimeError):
                    pass

            except (ValueError, OSError) as exc:
                result.is_allowed = False
                result.reasons.append(
                    f"Invalid path '{change.path}': {exc}"
                )

        return result

    # ── Test Tampering Validation ───────────────────────────────

    def _validate_test_tampering(
        self, patch: PatchSet
    ) -> RepairPolicyValidationResult:
        """Validate that repair does not tamper with tests."""
        result = RepairPolicyValidationResult(is_allowed=True)

        for change in patch.changes:
            is_test_file = self._matches_any(change.path, TEST_FILE_PATTERNS)

            # Deleting test files
            if is_test_file and change.operation == FileOperation.DELETE:
                result.is_allowed = False
                result.reasons.append(
                    f"Test file deletion is not allowed: {change.path}"
                )
                continue

            # Check test weakening patterns in modified content
            if is_test_file and change.operation == FileOperation.MODIFY:
                if change.new_content:
                    weakening = self._find_patterns(
                        change.new_content, TEST_WEAKENING_PATTERNS
                    )
                    if weakening and not self._allow_test_modification:
                        result.is_allowed = False
                        result.reasons.append(
                            f"Test weakening detected in {change.path}: "
                            f"patterns {weakening}"
                        )

                    # Check assertion weakening
                    assertion_weakening = self._find_patterns(
                        change.new_content, ASSERTION_WEAKENING_PATTERNS
                    )
                    if assertion_weakening and not self._allow_test_modification:
                        result.is_allowed = False
                        result.reasons.append(
                            f"Assertion weakening detected in {change.path}: "
                            f"patterns {assertion_weakening}"
                        )

        return result

    # ── Config Weakening Validation ─────────────────────────────

    def _validate_config_weakening(
        self, patch: PatchSet
    ) -> RepairPolicyValidationResult:
        """Validate that repair does not weaken verification config."""
        result = RepairPolicyValidationResult(is_allowed=True)

        for change in patch.changes:
            is_config = self._matches_any(change.path, CONFIG_FILE_PATTERNS)

            if is_config and change.operation == FileOperation.MODIFY:
                if change.new_content:
                    weakening = self._find_patterns(
                        change.new_content, CONFIG_WEAKENING_PATTERNS
                    )
                    if weakening and not self._allow_config_modification:
                        result.is_allowed = False
                        result.reasons.append(
                            f"Config weakening detected in {change.path}: "
                            f"patterns {weakening}"
                        )

            if is_config and change.operation == FileOperation.DELETE:
                result.is_allowed = False
                result.reasons.append(
                    f"Configuration file deletion is not allowed: {change.path}"
                )

        return result

    # ── Dangerous Content Validation ────────────────────────────

    def _validate_dangerous_content(
        self, patch: PatchSet
    ) -> RepairPolicyValidationResult:
        """Validate that repair does not introduce dangerous patterns."""
        result = RepairPolicyValidationResult(is_allowed=True)

        for change in patch.changes:
            if change.new_content:
                dangerous = self._find_patterns(
                    change.new_content, DANGEROUS_CONTENT_PATTERNS
                )
                if dangerous:
                    result.is_allowed = False
                    result.reasons.append(
                        f"Dangerous content pattern in {change.path}: {dangerous}"
                    )

        return result

    # ── Protected File Validation ───────────────────────────────

    def _validate_protected_files(
        self, patch: PatchSet
    ) -> RepairPolicyValidationResult:
        """Validate that repair does not modify explicitly protected files."""
        result = RepairPolicyValidationResult(is_allowed=True)

        # If the patch itself is a fix for a protected file, it's fine.
        # Only flag protection for non-original-patch files.
        for change in patch.changes:
            # Check for .git directory
            if ".git/" in change.path or change.path.startswith(".git"):
                result.is_allowed = False
                result.reasons.append(
                    f"Git directory modification is not allowed: {change.path}"
                )

            # Check for environment/secret files
            if change.path.endswith((".env", ".env.example", ".gitignore")):
                if change.operation == FileOperation.DELETE:
                    result.is_allowed = False
                    result.reasons.append(
                        f"Protected file deletion: {change.path}"
                    )

        return result

    # ── Helpers ─────────────────────────────────────────────────

    @staticmethod
    def _matches_any(path: str, patterns: List[str]) -> bool:
        """Check if a path matches any regex pattern."""
        for pattern in patterns:
            if re.search(pattern, path, re.IGNORECASE):
                return True
        return False

    @staticmethod
    def _find_patterns(content: str, patterns: List[str]) -> List[str]:
        """Find all matching patterns in content."""
        found: List[str] = []
        for pattern in patterns:
            if re.search(pattern, content, re.MULTILINE | re.IGNORECASE):
                found.append(pattern)
        return found
