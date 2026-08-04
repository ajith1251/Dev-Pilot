"""
Failure Diagnosis Service — Phase 8 deterministic failure triage.

Transforms Phase 7 TestRunResult evidence into structured FailureDiagnosis.
Maps failures to changed files, plan steps, and patch changes.
Classifies repairability based on evidence.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Set, Tuple

from app.models.base import new_id
from app.models.coding import FileChange, PatchApplicationResult, PatchSet
from app.models.issues import ImplementationPlan
from app.models.repair import (
    FailureDiagnosis,
    Repairability,
    Repairability,
)
from app.models.testing import (
    ExecutionStatus,
    FailureCategory,
    TestFailure,
    TestRunResult,
)


class FailureDiagnosisService:
    """Deterministic failure diagnosis and triage.

    Responsibilities:
    - Normalize failure evidence from Phase 7 TestRunResult
    - Map failures to changed files (based on stack traces, file paths)
    - Map failures to plan steps (based on affected areas)
    - Map failures to patch changes (based on file paths)
    - Classify repairability based on failure category and evidence
    - Identify baseline vs. introduced failures
    - Produce structured FailureDiagnosis
    """

    def __init__(self) -> None:
        pass

    # ── Main Entry Point ────────────────────────────────────────

    def diagnose(
        self,
        test_result: TestRunResult,
        patch_result: Optional[PatchApplicationResult] = None,
        patch_set: Optional[PatchSet] = None,
        plan: Optional[ImplementationPlan] = None,
    ) -> List[FailureDiagnosis]:
        """Produce structured diagnoses from a test run result.

        Returns one FailureDiagnosis per distinct failure, grouped
        where multiple failures share the same underlying cause.
        """
        diagnoses: List[FailureDiagnosis] = []

        # Handle infrastructure failures
        if test_result.status in (
            ExecutionStatus.ENVIRONMENT_NOT_READY,
            ExecutionStatus.TIMEOUT,
            ExecutionStatus.ERROR,
        ):
            diagnosis = self._diagnose_infrastructure_failure(test_result)
            if diagnosis:
                diagnoses.append(diagnosis)
            return diagnoses

        # Handle command rejections
        rejected_commands = [
            r for r in (test_result.process_results or [])
            if r.status == ExecutionStatus.REJECTED
        ]
        if rejected_commands:
            diagnosis = self._diagnose_rejected_commands(rejected_commands)
            diagnoses.append(diagnosis)

        # Handle individual test failures
        for failure in (test_result.failures or []):
            diagnosis = self._diagnose_single_failure(
                failure=failure,
                patch_result=patch_result,
                patch_set=patch_set,
                plan=plan,
            )
            diagnoses.append(diagnosis)

        # If no failures but status is FAILED, create generic diagnosis
        if (
            test_result.status == ExecutionStatus.FAILED
            and not diagnoses
            and test_result.process_results
        ):
            for proc in test_result.process_results:
                if proc.status in (
                    ExecutionStatus.FAILED,
                    ExecutionStatus.ERROR,
                ):
                    diagnosis = self._diagnose_process_failure(proc)
                    diagnoses.append(diagnosis)

        return diagnoses

    # ── Per-Failure Diagnosis ───────────────────────────────────

    def _diagnose_single_failure(
        self,
        failure: TestFailure,
        patch_result: Optional[PatchApplicationResult] = None,
        patch_set: Optional[PatchSet] = None,
        plan: Optional[ImplementationPlan] = None,
    ) -> FailureDiagnosis:
        """Diagnose a single test failure."""
        # Collect affected files from failure evidence
        affected_files: Set[str] = set()

        # Add the failure's own file path
        if failure.file_path:
            affected_files.add(failure.file_path)

        # Add files from stack trace
        if failure.stack_trace:
            stack_files = self._extract_files_from_trace(failure.stack_trace)
            affected_files.update(stack_files)

        # Add related output evidence
        if failure.related_output:
            related_files = self._extract_files_from_text(failure.related_output)
            affected_files.update(related_files)

        # Map to patch changes
        related_patch_changes: List[str] = []
        if patch_set:
            related_patch_changes = self._map_to_patch_changes(
                list(affected_files), patch_set
            )

        # Map to plan steps
        related_plan_steps: List[str] = []
        if plan:
            related_plan_steps = self._map_to_plan_steps(
                list(affected_files), plan
            )

        # Determine related-to-patch status
        pre_existing_status = self._determine_pre_existing(
            failure, patch_set, patch_result, affected_files
        )

        # Collect evidence
        evidence: List[str] = []
        if failure.message:
            evidence.append(f"Message: {failure.message[:200]}")
        if failure.stack_trace:
            trace_lines = failure.stack_trace.strip().split("\n")
            evidence.append(f"Traceback: {'; '.join(trace_lines[:3])}")
        if failure.file_path and failure.line_number:
            evidence.append(f"Location: {failure.file_path}:{failure.line_number}")

        # Build summary
        test_name = failure.test_name or failure.file_path or "unknown"
        summary = f"{failure.failure_type.value}: {test_name}"
        if failure.message:
            summary += f" — {failure.message[:100]}"

        # Determine likely cause
        likely_cause = self._determine_likely_cause(failure, affected_files, patch_set)

        # Determine repairability
        repairability, confidence = self._classify_repairability(
            failure, affected_files, patch_result
        )

        # Determine what additional context would help
        additional_context = self._identify_context_gaps(
            failure, plan, patch_set
        )

        return FailureDiagnosis(
            diagnosis_id=f"diag-{new_id()[:8]}",
            run_id="",  # Will be set by caller if needed
            failure_ids=[failure.failure_id] if failure.failure_id else [],
            category=failure.failure_type,
            summary=summary,
            likely_cause=likely_cause,
            confidence=confidence,
            repairability=repairability,
            affected_files=sorted(affected_files),
            affected_symbols=self._extract_symbols(failure),
            related_plan_steps=related_plan_steps,
            related_patch_changes=related_patch_changes,
            additional_context_needed=additional_context,
            evidence=evidence,
            related_to_patch=(
                pre_existing_status == "INTRODUCED_BY_PATCH"
            ),
            pre_existing_status=pre_existing_status,
        )

    # ── Infrastructure Diagnosis ────────────────────────────────

    def _diagnose_infrastructure_failure(
        self, test_result: TestRunResult
    ) -> Optional[FailureDiagnosis]:
        """Diagnose an infrastructure/environment failure."""
        summary = test_result.summary or test_result.status.value
        evidence = [test_result.summary] if test_result.summary else []

        return FailureDiagnosis(
            diagnosis_id=f"diag-{new_id()[:8]}",
            run_id=test_result.run_id,
            failure_ids=[f.failure_id for f in (test_result.failures or []) if f.failure_id],
            category=FailureCategory.EXECUTION_ERROR,
            summary=f"Infrastructure failure: {summary}",
            likely_cause="Environment not ready or execution infrastructure failure",
            confidence=0.9,
            repairability=Repairability.ENVIRONMENTAL,
            evidence=evidence,
            related_to_patch=False,
            pre_existing_status="UNKNOWN",
            warnings=["This is an infrastructure issue, not a code defect"],
        )

    def _diagnose_rejected_commands(
        self, rejected_results: List
    ) -> FailureDiagnosis:
        """Diagnose command rejection events."""
        reasons = [r.stderr for r in rejected_results if r.stderr]
        commands = [r.command for r in rejected_results]

        return FailureDiagnosis(
            diagnosis_id=f"diag-{new_id()[:8]}",
            run_id="",
            category=FailureCategory.CONFIGURATION_ERROR,
            summary=f"Command(s) rejected by execution policy: {', '.join(commands[:3])}",
            likely_cause="Command violates execution policy (path, executable, or argument restrictions)",
            confidence=0.95,
            repairability=Repairability.NOT_REPAIRABLE,
            evidence=reasons,
            related_to_patch=False,
            pre_existing_status="UNKNOWN",
            warnings=["Execution policy rejections cannot be fixed by code changes"],
        )

    def _diagnose_process_failure(self, proc) -> FailureDiagnosis:
        """Diagnose a generic process-level failure."""
        evidence = []
        if proc.stdout:
            evidence.append(f"stdout: {proc.stdout[:200]}")
        if proc.stderr:
            evidence.append(f"stderr: {proc.stderr[:200]}")

        return FailureDiagnosis(
            diagnosis_id=f"diag-{new_id()[:8]}",
            run_id="",
            category=FailureCategory.EXECUTION_ERROR,
            summary=f"Process failed: {proc.command} (exit code {proc.exit_code})",
            likely_cause="Process execution error",
            confidence=0.7,
            repairability=Repairability.POSSIBLY_REPAIRABLE,
            evidence=evidence,
            related_to_patch=False,
            pre_existing_status="UNKNOWN",
        )

    # ── Repairability Classification ────────────────────────────

    def _classify_repairability(
        self,
        failure: TestFailure,
        affected_files: Set[str],
        patch_result: Optional[PatchApplicationResult] = None,
    ) -> Tuple[Repairability, float]:
        """Classify whether a failure is repairable and with what confidence."""
        category = failure.failure_type

        # Syntax errors in changed files = high repairability
        if category == FailureCategory.SYNTAX_ERROR:
            if self._any_file_changed(affected_files, patch_result):
                return Repairability.REPAIRABLE, 0.9
            return Repairability.REPAIRABLE, 0.7

        # Import errors — distinguish patch-introduced vs environment
        if category == FailureCategory.IMPORT_ERROR:
            if self._any_file_changed(affected_files, patch_result):
                return Repairability.REPAIRABLE, 0.8
            if self._import_in_patch_package(failure, patch_result):
                return Repairability.REPAIRABLE, 0.7
            return Repairability.POSSIBLY_REPAIRABLE, 0.4

        # Assertion failures in patched code = repairable
        if category == FailureCategory.ASSERTION_FAILURE:
            if self._any_file_changed(affected_files, patch_result):
                return Repairability.REPAIRABLE, 0.7
            return Repairability.POSSIBLY_REPAIRABLE, 0.4

        # Type errors in changed files = repairable
        if category == FailureCategory.TYPE_ERROR:
            if self._any_file_changed(affected_files, patch_result):
                return Repairability.REPAIRABLE, 0.7
            return Repairability.POSSIBLY_REPAIRABLE, 0.4

        # Build/lint failures — possibly repairable
        if category in (FailureCategory.BUILD_FAILURE, FailureCategory.LINT_FAILURE):
            return Repairability.POSSIBLY_REPAIRABLE, 0.5

        # Timeout — likely environmental
        if category == FailureCategory.TIMEOUT:
            return Repairability.ENVIRONMENTAL, 0.3

        # Dependency/configuration errors — environmental
        if category in (
            FailureCategory.DEPENDENCY_ERROR,
            FailureCategory.CONFIGURATION_ERROR,
        ):
            return Repairability.ENVIRONMENTAL, 0.6

        # Execution errors — probably environmental
        if category == FailureCategory.EXECUTION_ERROR:
            return Repairability.ENVIRONMENTAL, 0.4

        # Unknown — insufficient context
        return Repairability.INSUFFICIENT_CONTEXT, 0.2

    # ─── Likely Cause ───────────────────────────────────────────

    def _determine_likely_cause(
        self,
        failure: TestFailure,
        affected_files: Set[str],
        patch_set: Optional[PatchSet] = None,
    ) -> str:
        """Determine the likely engineering cause of a failure."""
        category = failure.failure_type

        if category == FailureCategory.SYNTAX_ERROR:
            if failure.file_path and failure.line_number:
                return (
                    f"Syntax error at {failure.file_path}:{failure.line_number} — "
                    f"a parser-level error was introduced in the code"
                )
            return "Syntax error detected in source code"

        if category == FailureCategory.IMPORT_ERROR:
            if failure.message:
                # Try to extract the missing module name
                match = re.search(
                    r"(?:No module named|cannot import name|import error)\s+'?\"?([^'\"]+)",
                    failure.message,
                    re.IGNORECASE,
                )
                if match:
                    return (
                        f"Import of '{match.group(1)}' failed — "
                        f"module may be missing from environment or import path is incorrect"
                    )
            return "Import statement failed — module or symbol not found"

        if category == FailureCategory.ASSERTION_FAILURE:
            return "Test assertion failed — actual behavior does not match expected behavior"

        if category == FailureCategory.TYPE_ERROR:
            if failure.message:
                return f"Type error: {failure.message[:100]}"
            return "Type mismatch detected in code"

        if category == FailureCategory.LINT_FAILURE:
            return "Code style or lint rule violation"

        if category == FailureCategory.BUILD_FAILURE:
            return "Build/compilation failure"

        if category == FailureCategory.TIMEOUT:
            return "Test timed out — possible infinite loop, slow operation, or hanging dependency"

        if category == FailureCategory.DEPENDENCY_ERROR:
            return "Missing or incompatible dependency"

        if category == FailureCategory.CONFIGURATION_ERROR:
            return "Test or tool configuration issue"

        if failure.message:
            return failure.message[:150]

        return "Unknown cause — insufficient evidence"

    # ── Context Gap Analysis ────────────────────────────────────

    def _identify_context_gaps(
        self,
        failure: TestFailure,
        plan: Optional[ImplementationPlan] = None,
        patch_set: Optional[PatchSet] = None,
    ) -> List[str]:
        """Identify what additional context would help diagnosis."""
        gaps: List[str] = []

        if not failure.stack_trace:
            gaps.append("Full stack trace would improve diagnosis")

        if failure.file_path and patch_set:
            # Check if the failing file was not in the patch
            changed_paths = {c.path for c in patch_set.changes}
            if failure.file_path not in changed_paths:
                gaps.append(
                    f"Failing file ({failure.file_path}) was not modified by the patch"
                )

        if failure.failure_type == FailureCategory.IMPORT_ERROR:
            gaps.append("List of installed dependencies would help distinguish missing imports from wrong imports")

        if failure.failure_type == FailureCategory.TIMEOUT:
            gaps.append("Test duration breakdown would help identify slow operations")

        return gaps

    # ── File → Patch / Plan Mapping ─────────────────────────────

    def _map_to_patch_changes(
        self, files: List[str], patch_set: PatchSet
    ) -> List[str]:
        """Map files to the patch changes that modified them."""
        change_ids: List[str] = []
        for change in patch_set.changes:
            if change.path in files:
                change_ids.append(change.change_id)
        return change_ids

    def _map_to_plan_steps(
        self, files: List[str], plan: ImplementationPlan
    ) -> List[str]:
        """Map files to plan steps that affected them."""
        step_ids: List[str] = []
        for step in plan.steps:
            if step.affected_areas:
                for area in step.affected_areas:
                    if any(area in f for f in files):
                        step_ids.append(step.id)
                        break
        return step_ids

    def _determine_pre_existing(
        self,
        failure: TestFailure,
        patch_set: Optional[PatchSet],
        patch_result: Optional[PatchApplicationResult],
        affected_files: Set[str],
    ) -> str:
        """Determine whether a failure was introduced by the patch or pre-existing."""
        if not patch_set and not patch_result:
            return "UNKNOWN"

        # Check if any affected file was part of the patch
        if patch_set:
            changed_paths = {c.path for c in patch_set.changes}
            for f in affected_files:
                if f in changed_paths:
                    return "INTRODUCED_BY_PATCH"

        # Check if affected file was created by patch
        if patch_result:
            for f in affected_files:
                if f in patch_result.files_created:
                    return "INTRODUCED_BY_PATCH"
                if f in patch_result.files_modified:
                    return "INTRODUCED_BY_PATCH"

        return "PRE_EXISTING"

    # ── Helpers ─────────────────────────────────────────────────

    @staticmethod
    def _extract_files_from_trace(trace: str) -> List[str]:
        """Extract file paths from a stack trace."""
        files: List[str] = []
        # Match file paths in traceback frames
        patterns = [
            r'File "([^"]+)"',
            r'File ([^,]+), line',
        ]
        for pattern in patterns:
            matches = re.findall(pattern, trace)
            files.extend(matches)

        # Filter to reasonable file paths
        return [f for f in files if f.endswith((".py", ".js", ".ts", ".jsx", ".tsx"))]

    @staticmethod
    def _extract_files_from_text(text: str) -> List[str]:
        """Extract file paths from arbitrary text."""
        files: List[str] = []
        # Match paths like path/to/file.py, tests/test_auth.py
        matches = re.findall(
            r'(?:tests?/[\w/]+\.\w+|src/[\w/]+\.\w+|[\w/]+/tests?/[\w/]+\.\w+)',
            text,
        )
        files.extend(matches)
        return files

    @staticmethod
    def _extract_symbols(failure: TestFailure) -> List[str]:
        """Extract relevant symbol names from a failure."""
        symbols: List[str] = []

        # Extract test function name from test_name
        if failure.test_name:
            # Handle pytest-style (tests/test_file.py::TestClass::test_method)
            parts = failure.test_name.split("::")
            for part in parts:
                part = part.strip()
                if part and not part.startswith("tests/"):
                    symbols.append(part)

        # Extract from message
        if failure.message:
            # Look for function/method names
            func_matches = re.findall(
                r'(?:function|method|class)\s+(\w+)',
                failure.message,
                re.IGNORECASE,
            )
            symbols.extend(func_matches)

        return symbols

    @staticmethod
    def _any_file_changed(
        files: Set[str],
        patch_result: Optional[PatchApplicationResult],
    ) -> bool:
        """Check if any of the given files were changed by the patch."""
        if not patch_result:
            return False

        all_changed = set(
            patch_result.files_created
            + patch_result.files_modified
            + patch_result.files_deleted
        )
        return bool(files & all_changed)

    @staticmethod
    def _import_in_patch_package(
        failure: TestFailure,
        patch_result: Optional[PatchApplicationResult],
    ) -> bool:
        """Check if an import error relates to the patched package."""
        if not patch_result or not failure.message:
            return False

        # Extract package prefix from changed files
        changed_paths = (
            patch_result.files_created + patch_result.files_modified
        )
        # Get top-level package directory
        packages: Set[str] = set()
        for p in changed_paths:
            parts = p.replace("\\", "/").split("/")
            if parts:
                packages.add(parts[0])

        # Check if failure message references any of these packages
        for pkg in packages:
            if pkg.lower() in failure.message.lower():
                return True

        return False
