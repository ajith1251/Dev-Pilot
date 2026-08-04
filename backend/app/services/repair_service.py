"""
Repair Service — Phase 8 bounded repair loop orchestrator.

Orchestrates the complete repair pipeline:
1. Validate input (TestRunResult with failures)
2. Diagnose failures (FailureDiagnosisService)
3. Check repairability
4. Retrieve additional context (FailureContextRetriever)
5. Generate repair proposal (FixAgent)
6. Validate repair (PatchValidator + RepairPolicy)
7. Apply repair (SafePatchEngine)
8. Test repair (TestingService / TestAgent)
9. Evaluate result (progress, worsening)
10. Track best-known state, rollback if needed
11. Repeat or stop based on bounded limits

This service does NOT generate fixes or execute processes directly.
It delegates to specialized services for each concern.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from app.agents.fix_agent import FixAgent, FixAgentInput, FixAgentOutput
from app.config import settings
from app.core.exceptions import (
    EnvironmentNotReadyError,
    ExecutionRejectedError,
    PatchApplicationError,
    PatchValidationError,
    RepairError,
    WorkspaceError,
)
from app.models.base import new_id
from app.models.coding import PatchApplicationResult, PatchSet, PatchStatus
from app.models.issues import ImplementationPlan
from app.models.rag import RetrievedContext
from app.models.repair import (
    FailureDiagnosis,
    RepairAttempt,
    RepairAttemptStatus,
    RepairCapabilities,
    RepairProposal,
    RepairProposalStatus,
    RepairResult,
    RepairSession,
    RepairSessionStatus,
    Repairability,
    fingerprint_failure,
    fingerprint_patch,
)
from app.models.testing import (
    ExecutionStatus,
    FailureCategory,
    TestFailure,
    TestRunResult,
)
from app.services.failure_diagnosis_service import FailureDiagnosisService
from app.services.patch_validator import PatchValidator
from app.services.repair_policy import RepairPolicy, RepairPolicyValidationResult
from app.services.safe_patch_engine import SafePatchEngine
from app.services.testing_service import TestingService


class RepairService:
    """Orchestrates the bounded Phase 8 repair loop.

    Flow:
    1. Receive failed TestRunResult + workspace + context
    2. Diagnose failures
    3. Check repairability
    4. Retrieve additional context if needed
    5. Invoke FixAgent for each diagnosis
    6. Validate repair proposals (PatchValidator + RepairPolicy)
    7. Apply valid patches via SafePatchEngine
    8. Rerun verification via TestingService
    9. Evaluate results (fingerprinting, progress, worsening)
    10. Track best state, rollback if worsened
    11. Loop until success / max attempts / no progress
    12. Return RepairResult
    """

    def __init__(
        self,
        fix_agent: Optional[FixAgent] = None,
        testing_service: Optional[TestingService] = None,
        patch_validator: Optional[PatchValidator] = None,
        patch_engine: Optional[SafePatchEngine] = None,
        diagnosis_service: Optional[FailureDiagnosisService] = None,
        repair_policy: Optional[RepairPolicy] = None,
        max_attempts: int = 3,
        max_no_progress_count: int = 2,
    ):
        self._fix_agent = fix_agent or FixAgent()
        self._testing_service = testing_service or TestingService()
        self._patch_validator = patch_validator or PatchValidator(
            workspace_root="",  # Will be set per-call
        )
        self._diagnosis_service = diagnosis_service or FailureDiagnosisService()
        self._repair_policy = repair_policy or RepairPolicy()
        self._max_attempts = max(
            1, min(max_attempts, settings.REPAIR_MAX_ATTEMPTS)
        )
        self._max_no_progress_count = max_no_progress_count

        # Track failure fingerprints across attempts
        self._failure_fingerprints: Dict[str, Set[str]] = {}
        self._patch_fingerprints: Dict[str, Set[str]] = {}

    # ── Main Entry Point ────────────────────────────────────────

    async def run_repair(
        self,
        workspace_root: str,
        workspace_id: str,
        test_result: TestRunResult,
        patch_set: Optional[PatchSet] = None,
        patch_result: Optional[PatchApplicationResult] = None,
        plan: Optional[ImplementationPlan] = None,
        retrieved_context: Optional[RetrievedContext] = None,
        changed_files: Optional[List[str]] = None,
        max_attempts: Optional[int] = None,
        agent_context: Any = None,
    ) -> RepairResult:
        """Run the bounded repair loop.

        Args:
            workspace_root: Absolute path to the writable workspace.
            workspace_id: Workspace identifier.
            test_result: Phase 7 TestRunResult with failures.
            patch_set: Original Phase 6 patch set (for context).
            patch_result: Original Phase 6 patch application result.
            plan: Original implementation plan (for context).
            retrieved_context: Phase 5 retrieved context (for context).
            changed_files: List of changed files.
            max_attempts: Override max attempts for this run.

        Returns:
            RepairResult with full session history.
        """
        session_id = f"session-{new_id()[:8]}"
        start_time = time.time()
        effective_max = max(1, min(
            max_attempts or self._max_attempts,
            settings.REPAIR_MAX_ATTEMPTS,
        ))

        # Session key for fingerprint tracking
        session_key = session_id

        session = RepairSession(
            session_id=session_id,
            workspace_id=workspace_id,
            initial_test_result=test_result,
            status=RepairSessionStatus.RUNNING,
        )

        # Track best-known state
        best_result = test_result
        best_attempt: Optional[RepairAttempt] = None
        no_progress_count = 0

        # 0. Skip if no failures or environmental
        if not test_result.failures and test_result.status not in (
            ExecutionStatus.FAILED,
            ExecutionStatus.TIMEOUT,
            ExecutionStatus.ERROR,
        ):
            session.status = RepairSessionStatus.SUCCESS
            session.stop_reason = "No failures to repair"
            return self._build_result(session, start_time)

        if test_result.status == ExecutionStatus.ENVIRONMENT_NOT_READY:
            session.status = RepairSessionStatus.ENVIRONMENTAL
            session.stop_reason = "Environment not ready — cannot repair"
            return self._build_result(session, start_time)

        # 1. Diagnose failures
        try:
            diagnoses = self._diagnose_failures(
                test_result=test_result,
                patch_set=patch_set,
                patch_result=patch_result,
                plan=plan,
            )
        except Exception as exc:
            session.status = RepairSessionStatus.ERROR
            session.stop_reason = f"Diagnosis failed: {exc}"
            session.errors.append(str(exc))
            return self._build_result(session, start_time)

        if not diagnoses:
            session.status = RepairSessionStatus.NO_REPAIR
            session.stop_reason = "No diagnoses produced from test failures"
            return self._build_result(session, start_time)

        # 2. Check if any failure is repairable
        repairable = any(
            d.repairability in (
                Repairability.REPAIRABLE,
                Repairability.POSSIBLY_REPAIRABLE,
            )
            for d in diagnoses
        )
        if not repairable:
            session.status = RepairSessionStatus.NO_REPAIR
            reasons = [d.repairability.value for d in diagnoses]
            session.stop_reason = f"No repairable failures: {', '.join(set(reasons))}"
            return self._build_result(session, start_time)

        # 3. Bounded repair loop
        # Initialize fingerprint tracking only when we enter the main loop,
        # so early returns above don't leave orphaned dict entries
        self._failure_fingerprints[session_key] = set()
        self._patch_fingerprints[session_key] = set()
        all_diagnoses = diagnoses
        remaining_failures = list(test_result.failures or [])

        for attempt_num in range(1, effective_max + 1):
            attempt_start = time.time()
            attempt = RepairAttempt(
                attempt_id=f"attempt-{new_id()[:8]}",
                attempt_number=attempt_num,
                status=RepairAttemptStatus.PENDING,
                started_at=datetime.now(timezone.utc).isoformat(),
            )

            try:
                # 3a. Get diagnosis for current failures
                current_diagnoses = [
                    d for d in all_diagnoses
                    if any(fid in d.failure_ids for fid in
                           [f.failure_id for f in remaining_failures if f.failure_id])
                ]
                if not current_diagnoses:
                    current_diagnoses = all_diagnoses

                diagnosis = current_diagnoses[0]  # Focus on first failure
                attempt.diagnosis = diagnosis

                # 3b. Generate repair proposal
                attempt.status = RepairAttemptStatus.PROPOSED

                # Build changed file context
                changed_file_context = self._build_changed_file_context(
                    workspace_root, diagnosis
                )

                fix_input = FixAgentInput(
                    diagnosis=diagnosis,
                    test_result=test_result,
                    failures=remaining_failures,
                    changed_file_context=changed_file_context,
                    plan=plan,
                    original_patch=patch_set,
                    retrieved_context=retrieved_context,
                    repair_history=session.attempts,
                    attempt_number=attempt_num,
                    agent_context=agent_context,
                )

                try:
                    fix_output: FixAgentOutput = await self._fix_agent.run(fix_input)
                except Exception as exc:
                    attempt.status = RepairAttemptStatus.ERROR
                    attempt.errors.append(f"FixAgent execution failed: {exc}")
                    session.attempts.append(attempt)
                    no_progress_count += 1

                    if no_progress_count >= self._max_no_progress_count:
                        break
                    continue

                proposal = fix_output.proposal
                attempt.proposal = proposal
                attempt.warnings.extend(fix_output.warnings)

                # Check if agent decided not to repair
                if proposal.status != RepairProposalStatus.PROPOSED:
                    attempt.status = RepairAttemptStatus.SKIPPED
                    session.attempts.append(attempt)

                    if no_progress_count >= self._max_no_progress_count:
                        break
                    continue

                # 3c. Check for repeated patch
                if proposal.patch:
                    pf = fingerprint_patch(proposal)
                    if pf in self._patch_fingerprints[session_key]:
                        attempt.status = RepairAttemptStatus.SKIPPED
                        attempt.errors.append("Repeated patch fingerprint — identical repair already attempted")
                        session.attempts.append(attempt)
                        session.stop_reason = "Repeated patch — no progress"
                        session.status = RepairSessionStatus.REPEATED_PATCH
                        break
                    self._patch_fingerprints[session_key].add(pf)

                # 3d. Validate with RepairPolicy
                if proposal.patch:
                    policy_result = self._repair_policy.validate(
                        proposal=proposal,
                        workspace_root=workspace_root,
                    )

                    if not policy_result.is_allowed:
                        attempt.status = RepairAttemptStatus.REJECTED
                        attempt.errors.extend(policy_result.reasons)
                        session.attempts.append(attempt)
                        session.status = RepairSessionStatus.UNSAFE_REPAIR
                        session.stop_reason = "; ".join(policy_result.reasons[:3])
                        break

                    attempt.warnings.extend(policy_result.warnings)

                    # 3e. Validate with PatchValidator
                    try:
                        validation_result = self._patch_validator.validate(
                            patch=proposal.patch,
                            workspace_root=workspace_root,
                        )
                        if not validation_result.is_valid:
                            attempt.status = RepairAttemptStatus.REJECTED
                            attempt.errors.append(
                                f"PatchValidator rejected: {validation_result.errors[:3]}"
                            )
                            session.attempts.append(attempt)
                            no_progress_count += 1
                            if no_progress_count >= self._max_no_progress_count:
                                break
                            continue
                    except Exception as exc:
                        attempt.status = RepairAttemptStatus.ERROR
                        attempt.errors.append(f"PatchValidator error: {exc}")
                        session.attempts.append(attempt)
                        no_progress_count += 1
                        continue

                    # 3f. Apply patch via SafePatchEngine
                    try:
                        attempt.status = RepairAttemptStatus.APPLIED
                        patch_engine = SafePatchEngine(
                            workspace_root=workspace_root,
                        )
                        # Take snapshot before applying (for potential rollback)
                        workspace_snapshot = patch_engine.snapshot(proposal.patch)
                        apply_result = patch_engine.apply(proposal.patch)
                        attempt.patch_application = apply_result

                        # Store snapshot in attempt metadata for rollback
                        if not apply_result.rolled_back:
                            snapshot_serialized = {
                                k: (v.hex() if v is not None else None)
                                for k, v in workspace_snapshot.items()
                            }
                            attempt.metadata["workspace_snapshot"] = snapshot_serialized

                        if apply_result.status == PatchStatus.FAILED:
                            attempt.status = RepairAttemptStatus.ERROR
                            attempt.errors.append(
                                f"PatchEngine failed: {apply_result.errors[:3]}"
                            )
                            session.attempts.append(attempt)
                            no_progress_count += 1
                            continue

                    except Exception as exc:
                        attempt.status = RepairAttemptStatus.ERROR
                        attempt.errors.append(f"PatchEngine error: {exc}")
                        session.attempts.append(attempt)
                        no_progress_count += 1
                        continue

                # 3g. Re-run tests via TestingService
                attempt.status = RepairAttemptStatus.TESTING
                try:
                    # Build a test plan for the changed files
                    from app.agents.test_agent import TestAgent, TestAgentInput

                    test_agent = TestAgent()
                    changed = list(proposal.patch.changes) if proposal.patch else []

                    new_test_result = await self._run_verification(
                        workspace_id=workspace_id,
                        workspace_root=workspace_root,
                        changed_files=[c.path for c in changed],
                        test_agent=test_agent,
                    )
                    attempt.test_result = new_test_result

                except Exception as exc:
                    attempt.status = RepairAttemptStatus.ERROR
                    attempt.errors.append(f"Test execution failed: {exc}")
                    session.attempts.append(attempt)
                    no_progress_count += 1
                    continue

                # 3h. Evaluate result
                attempt.duration_seconds = time.time() - attempt_start
                attempt.finished_at = datetime.now(timezone.utc).isoformat()

                if new_test_result.status == ExecutionStatus.PASSED:
                    attempt.status = RepairAttemptStatus.PASSED
                    session.final_test_result = new_test_result
                    session.best_attempt = attempt
                    session.attempts.append(attempt)
                    session.status = RepairSessionStatus.SUCCESS
                    session.stop_reason = "All tests passed"
                    session.finished_at = datetime.now(timezone.utc).isoformat()
                    session.duration_seconds = time.time() - start_time
                    return self._build_result(session, start_time)

                else:
                    attempt.status = RepairAttemptStatus.FAILED
                    session.attempts.append(attempt)

                    # Evaluate progress
                    new_fingerprints = self._get_failure_fingerprints(
                        new_test_result
                    )
                    old_fingerprints = self._get_failure_fingerprints(best_result)

                    # Worsening detection
                    if self._is_worsened(best_result, new_test_result):
                        # Roll back to best-known state
                        self._rollback(attempt, workspace_root)
                        attempt.status = RepairAttemptStatus.ROLLED_BACK
                        attempt.warnings.append(
                            "Repair worsened results — rolled back to best-known state"
                        )
                        no_progress_count += 1

                    # Progress detection
                    elif self._has_progress(old_fingerprints, new_fingerprints):
                        # This is progress — update best-known state
                        best_result = new_test_result
                        best_attempt = attempt
                        no_progress_count = 0
                    else:
                        no_progress_count += 1

                    # Update remaining failures for next iteration
                    remaining_failures = list(new_test_result.failures or [])

                    if no_progress_count >= self._max_no_progress_count:
                        session.stop_reason = "No progress after multiple attempts"
                        session.status = RepairSessionStatus.NO_PROGRESS
                        break

            except Exception as exc:
                attempt.status = RepairAttemptStatus.ERROR
                attempt.errors.append(f"Unexpected error: {exc}")
                session.attempts.append(attempt)
                no_progress_count += 1
                attempt.duration_seconds = time.time() - attempt_start
                attempt.finished_at = datetime.now(timezone.utc).isoformat()

            # Continue loop
            if attempt_num >= effective_max:
                if session.status == RepairSessionStatus.RUNNING:
                    session.status = RepairSessionStatus.MAX_ATTEMPTS
                    session.stop_reason = f"Reached max {effective_max} repair attempts"

        # End of loop — finalize session
        session.finished_at = datetime.now(timezone.utc).isoformat()
        session.duration_seconds = time.time() - start_time

        # If still running, it failed
        if session.status == RepairSessionStatus.RUNNING:
            session.status = RepairSessionStatus.FAILED
            if not session.stop_reason:
                session.stop_reason = "Repair loop ended without success"

        # Restore best-known state if last attempt wasn't success
        if best_attempt and session.status != RepairSessionStatus.SUCCESS:
            session.best_attempt = best_attempt
            session.final_test_result = best_result

        # Clean up in-memory fingerprint tracking for this session
        # to prevent memory leaks across many repair sessions
        self.cleanup_session(session_key)

        return self._build_result(session, start_time)

    # ── Diagnosis ───────────────────────────────────────────────

    def _diagnose_failures(
        self,
        test_result: TestRunResult,
        patch_set: Optional[PatchSet] = None,
        patch_result: Optional[PatchApplicationResult] = None,
        plan: Optional[ImplementationPlan] = None,
    ) -> List[FailureDiagnosis]:
        """Produce diagnoses from test failures."""
        return self._diagnosis_service.diagnose(
            test_result=test_result,
            patch_result=patch_result,
            patch_set=patch_set,
            plan=plan,
        )

    # ── Verification ────────────────────────────────────────────

    async def _run_verification(
        self,
        workspace_id: str,
        workspace_root: str,
        changed_files: List[str],
        test_agent,
    ) -> TestRunResult:
        """Run Phase 7 verification on the workspace."""
        agent_input = TestAgentInput(
            workspace_id=workspace_id,
            workspace_root=workspace_root,
            changed_files=changed_files,
        )
        agent_output = await test_agent.run(agent_input)

        # Validate plan against policy
        package_scripts = self._testing_service.load_package_scripts(
            workspace_root
        )

        # Execute
        result = await self._testing_service.run_tests(agent_output.plan)
        return result

    # ── Progress / Worsening Detection ──────────────────────────

    def _get_failure_fingerprints(
        self, result: TestRunResult
    ) -> Set[str]:
        """Get set of failure fingerprints from a test result."""
        fingerprints: Set[str] = set()
        for failure in (result.failures or []):
            fingerprints.add(fingerprint_failure(failure))
        return fingerprints

    def _has_progress(
        self, old_fingerprints: Set[str], new_fingerprints: Set[str]
    ) -> bool:
        """Check if the new state has fewer failure fingerprints."""
        # Progress = fewer distinct failure fingerprints
        return len(new_fingerprints) < len(old_fingerprints)

    def _is_worsened(
        self, old_result: TestRunResult, new_result: TestRunResult
    ) -> bool:
        """Check if the new test result is worse than the old one.

        Worsening means:
        - Fewer passed tests
        - More failed tests
        - More process errors
        - Infrastructure status degraded
        """
        # Get comparison metrics
        old_failures = len(old_result.failures or [])
        new_failures = len(new_result.failures or [])

        old_passed = old_result.commands_passed or 0
        new_passed = new_result.commands_passed or 0

        # Worse if more failures AND fewer passed commands
        if new_failures > old_failures and new_passed < old_passed:
            return True

        # Worse if status degraded
        status_order = [
            ExecutionStatus.PASSED,
            ExecutionStatus.SKIPPED,
            ExecutionStatus.FAILED,
            ExecutionStatus.TIMEOUT,
            ExecutionStatus.ERROR,
            ExecutionStatus.ENVIRONMENT_NOT_READY,
        ]
        try:
            old_idx = status_order.index(old_result.status)
            new_idx = status_order.index(new_result.status)
            if new_idx > old_idx + 1:  # More than one step worse
                return True
        except ValueError:
            pass

        # Worse if significant increase in failure count (>50% increase)
        if new_failures > old_failures and old_failures > 0:
            if new_failures / old_failures > 1.5:
                return True

        return False

    # ── Rollback ────────────────────────────────────────────────

    def _rollback(
        self, attempt: RepairAttempt, workspace_root: str
    ) -> None:
        """Rollback a repair attempt to restore previous workspace state.

        Uses the snapshot stored in attempt.metadata to restore files.
        """
        snapshot_raw = attempt.metadata.get("workspace_snapshot")
        if not snapshot_raw:
            attempt.errors.append("No snapshot available for rollback")
            return

        # Deserialize snapshot
        try:
            snapshot: Dict[str, Optional[bytes]] = {}
            for path_str, hex_val in snapshot_raw.items():
                if hex_val is not None:
                    snapshot[path_str] = bytes.fromhex(hex_val)
                else:
                    snapshot[path_str] = None

            patch_engine = SafePatchEngine(workspace_root=workspace_root)
            patch_engine.rollback(snapshot)
        except Exception as exc:
            attempt.errors.append(f"Rollback failed: {exc}")

    # ── Helpers ─────────────────────────────────────────────────

    def _build_changed_file_context(
        self, workspace_root: str, diagnosis: FailureDiagnosis
    ) -> str:
        """Build source file context for the FixAgent."""
        from pathlib import Path

        ws = Path(workspace_root)
        parts = []

        for file_path in diagnosis.affected_files[:5]:
            full_path = ws / file_path
            if full_path.exists():
                try:
                    content = full_path.read_text("utf-8", errors="replace")
                    parts.append(f"=== {file_path} ===")
                    parts.append(content[:3000])  # Limit context per file
                    parts.append("")
                except (OSError, PermissionError):
                    parts.append(f"=== {file_path} === (unreadable)")
                    parts.append("")

        return "\n".join(parts)

    @staticmethod
    def _build_result(
        session: RepairSession, start_time: float
    ) -> RepairResult:
        """Build a RepairResult from the session state."""
        duration = time.time() - start_time

        initial = session.initial_test_result
        final = session.final_test_result
        remaining = []

        if final and final.failures:
            remaining = list(final.failures)

        # Build concise summary
        if session.status == RepairSessionStatus.SUCCESS:
            summary = (
                f"Repair succeeded in {len(session.attempts)} attempt(s). "
                f"All tests pass."
            )
        elif session.status == RepairSessionStatus.MAX_ATTEMPTS:
            summary = (
                f"Repair stopped after {len(session.attempts)} attempt(s) "
                f"(max attempts reached). "
                f"{len(remaining)} failure(s) remain."
            )
        elif session.status == RepairSessionStatus.NO_PROGRESS:
            summary = (
                f"Repair stopped after {len(session.attempts)} attempt(s) "
                f"(no progress detected). "
                f"{len(remaining)} failure(s) remain."
            )
        elif session.status == RepairSessionStatus.NO_REPAIR:
            summary = (
                f"Repair not attempted: {session.stop_reason}"
            )
        else:
            summary = (
                f"Repair ended with status {session.status.value}: "
                f"{session.stop_reason}"
            )

        return RepairResult(
            session=session,
            status=session.status,
            initial_test_result=initial,
            final_test_result=final,
            attempts=len(session.attempts),
            best_attempt=session.best_attempt,
            stop_reason=session.stop_reason,
            remaining_failures=remaining,
            workspace_id=session.workspace_id,
            summary=summary,
            duration_seconds=duration,
            warnings=session.warnings,
        )

    def cleanup_session(self, session_id: str) -> None:
        """Clean up in-memory fingerprint tracking for a completed session.

        Prevents memory leaks by removing fingerprint sets for sessions
        that are no longer active. Call this after a repair session ends.
        """
        self._failure_fingerprints.pop(session_id, None)
        self._patch_fingerprints.pop(session_id, None)

    def get_capabilities(self) -> RepairCapabilities:
        """Return current repair capabilities."""
        return RepairCapabilities(
            max_repair_attempts=self._max_attempts,
        )

    def diagnose_only(
        self,
        test_result: TestRunResult,
        patch_set: Optional[PatchSet] = None,
        patch_result: Optional[PatchApplicationResult] = None,
        plan: Optional[ImplementationPlan] = None,
    ) -> List[FailureDiagnosis]:
        """Diagnose failures without running any repair.

        Useful for inspection/preview before deciding to repair.
        """
        return self._diagnose_failures(
            test_result=test_result,
            patch_set=patch_set,
            patch_result=patch_result,
            plan=plan,
        )
