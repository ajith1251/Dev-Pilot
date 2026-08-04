"""
Deterministic Review — Phase 9 deterministic checks.

Runs all review checks that do NOT require LLM reasoning.
These are the authoritative gate checks — LLM findings cannot override them.

Checks:
1. Final verification status — did tests run and pass?
2. Remaining failures — are there unresolved failures?
3. Requirement coverage completeness — are all requirements covered?
4. Unresolved repair result — did repair succeed?
5. Changed-file scope — detect out-of-scope changes
6. Test deletion detection — were test files deleted?
7. Skip/xfail introduction — were tests weakened?
8. Protected security invariants — security boundaries intact?
9. Workspace integrity — was original repository modified?
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Set

from app.models.coding import FileOperation, PatchSet
from app.models.issues import ImplementationPlan
from app.models.repair import RepairResult, RepairSessionStatus
from app.models.review import (
    DeterministicReviewResult,
    FindingCategory,
    FindingSeverity,
    RequirementCoverage,
    RequirementStatus,
    ReviewFinding,
    ReviewInput,
    ScopeSummary,
    SecuritySummary,
    TestSummary,
)
from app.models.testing import ExecutionStatus, TestRunResult

# Patterns for test weakening
TEST_SKIP_PATTERNS: List[str] = [
    r"@pytest\.mark\.skip\b",
    r"@pytest\.mark\.xfail\b",
    r"@unittest\.skip\b",
    r"@unittest\.expectedFailure\b",
    r"pytest\.skip\s*\(",
    r"raise\s+unittest\.SkipTest",
    r"__test__\s*=\s*False",
]

# Dangerous invariants to check
DANGEROUS_PATTERNS: List[str] = [
    r"subprocess\.(call|Popen|run)\s*\(",
    r"os\.system\s*\(",
    r"shell\s*=\s*True",
    r"eval\s*\(.*input",
    r"exec\s*\(",
]


class DeterministicReview:
    """Runs deterministic review checks. No LLM required."""

    def __init__(self) -> None:
        self._findings: List[ReviewFinding] = []
        self._warnings: List[str] = []

    def run(self, inp: ReviewInput) -> DeterministicReviewResult:
        """Run all deterministic review checks."""
        self._findings = []
        self._warnings = []

        test_summary = self._check_verification(inp.test_result)
        self._check_remaining_failures(inp.test_result)
        self._check_requirement_coverage(inp)
        self._check_repair_result(inp.repair_result)
        self._check_file_scope(inp)
        self._check_test_tampering(inp)
        self._check_security_invariants(inp)
        self._check_workspace_integrity(inp)

        # Build summaries
        security_summary = SecuritySummary(
            passed=all(
                f.category != FindingCategory.SECURITY or f.severity != FindingSeverity.CRITICAL
                for f in self._findings
            ),
            warnings=[
                f.description for f in self._findings
                if f.category == FindingCategory.SECURITY
            ],
        )

        scope_summary = ScopeSummary(
            warnings=[
                f.description for f in self._findings
                if f.category == FindingCategory.SCOPE
            ],
        )

        passed = all(
            not f.blocking
            for f in self._findings
        )

        return DeterministicReviewResult(
            passed=passed,
            findings=self._findings,
            test_summary=test_summary,
            security_summary=security_summary,
            scope_summary=scope_summary,
            warnings=self._warnings,
        )

    # ── Check 1: Final Verification Status ──────────────────────

    def _check_verification(self, test_result: Optional[TestRunResult]) -> TestSummary:
        """Check if tests executed and passed."""
        summary = TestSummary()

        if not test_result:
            self._findings.append(ReviewFinding(
                finding_id="DET-001",
                category=FindingCategory.TESTING,
                severity=FindingSeverity.HIGH,
                title="No test results available",
                description="Verification was not executed or results are missing. "
                            "Cannot confirm implementation correctness without test evidence.",
                blocking=True,
                confidence=1.0,
            ))
            summary.executed = False
            return summary

        status = test_result.status.value if hasattr(test_result.status, 'value') else str(test_result.status)
        summary.executed = True
        summary.status = status
        summary.tests_passed = test_result.tests_passed
        summary.tests_failed = test_result.tests_failed
        summary.tests_skipped = test_result.tests_skipped
        summary.commands_total = test_result.commands_total
        summary.commands_passed = test_result.commands_passed
        summary.commands_failed = test_result.commands_failed
        summary.duration_seconds = test_result.duration_seconds
        summary.has_skipped = (test_result.tests_skipped or 0) > 0
        summary.warnings = test_result.warnings or []

        # Check for rejected commands
        if test_result.process_results:
            rejected = [p for p in test_result.process_results
                        if hasattr(p.status, 'value') and p.status.value == 'rejected']
            if rejected:
                summary.commands_rejected = len(rejected)
                self._warnings.append(
                    f"{len(rejected)} command(s) were rejected by execution policy"
                )

        if status == ExecutionStatus.PASSED.value:
            # Tests passed - good
            pass
        elif status == ExecutionStatus.FAILED.value:
            self._findings.append(ReviewFinding(
                finding_id="DET-002",
                category=FindingCategory.TESTING,
                severity=FindingSeverity.CRITICAL,
                title="Final tests failed",
                description=f"Tests failed: {test_result.tests_failed or 0} failures "
                            f"in {test_result.tests_total or 0} tests. "
                            f"{test_result.summary or ''}",
                blocking=True,
                confidence=1.0,
                evidence=[f"Status: {status}",
                          f"Tests: {test_result.tests_passed}/{test_result.tests_failed}/{test_result.tests_skipped}",
                          f"Commands: {test_result.commands_passed}/{test_result.commands_failed}"],
            ))
        elif status == ExecutionStatus.ENVIRONMENT_NOT_READY.value:
            self._findings.append(ReviewFinding(
                finding_id="DET-003",
                category=FindingCategory.TESTING,
                severity=FindingSeverity.HIGH,
                title="Test environment not ready",
                description="Verification could not complete because the test environment "
                            "was not ready. This may be an infrastructure issue.",
                blocking=True,
                confidence=1.0,
                evidence=[f"Status: {status}"],
            ))
        elif status == ExecutionStatus.TIMEOUT.value:
            self._findings.append(ReviewFinding(
                finding_id="DET-004",
                category=FindingCategory.TESTING,
                severity=FindingSeverity.HIGH,
                title="Test execution timed out",
                description="Verification timed out. Results may be incomplete.",
                blocking=True,
                confidence=0.8,
            ))
        elif status == ExecutionStatus.ERROR.value:
            self._findings.append(ReviewFinding(
                finding_id="DET-005",
                category=FindingCategory.TESTING,
                severity=FindingSeverity.CRITICAL,
                title="Test execution error",
                description=f"Test execution encountered an error: {test_result.summary or 'Unknown error'}",
                blocking=True,
                confidence=1.0,
            ))

        # Check for suspicious skips
        if test_result.tests_skipped and test_result.tests_skipped > 0 and test_result.tests_total:
            skip_ratio = test_result.tests_skipped / test_result.tests_total
            if skip_ratio > 0.5:
                self._findings.append(ReviewFinding(
                    finding_id="DET-006",
                    category=FindingCategory.TESTING,
                    severity=FindingSeverity.MEDIUM,
                    title=f"High test skip rate ({skip_ratio:.0%})",
                    description=f"{test_result.tests_skipped} of {test_result.tests_total} "
                                f"tests were skipped. This may indicate configuration issues "
                                f"or attempts to hide test failures.",
                    blocking=False,
                    confidence=0.6,
                    evidence=[f"Tests skipped: {test_result.tests_skipped}/{test_result.tests_total}"],
                ))

        return summary

    # ── Check 2: Remaining Failures ─────────────────────────────

    def _check_remaining_failures(
        self, test_result: Optional[TestRunResult]
    ) -> None:
        """Check for unresolved test failures."""
        if not test_result or not test_result.failures:
            return

        # Already handled by DET-002, but add detail if failures are numerous
        if len(test_result.failures) > 3:
            self._findings.append(ReviewFinding(
                finding_id="DET-007",
                category=FindingCategory.TESTING,
                severity=FindingSeverity.MEDIUM,
                title=f"Multiple test failures ({len(test_result.failures)})",
                description=f"There are {len(test_result.failures)} distinct test failures. "
                            f"Each failure should be investigated independently.",
                blocking=False,
                confidence=0.9,
            ))

    # ── Check 3: Requirement Coverage ───────────────────────────

    def _check_requirement_coverage(self, inp: ReviewInput) -> None:
        """Check that all requirements have implementation evidence."""
        if not inp.requirements:
            return

        requirements = inp.requirements.requirements
        plan = inp.implementation_plan

        if not requirements:
            return

        # Count covered vs uncovered
        covered = 0
        uncovered = 0
        for i, req in enumerate(requirements):
            req_id = f"REQ-{i+1:03d}"
            covered_steps = set()

            if plan and plan.requirements_coverage:
                step_ids = plan.requirements_coverage.get(req_id, [])
                covered_steps.update(step_ids)

            # Map to changed files
            affected_files = set()
            if plan and covered_steps:
                for step in plan.steps:
                    if step.id in covered_steps:
                        affected_files.update(step.affected_areas)

            if covered_steps or affected_files:
                covered += 1
            else:
                uncovered += 1

        # If many requirements are uncovered, flag it
        if requirements and uncovered > 0:
            ratio = uncovered / len(requirements)
            if ratio > 0.5:
                self._findings.append(ReviewFinding(
                    finding_id="DET-008",
                    category=FindingCategory.REQUIREMENT,
                    severity=FindingSeverity.HIGH,
                    title=f"{uncovered}/{len(requirements)} requirements lack implementation evidence",
                    description=f"{uncovered} of {len(requirements)} requirements do not have "
                                f"clear implementation evidence. They may be partially or "
                                f"not implemented.",
                    blocking=True,
                    confidence=0.7,
                    evidence=[
                        f"Total requirements: {len(requirements)}",
                        f"Covered: {covered}",
                        f"Uncovered: {uncovered}",
                    ],
                ))
            elif ratio > 0:
                self._findings.append(ReviewFinding(
                    finding_id="DET-009",
                    category=FindingCategory.REQUIREMENT,
                    severity=FindingSeverity.MEDIUM,
                    title=f"{uncovered}/{len(requirements)} requirements may lack evidence",
                    description=f"{uncovered} of {len(requirements)} requirements have "
                                f"limited or no implementation evidence.",
                    blocking=False,
                    confidence=0.5,
                ))

    # ── Check 4: Unresolved Repair ─────────────────────────────

    def _check_repair_result(
        self, repair_result: Optional[RepairResult]
    ) -> None:
        """Check if repair was successful or unresolved."""
        if not repair_result:
            return

        status = repair_result.status

        if status == RepairSessionStatus.SUCCESS:
            return  # Repair succeeded — no issue

        if status == RepairSessionStatus.MAX_ATTEMPTS:
            self._findings.append(ReviewFinding(
                finding_id="DET-010",
                category=FindingCategory.TESTING,
                severity=FindingSeverity.HIGH,
                title="Repair reached max attempts without resolving all failures",
                description=f"Repair stopped after {repair_result.attempts} attempts "
                            f"(max reached). {len(repair_result.remaining_failures)} "
                            f"failure(s) remain unresolved.",
                blocking=True,
                confidence=1.0,
                evidence=[f"Stop reason: {repair_result.stop_reason}",
                          f"Attempts: {repair_result.attempts}",
                          f"Remaining failures: {len(repair_result.remaining_failures)}"],
            ))
        elif status == RepairSessionStatus.NO_PROGRESS:
            self._findings.append(ReviewFinding(
                finding_id="DET-011",
                category=FindingCategory.TESTING,
                severity=FindingSeverity.HIGH,
                title="Repair stopped due to no progress",
                description=f"Repair made no progress after {repair_result.attempts} attempts. "
                            f"Remaining issues could not be resolved.",
                blocking=True,
                confidence=1.0,
            ))
        elif status == RepairSessionStatus.FAILED:
            self._findings.append(ReviewFinding(
                finding_id="DET-012",
                category=FindingCategory.TESTING,
                severity=FindingSeverity.HIGH,
                title="Repair failed",
                description=f"Repair failed: {repair_result.stop_reason}",
                blocking=True,
                confidence=1.0,
            ))
        elif status == RepairSessionStatus.UNSAFE_REPAIR:
            self._findings.append(ReviewFinding(
                finding_id="DET-013",
                category=FindingCategory.SECURITY,
                severity=FindingSeverity.CRITICAL,
                title="Unsafe repair proposal detected",
                description=f"Repair was rejected as unsafe: {repair_result.stop_reason}",
                blocking=True,
                confidence=1.0,
            ))
        elif status == RepairSessionStatus.NO_REPAIR:
            self._findings.append(ReviewFinding(
                finding_id="DET-014",
                category=FindingCategory.TESTING,
                severity=FindingSeverity.MEDIUM,
                title="Repair was not attempted",
                description=f"Repair was not attempted: {repair_result.stop_reason or 'Unknown reason'}",
                blocking=False,
                confidence=1.0,
            ))
        elif status == RepairSessionStatus.REPEATED_PATCH:
            self._findings.append(ReviewFinding(
                finding_id="DET-015",
                category=FindingCategory.QUALITY,
                severity=FindingSeverity.MEDIUM,
                title="Repair produced repeated identical patches",
                description="The repair loop generated the same patch multiple times. "
                            "This suggests the diagnosis may be incorrect or the fix "
                            "requires a different approach.",
                blocking=False,
                confidence=0.7,
            ))

    # ── Check 5: Changed-file Scope ────────────────────────────

    def _check_file_scope(self, inp: ReviewInput) -> None:
        """Detect out-of-scope changes."""
        if not inp.implementation_plan or not inp.original_patch:
            return

        plan = inp.implementation_plan
        patch = inp.original_patch

        # Collect known affected areas from plan
        known_areas: Set[str] = set()
        for step in plan.steps:
            known_areas.update(step.affected_areas)

        # Check each change in the patch
        out_of_scope: List[str] = []
        for change in patch.changes:
            path = change.path
            in_scope = False
            for area in known_areas:
                if area in path or path in area:
                    in_scope = True
                    break
            if not in_scope:
                out_of_scope.append(path)

        if out_of_scope:
            self._findings.append(ReviewFinding(
                finding_id="DET-016",
                category=FindingCategory.SCOPE,
                severity=FindingSeverity.MEDIUM,
                title=f"Changes to {len(out_of_scope)} file(s) outside planned scope",
                description=f"The following files were modified but are not part of any "
                            f"plan step's affected areas: {', '.join(out_of_scope[:5])}",
                blocking=False,
                confidence=0.7,
                evidence=[f"Out of scope: {out_of_scope[:10]}",
                          f"Known areas: {list(known_areas)[:10]}"],
            ))

    # ── Check 6: Test Tampering ─────────────────────────────────

    def _check_test_tampering(self, inp: ReviewInput) -> None:
        """Detect test file deletion and weakening."""
        if not inp.original_patch:
            return

        patch = inp.original_patch

        for change in patch.changes:
            path = change.path

            # Check for test file deletion
            is_test = self._is_test_file(path)
            if is_test and change.operation == FileOperation.DELETE:
                self._findings.append(ReviewFinding(
                    finding_id="DET-017",
                    category=FindingCategory.TAMPERING,
                    severity=FindingSeverity.CRITICAL,
                    title=f"Test file deleted: {path}",
                    description=f"A test file was deleted as part of the patch. "
                                f"Test deletion can hide failures.",
                    file_path=path,
                    blocking=True,
                    confidence=1.0,
                    evidence=[f"Operation: DELETE", f"File: {path}"],
                ))

            # Check for skip/xfail introduction in test files
            if is_test and change.new_content:
                for pattern in TEST_SKIP_PATTERNS:
                    if re.search(pattern, change.new_content, re.MULTILINE | re.IGNORECASE):
                        self._findings.append(ReviewFinding(
                            finding_id="DET-018",
                            category=FindingCategory.TAMPERING,
                            severity=FindingSeverity.HIGH,
                            title=f"Test weakening detected in {path}",
                            description=f"A test weakening pattern was found in {path}. "
                                        f"This can hide test failures.",
                            file_path=path,
                            blocking=True,
                            confidence=0.9,
                            evidence=[f"Pattern: {pattern}"],
                        ))
                        break  # One finding per file

        # Check repair history for tampering
        if inp.repair_result and inp.repair_result.session:
            for attempt in inp.repair_result.session.attempts:
                if attempt.status.value == "rejected" and attempt.errors:
                    for err in attempt.errors:
                        if "test" in err.lower() and ("delet" in err.lower() or "weaken" in err.lower()):
                            self._findings.append(ReviewFinding(
                                finding_id="DET-019",
                                category=FindingCategory.TAMPERING,
                                severity=FindingSeverity.HIGH,
                                title="Repair attempt rejected for test tampering",
                                description=err,
                                blocking=True,
                                confidence=1.0,
                                evidence=[err],
                            ))
                            break

    # ── Check 7: Security Invariants ────────────────────────────

    def _check_security_invariants(self, inp: ReviewInput) -> None:
        """Check for security invariant violations in changes."""
        if not inp.original_patch:
            return

        patch = inp.original_patch

        for change in patch.changes:
            if not change.new_content:
                continue

            for pattern in DANGEROUS_PATTERNS:
                if re.search(pattern, change.new_content, re.MULTILINE):
                    self._findings.append(ReviewFinding(
                        finding_id="DET-020",
                        category=FindingCategory.SECURITY,
                        severity=FindingSeverity.CRITICAL,
                        title=f"Potential security violation in {change.path}",
                        description=f"A potentially dangerous pattern was detected in "
                                    f"{change.path}: {pattern}",
                        file_path=change.path,
                        blocking=True,
                        confidence=0.8,
                        evidence=[f"Pattern: {pattern}", f"File: {change.path}"],
                    ))
                    break

    # ── Check 8: Workspace Integrity ────────────────────────────

    def _check_workspace_integrity(self, inp: ReviewInput) -> None:
        """Check if original repository was modified."""
        # This is a structural check — if RepairResult has warnings about
        # original repo modification, flag it
        if inp.repair_result and inp.repair_result.session:
            for attempt in inp.repair_result.session.attempts:
                for err in attempt.errors:
                    if "original" in err.lower() and ("repo" in err.lower() or "repository" in err.lower()):
                        self._findings.append(ReviewFinding(
                            finding_id="DET-021",
                            category=FindingCategory.SECURITY,
                            severity=FindingSeverity.CRITICAL,
                            title="Original repository modification detected",
                            description=f"A repair attempt attempted to modify the original "
                                        f"repository rather than the workspace: {err}",
                            blocking=True,
                            confidence=1.0,
                            evidence=[err],
                        ))
                        break

    # ── Helpers ─────────────────────────────────────────────────

    @staticmethod
    def _is_test_file(path: str) -> bool:
        """Check if a file path appears to be a test file."""
        test_patterns = [
            r"^tests?/",
            r"/tests?/",
            r"test_.*\.py$",
            r".*_test\.py$",
            r".*\.test\.(ts|tsx|js|jsx)$",
            r".*\.spec\.(ts|tsx|js|jsx)$",
            r"^__tests?__/",
            r"/__tests?__/",
        ]
        for pattern in test_patterns:
            if re.search(pattern, path, re.IGNORECASE):
                return True
        return False
