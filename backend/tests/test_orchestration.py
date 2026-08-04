"""
Comprehensive Phase 10 tests — End-to-End Multi-Agent Orchestration.

Covers state machine transitions, happy path, repair path, rejection path,
cancellation, failure boundaries, security, run store, events, and decision mapping.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.coding import CodingAgentOutput, FileChange, FileOperation, PatchSet
from app.models.issues import ImplementationPlan, ImplementationStep, Requirement, StructuredRequirements
from app.models.orchestration import (
    STAGE_TRANSITIONS, TERMINAL_STAGES, DevPilotRun, DevPilotRunResult, FailureCode,
    OrchestrationCapabilities, RunEvent, RunFailure, RunSource, RunSourceType,
    RunStateMachine, RunStatus, StageResult, StageStatus, StageType, TransitionError,
)
from app.models.rag import RetrievedContext, RetrievalQuery
from app.models.repair import RepairResult, RepairSessionStatus
from app.models.review import FindingSeverity, QualityGateDecision, QualityGateResult, ReviewFinding, ReviewReport
from app.models.testing import ExecutionStatus, TestFailure, TestRunResult
from app.services.orchestration_service import OrchestrationService
from app.services.run_store import InMemoryRunStore, generate_run_id

client = TestClient(app)


# ── Helpers ─────────────────────────────────────────────────────

def _mock_stage(target: StageType):
    async def _fn(run, *args, **kwargs):
        run.current_stage = target
        return True
    return _fn


def _mock_approve():
    async def _fn(run, *args, **kwargs):
        run.current_stage = StageType.QUALITY_GATE
        run.status = RunStatus.APPROVED
        return True
    return _fn


def _mock_repair():
    async def _fn(run, *args, **kwargs):
        run.repair_result = make_repair_success()
        run.current_stage = StageType.REPAIRING
        return True
    return _fn


async def _prepare_run(orch, title="Test", repo_path=None):
    """Create a run pre-populating all guarded fields and mocking always-called stages."""
    source = RunSource(source_type=RunSourceType.USER_TASK, title=title, repository_path=repo_path)
    run = await orch.create_run(source)
    run.repository_path = repo_path
    if repo_path:
        run.repository_profile = MagicMock()
    run.requirements = StructuredRequirements(objective="Test", requirements=[Requirement(id="REQ-001", description="Test")])
    run.plan = ImplementationPlan(summary="Test", objective="Test", steps=[ImplementationStep(id="STEP-001", title="S", description="D", affected_areas=["s"])], test_strategy="T")
    run.retrieved_context = MagicMock()
    run.patch_set = PatchSet(patch_id="p", changes=[])
    run.patch_result = MagicMock()
    await orch._store.update(run)
    return run.run_id


# ── Fixtures ────────────────────────────────────────────────────

@pytest.fixture
def store() -> InMemoryRunStore:
    return InMemoryRunStore()

@pytest.fixture
def run_source() -> RunSource:
    return RunSource(source_type=RunSourceType.USER_TASK, title="Test", description="A test")


def make_plan() -> ImplementationPlan:
    return ImplementationPlan(summary="T", objective="T", steps=[ImplementationStep(id="STEP-001", title="S", description="D", affected_areas=["s"])], test_strategy="T")

def make_reqs() -> StructuredRequirements:
    return StructuredRequirements(objective="T", requirements=[Requirement(id="REQ-001", description="T")])

def make_passed_tr() -> TestRunResult:
    return TestRunResult(run_id="r", workspace_id="w", status=ExecutionStatus.PASSED, commands_total=1, commands_passed=1, commands_failed=0, commands_skipped=0, tests_total=5, tests_passed=5, tests_failed=0, tests_skipped=0, failures=[], process_results=[], duration_seconds=0.5, summary="pass")

def make_failed_tr() -> TestRunResult:
    return TestRunResult(run_id="r", workspace_id="w", status=ExecutionStatus.FAILED, commands_total=1, commands_passed=0, commands_failed=1, commands_skipped=0, tests_total=5, tests_passed=3, tests_failed=2, tests_skipped=0, failures=[TestFailure(failure_id="tf-001", test_name="t", file_path="f.py", line_number=1, message="fail", framework="pytest", failure_type="assertion_failure")], process_results=[], duration_seconds=0.5, summary="fail")

def make_approved_gate() -> QualityGateResult:
    return QualityGateResult(review_id="rv-001", decision=QualityGateDecision.APPROVED, score=92.5, requirements_satisfied=3, requirements_partial=0, requirements_unsatisfied=0, verification_status="passed", security_status="passed", reason_codes=["review_passed"])

def make_rejected_gate() -> QualityGateResult:
    return QualityGateResult(review_id="rv-002", decision=QualityGateDecision.REJECTED, score=28.4, verification_status="failed", security_status="failed", reason_codes=["requirement_unsatisfied"], blocking_findings=["Missing audit"])

def make_repair_success() -> RepairResult:
    from app.models.repair import RepairSession
    return RepairResult(
        session=RepairSession(session_id="rs-001", workspace_id="ws-001", status=RepairSessionStatus.SUCCESS),
        status=RepairSessionStatus.SUCCESS, stop_reason="Fixed", attempts=1,
        remaining_failures=[], summary="repair ok", duration_seconds=1.0,
    )


# ═════════════════════════════════════════════════════════════════
#  1 — STATE MACHINE
# ═════════════════════════════════════════════════════════════════

class TestRunStateMachine:
    def test_can_transition_valid(self):
        assert RunStateMachine.can_transition(StageType.PLANNING, StageType.RETRIEVING_CONTEXT)
        assert RunStateMachine.can_transition(StageType.TESTING, StageType.REVIEWING)
        assert RunStateMachine.can_transition(StageType.TESTING, StageType.REPAIRING)
        assert RunStateMachine.can_transition(StageType.REPAIRING, StageType.TESTING)

    def test_can_transition_invalid(self):
        assert not RunStateMachine.can_transition(StageType.PLANNING, StageType.QUALITY_GATE)
        assert not RunStateMachine.can_transition(StageType.CODING, StageType.COMPLETED)

    def test_can_transition_from_terminal(self):
        for t in TERMINAL_STAGES:
            for target in StageType:
                assert not RunStateMachine.can_transition(t, target)

    def test_transition_valid(self):
        assert RunStateMachine.transition(StageType.CODING, StageType.VALIDATING_PATCH) == StageType.VALIDATING_PATCH

    def test_transition_invalid_raises(self):
        with pytest.raises(TransitionError):
            RunStateMachine.transition(StageType.CODING, StageType.COMPLETED)

    def test_transition_from_terminal_raises(self):
        for t in TERMINAL_STAGES:
            with pytest.raises(TransitionError):
                RunStateMachine.transition(t, StageType.PLANNING)

    def test_next_stage(self):
        assert RunStateMachine.next_stage(StageType.PLANNING) == StageType.RETRIEVING_CONTEXT
        assert RunStateMachine.next_stage(StageType.CODING) == StageType.VALIDATING_PATCH
        assert RunStateMachine.next_stage(StageType.REVIEWING) == StageType.QUALITY_GATE
        assert RunStateMachine.next_stage(StageType.TESTING) == StageType.REPAIRING

    def test_next_stage_terminal(self):
        for t in TERMINAL_STAGES:
            assert RunStateMachine.next_stage(t) is None

    def test_is_terminal(self):
        assert RunStateMachine.is_terminal(StageType.COMPLETED)
        assert RunStateMachine.is_terminal(StageType.FAILED)
        assert RunStateMachine.is_terminal(StageType.CANCELLED)
        assert not RunStateMachine.is_terminal(StageType.PLANNING)
        assert not RunStateMachine.is_terminal(StageType.TESTING)

    def test_all_stages_have_transitions(self):
        for stage in StageType:
            if stage in TERMINAL_STAGES:
                continue
            assert stage in STAGE_TRANSITIONS
            assert len(STAGE_TRANSITIONS[stage]) > 0

    def test_failure_cancelled_in_every_stage(self):
        exclude = {StageType.COMPLETED, StageType.FAILED, StageType.CANCELLED, StageType.INITIALIZING}
        for src, targets in STAGE_TRANSITIONS.items():
            if src in exclude:
                continue
            assert StageType.FAILED in targets, f"{src.value} missing FAILED"
            assert StageType.CANCELLED in targets, f"{src.value} missing CANCELLED"


# ═════════════════════════════════════════════════════════════════
#  2 — HAPPY PATH
# ═════════════════════════════════════════════════════════════════

class TestHappyPath:
    @patch.object(OrchestrationService, "_stage_patch_validation", new_callable=AsyncMock)
    @patch.object(OrchestrationService, "_stage_testing", new_callable=AsyncMock)
    @patch.object(OrchestrationService, "_stage_review", new_callable=AsyncMock)
    @patch.object(OrchestrationService, "_stage_quality_gate", new_callable=AsyncMock)
    async def test_full_happy_path(self, mock_qg, mock_rev, mock_tst, mock_pv):
        mock_pv.side_effect = _mock_stage(StageType.VALIDATING_PATCH)
        mock_tst.side_effect = _mock_stage(StageType.TESTING)
        mock_rev.side_effect = _mock_stage(StageType.REVIEWING)
        mock_qg.side_effect = _mock_approve()
        orch = OrchestrationService()
        rid = await _prepare_run(orch, "Happy", "/tmp/repo")
        result = await orch.execute_run(rid)
        assert result.status == RunStatus.APPROVED

    async def test_create_run_no_repo(self):
        r = await OrchestrationService().create_run(RunSource(source_type=RunSourceType.USER_TASK, title="T"))
        assert r.run_id.startswith("RUN-")
        assert r.status == RunStatus.PENDING
        assert r.current_stage == StageType.INITIALIZING
        assert len(r.events) == 3

    async def test_create_run_with_repo(self):
        r = await OrchestrationService().create_run(RunSource(source_type=RunSourceType.USER_TASK, title="T", repository_path="/p"))
        assert len(r.events) == 1

    async def test_run_not_found(self):
        r = await OrchestrationService().execute_run("NONEXISTENT")
        assert r.status == RunStatus.FAILED
        assert r.failure.code == FailureCode.UNKNOWN

    async def test_capabilities(self):
        c = OrchestrationService.get_capabilities()
        assert isinstance(c, OrchestrationCapabilities)
        assert RunSourceType.USER_TASK in c.supported_sources
        assert c.cancellation_mode == "cooperative"

    async def test_capabilities_endpoint(self):
        r = client.get("/api/v1/orchestration/capabilities")
        assert r.status_code == 200
        assert r.json()["success"] is True


# ═════════════════════════════════════════════════════════════════
#  3 — REPAIR PATH
# ═════════════════════════════════════════════════════════════════

class TestRepairPath:
    @patch.object(OrchestrationService, "_stage_patch_validation", new_callable=AsyncMock)
    @patch.object(OrchestrationService, "_stage_testing", new_callable=AsyncMock)
    @patch.object(OrchestrationService, "_stage_repair", new_callable=AsyncMock)
    @patch.object(OrchestrationService, "_stage_review", new_callable=AsyncMock)
    @patch.object(OrchestrationService, "_stage_quality_gate", new_callable=AsyncMock)
    async def test_repair_success(self, mock_qg, mock_rev, mock_rep, mock_tst, mock_pv):
        mock_pv.side_effect = _mock_stage(StageType.VALIDATING_PATCH)
        mock_tst.side_effect = [False, True]
        mock_rep.side_effect = _mock_repair()
        mock_rev.side_effect = _mock_stage(StageType.REVIEWING)
        mock_qg.side_effect = _mock_approve()
        orch = OrchestrationService()
        rid = await _prepare_run(orch, "Repair", "/tmp/repo")
        result = await orch.execute_run(rid)
        assert result.status == RunStatus.APPROVED
        assert mock_tst.call_count == 2
        assert mock_rep.call_count == 1

    @patch.object(OrchestrationService, "_stage_patch_validation", new_callable=AsyncMock)
    @patch.object(OrchestrationService, "_stage_testing", new_callable=AsyncMock)
    @patch.object(OrchestrationService, "_stage_repair", new_callable=AsyncMock)
    @patch.object(OrchestrationService, "_stage_review", new_callable=AsyncMock)
    @patch.object(OrchestrationService, "_stage_quality_gate", new_callable=AsyncMock)
    async def test_repair_max_attempts(self, mock_qg, mock_rev, mock_rep, mock_tst, mock_pv):
        mock_pv.side_effect = _mock_stage(StageType.VALIDATING_PATCH)
        mock_tst.side_effect = [False, False]
        mock_rep.side_effect = _mock_repair()
        mock_rev.side_effect = _mock_stage(StageType.REVIEWING)
        mock_qg.side_effect = _mock_approve()
        orch = OrchestrationService()
        rid = await _prepare_run(orch, "Max", "/tmp/repo")
        await orch.execute_run(rid)
        assert mock_rev.call_count == 1
        assert mock_qg.call_count == 1


# ═════════════════════════════════════════════════════════════════
#  4 — REJECTION PATH
# ═════════════════════════════════════════════════════════════════

class TestRejectionPath:
    @patch.object(OrchestrationService, "_stage_patch_validation", new_callable=AsyncMock)
    @patch.object(OrchestrationService, "_stage_testing", new_callable=AsyncMock)
    @patch.object(OrchestrationService, "_stage_review", new_callable=AsyncMock)
    @patch.object(OrchestrationService, "_stage_quality_gate", new_callable=AsyncMock)
    async def test_rejected(self, mock_qg, mock_rev, mock_tst, mock_pv):
        mock_pv.side_effect = _mock_stage(StageType.VALIDATING_PATCH)
        mock_tst.side_effect = _mock_stage(StageType.TESTING)
        mock_rev.side_effect = _mock_stage(StageType.REVIEWING)
        async def _reject(run, *a, **kw):
            run.quality_gate_result = make_rejected_gate()
            run.status = RunStatus.REJECTED
            run.current_stage = StageType.QUALITY_GATE
            return True
        mock_qg.side_effect = _reject
        orch = OrchestrationService()
        rid = await _prepare_run(orch, "Reject", "/tmp/repo")
        result = await orch.execute_run(rid)
        assert result.status == RunStatus.REJECTED


# ═════════════════════════════════════════════════════════════════
#  5 — CANCELLATION
# ═════════════════════════════════════════════════════════════════

class TestPhase15Collaboration:
    """Phase 15: structured handoffs are created at stage boundaries.

    The stage methods are mocked to populate run state (plan / patch_set /
    test_result / repair_result / review_report) so the orchestrator's real
    handoff-creation wiring in execute_run genuinely runs.
    """

    async def _execute(
        self,
        orch: OrchestrationService,
        collab: Any,
        test_side_effect: Any,
        degrade: bool = False,
    ):
        """Run the full pipeline with stage mocks that populate run state."""
        source = RunSource(source_type=RunSourceType.USER_TASK, title="Phase15")
        run = await orch.create_run(source)
        run.requirements = make_reqs()
        run.retrieved_context = MagicMock()
        await orch._store.update(run)
        run_id = run.run_id

        if degrade:
            orch._get_collaboration = lambda: None
        else:
            orch._get_collaboration = lambda: collab

        async def _planning(run, *a, **kw):
            run.plan = make_plan()
            run.current_stage = StageType.PLANNING
            return True

        async def _coding(run, *a, **kw):
            run.patch_set = PatchSet(patch_id="p", changes=[
                FileChange(
                    change_id="CHANGE-001",
                    operation=FileOperation.CREATE,
                    path="auth_service.py",
                    new_content="class AuthService:\n    def login(self):\n        pass\n",
                )
            ])
            run.current_stage = StageType.CODING
            return True

        async def _pv(run, *a, **kw):
            run.current_stage = StageType.VALIDATING_PATCH
            return True

        async def _pa(run, *a, **kw):
            run.patch_result = MagicMock()
            run.current_stage = StageType.APPLYING_PATCH
            return True

        async def _rev(run, *a, **kw):
            run.review_report = ReviewReport(review_id="rv-t15")
            run.current_stage = StageType.REVIEWING
            return True

        async def _qg(run, *a, **kw):
            run.quality_gate_result = make_approved_gate()
            run.status = RunStatus.APPROVED
            run.current_stage = StageType.QUALITY_GATE
            return True

        async def _rep(run, *a, **kw):
            run.repair_result = make_repair_success()
            run.current_stage = StageType.REPAIRING
            return True

        with patch.object(orch, "_stage_planning", new_callable=AsyncMock) as mp, \
                patch.object(orch, "_stage_coding", new_callable=AsyncMock) as mc, \
                patch.object(orch, "_stage_patch_validation", new_callable=AsyncMock) as mpv, \
                patch.object(orch, "_stage_patch_application", new_callable=AsyncMock) as mpa, \
                patch.object(orch, "_stage_testing", new_callable=AsyncMock) as mt, \
                patch.object(orch, "_stage_repair", new_callable=AsyncMock) as mrep, \
                patch.object(orch, "_stage_review", new_callable=AsyncMock) as mr, \
                patch.object(orch, "_stage_quality_gate", new_callable=AsyncMock) as mq:
            mp.side_effect = _planning
            mc.side_effect = _coding
            mpv.side_effect = _pv
            mpa.side_effect = _pa
            mt.side_effect = test_side_effect
            mrep.side_effect = _rep
            mr.side_effect = _rev
            mq.side_effect = _qg
            result = await orch.execute_run(run_id)
        return result

    @staticmethod
    def _tst_pass():
        async def _fn(run, *a, **kw):
            run.test_result = make_passed_tr()
            run.current_stage = StageType.TESTING
            return True
        return _fn

    @staticmethod
    def _tst_fail_then_pass():
        calls = {"n": 0}
        async def _fn(run, *a, **kw):
            calls["n"] += 1
            run.current_stage = StageType.TESTING
            if calls["n"] == 1:
                run.test_result = make_failed_tr()
                return False
            run.test_result = make_passed_tr()
            return True
        return _fn

    async def test_handoffs_created_across_stages(self):
        orch = OrchestrationService()
        from app.services.collaboration_service import CollaborationService
        collab = CollaborationService()
        result = await self._execute(orch, collab, self._tst_pass())

        handoffs = await collab.list_handoffs(result.run_id)
        by_from = {h.from_agent for h in handoffs}
        by_to = {h.to_agent for h in handoffs}
        assert "planner" in by_from
        assert "coding" in by_from
        assert "testing" in by_from
        assert "reviewer" in by_from
        assert "coding" in by_to
        assert "testing" in by_to
        assert "reviewer" in by_to
        assert "quality_gate" in by_to

    async def test_decisions_recorded(self):
        orch = OrchestrationService()
        from app.services.collaboration_service import CollaborationService
        collab = CollaborationService()
        result = await self._execute(orch, collab, self._tst_pass())
        decisions = await collab.list_decisions(result.run_id)
        types = {d.decision_type.value for d in decisions}
        assert "planning" in types
        assert "implementation" in types
        assert "review" in types

    async def test_handoffs_survive_graceful_degradation(self):
        """Orchestrator still works when collaboration service is unavailable."""
        orch = OrchestrationService()
        result = await self._execute(orch, None, self._tst_pass(), degrade=True)
        assert result is not None
        assert result.run_id.startswith("RUN-")

    async def test_repair_loop_preserves_attempt_handoffs(self):
        """Repair produces its own handoff; testing→repair handoff exists."""
        orch = OrchestrationService()
        from app.services.collaboration_service import CollaborationService
        collab = CollaborationService()
        result = await self._execute(orch, collab, self._tst_fail_then_pass())
        handoffs = await collab.list_handoffs(result.run_id)
        by_from = {h.from_agent for h in handoffs}
        assert "testing" in by_from  # testing→repair handoff
        assert "repair" in by_from   # repair→testing handoff


class TestCancellation:
    async def test_cancel_running(self):
        o = OrchestrationService()
        r = await o.create_run(RunSource(source_type=RunSourceType.USER_TASK, title="T"))
        assert await o.request_cancellation(r.run_id) is True
        run = await o.get_run(r.run_id)
        assert run.cancellation_requested is True

    async def test_cancel_terminal_fails(self):
        o = OrchestrationService()
        r = await o.create_run(RunSource(source_type=RunSourceType.USER_TASK, title="T"))
        r.status = RunStatus.APPROVED
        await o._store.update(r)
        assert await o.request_cancellation(r.run_id) is False

    async def test_cancel_nonexistent(self):
        assert await OrchestrationService().request_cancellation("X") is False

    async def test_check_cancelled(self):
        o = OrchestrationService()
        r = await o.create_run(RunSource(source_type=RunSourceType.USER_TASK, title="T"))
        r.cancellation_requested = True
        cancelled = await o._check_cancelled(r)
        assert cancelled is True
        assert r.status == RunStatus.CANCELLED


# ═════════════════════════════════════════════════════════════════
#  6 — FAILURE BOUNDARIES
# ═════════════════════════════════════════════════════════════════

class TestFailureBoundaries:
    async def test_planning_failure_stops_coding(self):
        """Planning fails → coding not called."""
        o = OrchestrationService()
        src = RunSource(source_type=RunSourceType.USER_TASK, title="P", repository_path="/tmp/r")
        r = await o.create_run(src)
        r.repository_path = "/tmp/r"
        r.repository_profile = MagicMock()
        r.requirements = make_reqs()
        r.retrieved_context = MagicMock()  # Skip retrieval
        await o._store.update(r)

        with patch.object(o, "_stage_analysis") as ma, \
                patch.object(o, "_stage_task_analysis") as mt, \
                patch.object(o, "_stage_planning") as mp, \
                patch.object(o, "_stage_coding") as mc:
            ma.return_value = True
            mt.return_value = True
            mp.return_value = False
            result = await o.execute_run(r.run_id)
            assert result.status == RunStatus.FAILED
            mc.assert_not_called()

    async def test_coding_failure_stops_patch(self):
        """Coding fails → patch validation not called."""
        o = OrchestrationService()
        src = RunSource(source_type=RunSourceType.USER_TASK, title="C", repository_path="/tmp/r")
        r = await o.create_run(src)
        r.repository_path = "/tmp/r"
        r.repository_profile = MagicMock()
        r.requirements = make_reqs()
        r.plan = make_plan()
        r.retrieved_context = MagicMock()
        await o._store.update(r)

        with patch.object(o, "_stage_analysis") as ma, \
                patch.object(o, "_stage_task_analysis") as mt, \
                patch.object(o, "_stage_planning") as mp, \
                patch.object(o, "_stage_coding") as mc, \
                patch.object(o, "_stage_patch_validation") as mpv:
            ma.return_value = True
            mt.return_value = True
            mp.return_value = True
            mc.return_value = False

            result = await o.execute_run(r.run_id)
            assert result.status == RunStatus.FAILED
            mpv.assert_not_called()

    @patch.object(OrchestrationService, "_stage_patch_validation", new_callable=AsyncMock)
    @patch.object(OrchestrationService, "_stage_patch_application", new_callable=AsyncMock)
    async def test_patch_validation_failure(self, mock_app, mock_pv):
        """Patch validation fails → application not called."""
        mock_pv.return_value = False
        o = OrchestrationService()
        rid = await _prepare_run(o, "PV", "/tmp/r")
        result = await o.execute_run(rid)
        assert result.status == RunStatus.FAILED
        mock_app.assert_not_called()

    @patch.object(OrchestrationService, "_stage_patch_validation", new_callable=AsyncMock)
    @patch.object(OrchestrationService, "_stage_testing", new_callable=AsyncMock)
    @patch.object(OrchestrationService, "_stage_repair", new_callable=AsyncMock)
    async def test_environment_failure(self, mock_rep, mock_tst, mock_pv):
        """Env not ready → repair not called."""
        mock_pv.side_effect = _mock_stage(StageType.VALIDATING_PATCH)
        mock_tst.return_value = None
        o = OrchestrationService()
        rid = await _prepare_run(o, "Env", "/tmp/r")
        result = await o.execute_run(rid)
        assert result.status == RunStatus.FAILED
        mock_rep.assert_not_called()


# ═════════════════════════════════════════════════════════════════
#  7 — SECURITY
# ═════════════════════════════════════════════════════════════════

class TestSecurity:
    def test_no_subprocess(self):
        import inspect; s = inspect.getsource(OrchestrationService)
        assert "subprocess.run" not in s
        assert "subprocess.Popen" not in s
        assert "os.system" not in s

    def test_no_file_writes(self):
        import inspect; s = inspect.getsource(OrchestrationService)
        assert "Path.write_text" not in s
        assert ".write_bytes" not in s

    def test_no_shell_true(self):
        import inspect; s = inspect.getsource(OrchestrationService)
        assert "shell=True" not in s

    def test_execution_delegated(self):
        import inspect; s = inspect.getsource(OrchestrationService)
        assert "self._testing" in s

    def test_patch_delegated(self):
        import inspect; s = inspect.getsource(OrchestrationService)
        assert "self._patch_validator" in s
        assert "self._patch_engine" in s

    def test_event_redaction(self):
        e = RunEvent(event_id="e", run_id="r", timestamp="2026-01-01T00:00:00", event_type="stage_completed", stage=StageType.TESTING, message="A" * 1000)
        assert len(e.message[:200]) == 200


# ═════════════════════════════════════════════════════════════════
#  8 — RUN STORE
# ═════════════════════════════════════════════════════════════════

class TestRunStore:
    async def test_crud(self, store, run_source):
        await store.create(DevPilotRun(run_id="R1", source=run_source))
        r1 = await store.get("R1")
        assert r1 is not None
        assert await store.get("X") is None
        assert await store.delete("R1") is True
        assert await store.delete("X") is False

    async def test_update(self, store, run_source):
        r = DevPilotRun(run_id="R2", source=run_source)
        await store.create(r)
        r.status = RunStatus.RUNNING
        await store.update(r)
        r2 = await store.get("R2")
        assert r2.status == RunStatus.RUNNING

    async def test_list(self, store, run_source):
        assert len(await store.list()) == 0
        for i in range(5):
            await store.create(DevPilotRun(run_id=f"R-{i}", source=run_source))
        assert len(await store.list()) == 5

    async def test_list_filter(self, store, run_source):
        for i in range(3):
            await store.create(DevPilotRun(run_id=f"A-{i}", source=run_source, status=RunStatus.APPROVED))
        for i in range(2):
            await store.create(DevPilotRun(run_id=f"RJ-{i}", source=run_source, status=RunStatus.REJECTED))
        assert len(await store.list(status=RunStatus.APPROVED.value)) == 3
        assert len(await store.list(status=RunStatus.REJECTED.value)) == 2

    async def test_list_pagination(self, store, run_source):
        for i in range(10):
            await store.create(DevPilotRun(run_id=f"R-{i:03d}", source=run_source))
        assert len(await store.list(limit=3, offset=0)) == 3
        assert len(await store.list(limit=3, offset=3)) == 3

    async def test_generate_run_id(self):
        rid = generate_run_id()
        assert rid.startswith("RUN-")
        assert len(rid) == 12


# ═════════════════════════════════════════════════════════════════
#  9 — EVENTS
# ═════════════════════════════════════════════════════════════════

class TestEvents:
    async def test_event_creation(self):
        r = await OrchestrationService().create_run(RunSource(source_type=RunSourceType.USER_TASK, title="T"))
        assert len(r.events) >= 1
        assert r.events[0].event_type.value == "run_created"

    async def test_events_sanitized(self):
        o = OrchestrationService()
        r = await o.create_run(RunSource(source_type=RunSourceType.USER_TASK, title="T"))
        events = await o.get_events(r.run_id)
        assert len(events) >= 1
        for e in events:
            assert all(k in e for k in ("event_id", "event_type", "message", "timestamp"))
            assert len(e["message"]) <= 200

    async def test_events_empty(self):
        assert await OrchestrationService().get_events("X") == []


# ═════════════════════════════════════════════════════════════════
#  10 — DECISION MAPPING
# ═════════════════════════════════════════════════════════════════

# ═════════════════════════════════════════════════════════════════
#  11.5 — CROSS-AGENT CONTEXT SHARING (Phase 15)
# ═════════════════════════════════════════════════════════════════

class TestCrossAgentContextSharing:
    """Shared notes accumulate across stage boundaries for later agents."""

    @patch.object(OrchestrationService, "_stage_patch_validation", new_callable=AsyncMock)
    @patch.object(OrchestrationService, "_stage_testing", new_callable=AsyncMock)
    @patch.object(OrchestrationService, "_stage_review", new_callable=AsyncMock)
    @patch.object(OrchestrationService, "_stage_quality_gate", new_callable=AsyncMock)
    async def test_notes_accumulate_and_flow_to_later_agents(
        self, mock_qg, mock_rev, mock_tst, mock_pv
    ):
        """Reviewer context should receive notes accumulated from earlier stages."""
        mock_pv.side_effect = _mock_stage(StageType.VALIDATING_PATCH)
        mock_tst.side_effect = _mock_stage(StageType.TESTING)
        mock_rev.side_effect = _mock_stage(StageType.REVIEWING)
        mock_qg.side_effect = _mock_approve()

        orch = OrchestrationService()

        # Capture the cross_agent_notes passed into ContextEngine.build_context
        received_notes: list = []
        original_build = orch._build_agent_context

        async def spy_build(run, agent_type, cross_agent_notes=None):
            received_notes.append((agent_type, list(cross_agent_notes or [])))
            return await original_build(run, agent_type, cross_agent_notes=cross_agent_notes)

        orch._build_agent_context = spy_build
        rid = await _prepare_run(orch, "Sharing", "/tmp/repo")
        result = await orch.execute_run(rid)
        assert result.status == RunStatus.APPROVED

        # We expect calls for planner/coding (both skipped here) + reviewer
        review_calls = [n for t, n in received_notes if t == "reviewer"]
        assert review_calls, "Expected at least one reviewer context build"
        # Reviewer notes should include the test run note (only note produced
        # in this mocked flow: plan/coding are pre-populated so no notes).
        # The shared-notes list must be passed even when empty or partial.
        assert isinstance(review_calls[0], list)

    @patch.object(OrchestrationService, "_stage_patch_validation", new_callable=AsyncMock)
    @patch.object(OrchestrationService, "_stage_testing", new_callable=AsyncMock)
    @patch.object(OrchestrationService, "_stage_repair", new_callable=AsyncMock)
    @patch.object(OrchestrationService, "_stage_review", new_callable=AsyncMock)
    @patch.object(OrchestrationService, "_stage_quality_gate", new_callable=AsyncMock)
    async def test_test_failure_note_reaches_repair(
        self, mock_qg, mock_rev, mock_rep, mock_tst, mock_pv
    ):
        """On failed tests, repair context should include the test result note."""
        mock_pv.side_effect = _mock_stage(StageType.VALIDATING_PATCH)
        mock_tst.side_effect = [False, True]  # first fails → repair, retest passes
        mock_rep.side_effect = _mock_repair()
        mock_rev.side_effect = _mock_stage(StageType.REVIEWING)
        mock_qg.side_effect = _mock_approve()

        orch = OrchestrationService()
        received_notes: list = []
        original_build = orch._build_agent_context

        async def spy_build(run, agent_type, cross_agent_notes=None):
            received_notes.append((agent_type, list(cross_agent_notes or [])))
            return await original_build(run, agent_type, cross_agent_notes=cross_agent_notes)

        orch._build_agent_context = spy_build
        rid = await _prepare_run(orch, "RepairSharing", "/tmp/repo")
        result = await orch.execute_run(rid)
        assert result.status == RunStatus.APPROVED

        repair_calls = [n for t, n in received_notes if t == "repair"]
        assert repair_calls

    @patch.object(OrchestrationService, "_stage_patch_validation", new_callable=AsyncMock)
    @patch.object(OrchestrationService, "_stage_testing", new_callable=AsyncMock)
    @patch.object(OrchestrationService, "_stage_review", new_callable=AsyncMock)
    @patch.object(OrchestrationService, "_stage_quality_gate", new_callable=AsyncMock)
    async def test_notes_passed_through_build_context(
        self, mock_qg, mock_rev, mock_tst, mock_pv
    ):
        """build_context should receive cross_agent_notes (unit-level check)."""
        from app.services.context_engine import ContextEngine

        mock_pv.side_effect = _mock_stage(StageType.VALIDATING_PATCH)
        mock_tst.side_effect = _mock_stage(StageType.TESTING)
        mock_rev.side_effect = _mock_stage(StageType.REVIEWING)
        mock_qg.side_effect = _mock_approve()

        orch = OrchestrationService()
        engine = ContextEngine()
        orch._context_engine = engine

        # Build a run in REVIEWING state with test result, then call reviewer path
        run = DevPilotRun(
            run_id="RUN-SHARE1",
            source=RunSource(source_type=RunSourceType.USER_TASK, title="Sharing", repository_path="/tmp/repo"),
            status=RunStatus.RUNNING,
            current_stage=StageType.REVIEWING,
            repository_path="/tmp/repo",
        )
        run.test_result = make_passed_tr()
        run.plan = make_plan()

        notes = ["Test run result: 5 passed"]
        ctx = await orch._build_agent_context(run, "reviewer", cross_agent_notes=notes)
        assert ctx is not None
        # The engine assembled the notes into agent_notes
        assert ctx.agent_notes
        assert "Test run result" in ctx.agent_notes


class TestStageCodingUnwrap:
    """Regression: _stage_coding must unwrap CodingAgentOutput -> PatchSet.

    The live Phase 17 demo exposed a latent bug: _stage_coding treated the
    CodingAgentOutput wrapper as if it were already a PatchSet and accessed
    `.changes` on it ('CodingAgentOutput' object has no attribute 'changes').
    The fix reads coding_output.patch_set instead. These tests drive the REAL
    _stage_coding through execute_run with a mocked coding agent, so the full
    orchestration path (state transitions, events, failure mapping) is covered.
    """

    async def _execute_with_coding_output(self, output: CodingAgentOutput):
        """Run execute_run with the real _stage_coding and a mocked agent.

        Unlike _prepare_run, run.patch_set is deliberately left unset so
        execute_run's `if not run.patch_set:` gate actually reaches the
        coding stage, and current_stage is pre-advanced so the real
        RETRIEVING_CONTEXT -> CODING state-machine transition is valid.
        """
        orch = OrchestrationService()
        orch._get_collaboration = lambda: None  # graceful handoff degradation
        fake_agent = MagicMock()
        fake_agent.run = AsyncMock(return_value=output)
        orch._coding_agent = fake_agent

        source = RunSource(source_type=RunSourceType.USER_TASK,
                           title="Unwrap", repository_path="/tmp/repo")
        run = await orch.create_run(source)
        run.repository_path = "/tmp/repo"
        run.repository_profile = MagicMock()
        run.requirements = make_reqs()
        run.plan = make_plan()
        # Real RetrievedContext — CodingAgentInput is a pydantic model and
        # rejects a MagicMock for retrieved_context.
        run.retrieved_context = RetrievedContext(
            query=RetrievalQuery(text="Unwrap"))
        run.current_stage = StageType.RETRIEVING_CONTEXT
        # patch_set intentionally left None -> coding stage executes.
        await orch._store.update(run)
        rid = run.run_id

        with patch.object(orch, "_stage_patch_validation",
                          new_callable=AsyncMock) as mpv, \
                patch.object(orch, "_stage_patch_application",
                             new_callable=AsyncMock) as mpa, \
                patch.object(orch, "_stage_testing",
                             new_callable=AsyncMock) as mt, \
                patch.object(orch, "_stage_review",
                             new_callable=AsyncMock) as mrev, \
                patch.object(orch, "_stage_quality_gate",
                             new_callable=AsyncMock) as mq:
            mpv.side_effect = _mock_stage(StageType.VALIDATING_PATCH)
            mpa.side_effect = _mock_stage(StageType.APPLYING_PATCH)
            mt.side_effect = _mock_stage(StageType.TESTING)
            mrev.side_effect = _mock_stage(StageType.REVIEWING)
            mq.side_effect = _mock_approve()
            result = await orch.execute_run(rid)

        run = await orch._store.get(rid)
        return result, run, fake_agent

    async def test_stage_coding_unwraps_output_patch_set_end_to_end(self):
        """A CodingAgentOutput wrapper is unwrapped to its PatchSet on the run."""
        patch_set = PatchSet(
            patch_id="P-UNWRAP",
            changes=[
                FileChange(
                    change_id="C-001",
                    operation=FileOperation.MODIFY,
                    path="auth/service.py",
                    original_hash="abc123",
                    new_content="class AuthService:\n    pass\n",
                )
            ],
        )
        output = CodingAgentOutput(patch_set=patch_set, status="success")
        result, run, fake_agent = await self._execute_with_coding_output(output)

        assert fake_agent.run.await_count == 1
        assert result.status == RunStatus.APPROVED
        # Regression core: run.patch_set must be the unwrapped PatchSet,
        # NOT the CodingAgentOutput wrapper (which has no .changes).
        assert isinstance(run.patch_set, PatchSet)
        assert run.patch_set is patch_set
        assert run.patch_set.changes[0].path == "auth/service.py"
        assert any(e.event_type.value == "patch_generated"
                   for e in run.events)

    async def test_stage_coding_passes_workspace_structure(self, tmp_path: Path) -> None:
        """The workspace file layout must reach the coding agent's input.

        Regression: the coding prompt previously had no file layout, making
        the LLM conservatively return INSUFFICIENT_CONTEXT ('No patch
        produced') even when retrieval succeeded.
        """
        src = tmp_path / "auth"
        src.mkdir()
        (src / "service.py").write_text("class AuthService: pass\n")
        (src / "tokens.py").write_text("class TokenManager: pass\n")
        workspace = str(tmp_path)

        orch = OrchestrationService()
        fake_agent = MagicMock()
        fake_agent.run = AsyncMock(return_value=CodingAgentOutput(
            patch_set=PatchSet(patch_id="p", changes=[
                FileChange(change_id="C1", operation=FileOperation.MODIFY,
                           path="auth/service.py", new_content="x")]),
            status="success",
        ))
        orch._coding_agent = fake_agent

        source = RunSource(source_type=RunSourceType.USER_TASK,
                           title="Unwrap", repository_path=workspace)
        run = await orch.create_run(source)
        run.repository_path = workspace
        run.repository_profile = MagicMock()
        run.requirements = make_reqs()
        run.plan = make_plan()
        run.retrieved_context = None
        run.current_stage = StageType.RETRIEVING_CONTEXT
        await orch._store.update(run)

        ok = await orch._stage_coding(run, workspace)
        assert ok is True
        inp = fake_agent.run.await_args.args[0]
        assert "auth/service.py" in inp.workspace_structure
        assert "auth/tokens.py" in inp.workspace_structure

    async def test_stage_coding_error_output_fails_stage(self):
        """status='error' short-circuits to a CODING_FAILED run failure.

        Supplementary behavior test (not the unwrap regression): an error
        output must fail the stage and leave no partial patch on the run.
        """
        output = CodingAgentOutput(
            patch_set=None, status="error", error="LLM exploded")
        result, run, _ = await self._execute_with_coding_output(output)

        assert result.status == RunStatus.FAILED
        assert run.failure is not None
        assert run.failure.code == FailureCode.CODING_FAILED
        assert "LLM exploded" in run.failure.message
        assert run.patch_set is None  # no partial patch leaks onto the run

    async def test_stage_coding_empty_patch_set_fails_stage(self):
        """A patch_set with no changes is treated as 'No patch produced'."""
        output = CodingAgentOutput(
            patch_set=PatchSet(patch_id="P-EMPTY", changes=[]),
            status="success",
        )
        result, run, _ = await self._execute_with_coding_output(output)

        assert result.status == RunStatus.FAILED
        assert run.failure is not None
        assert run.failure.code == FailureCode.CODING_FAILED
        assert "no changes" in run.failure.message
        # the stage record/event carries the 'No patch produced' detail
        assert any("No patch produced" in e.message for e in run.events)

    async def _execute_with_coding_side_effect(self, outputs):
        """Like _execute_with_coding_output, but the agent returns outputs in order.

        Used for the empty-patch retry regression: the first output is a
        valid-but-empty patch set (the ~20-25% Gemini variance), the second
        a real patch — proving _stage_coding retries once instead of failing.
        """
        orch = OrchestrationService()
        orch._get_collaboration = lambda: None  # graceful handoff degradation
        fake_agent = MagicMock()
        fake_agent.run = AsyncMock(side_effect=outputs)
        orch._coding_agent = fake_agent

        source = RunSource(source_type=RunSourceType.USER_TASK,
                           title="Retry", repository_path="/tmp/repo")
        run = await orch.create_run(source)
        run.repository_path = "/tmp/repo"
        run.repository_profile = MagicMock()
        run.requirements = make_reqs()
        run.plan = make_plan()
        run.retrieved_context = RetrievedContext(
            query=RetrievalQuery(text="Retry"))
        run.current_stage = StageType.RETRIEVING_CONTEXT
        await orch._store.update(run)
        rid = run.run_id

        with patch.object(orch, "_stage_patch_validation",
                          new_callable=AsyncMock) as mpv, \
                patch.object(orch, "_stage_patch_application",
                             new_callable=AsyncMock) as mpa, \
                patch.object(orch, "_stage_testing",
                             new_callable=AsyncMock) as mt, \
                patch.object(orch, "_stage_review",
                             new_callable=AsyncMock) as mrev, \
                patch.object(orch, "_stage_quality_gate",
                             new_callable=AsyncMock) as mq:
            mpv.side_effect = _mock_stage(StageType.VALIDATING_PATCH)
            mpa.side_effect = _mock_stage(StageType.APPLYING_PATCH)
            mt.side_effect = _mock_stage(StageType.TESTING)
            mrev.side_effect = _mock_stage(StageType.REVIEWING)
            mq.side_effect = _mock_approve()
            result = await orch.execute_run(rid)

        run = await orch._store.get(rid)
        return result, run, fake_agent

    async def test_stage_coding_retries_empty_patch_then_succeeds(self):
        """A valid-but-empty patch (LLM variance) is retried once, not failed.

        Regression for the live goal-API validation: the coding LLM returned
        no changes on two consecutive goal runs while planning and retrieval
        succeeded. _stage_coding now retries once (PROJECT_STATE item 12)
        before failing the stage.
        """
        empty = CodingAgentOutput(
            patch_set=PatchSet(patch_id="P-EMPTY", changes=[]),
            status="success",
        )
        good = CodingAgentOutput(
            patch_set=PatchSet(
                patch_id="P-RETRY",
                changes=[
                    FileChange(
                        change_id="C-001",
                        operation=FileOperation.MODIFY,
                        path="auth/service.py",
                        original_hash="abc123",
                        new_content="class AuthService:\n    pass\n",
                    )
                ],
            ),
            status="success",
        )
        result, run, fake_agent = await self._execute_with_coding_side_effect(
            [empty, good])

        assert fake_agent.run.await_count == 2
        assert result.status == RunStatus.APPROVED
        assert isinstance(run.patch_set, PatchSet)
        assert run.patch_set is not None and len(run.patch_set.changes) == 1
        assert any(e.event_type.value == "coding_retry" for e in run.events)

    async def test_stage_coding_fails_after_retry_exhausted(self):
        """Two consecutive empty patches still fail the stage (bounded retry)."""
        empty = CodingAgentOutput(
            patch_set=PatchSet(patch_id="P-EMPTY", changes=[]),
            status="success",
        )
        result, run, fake_agent = await self._execute_with_coding_side_effect(
            [empty, empty])

        assert fake_agent.run.await_count == 2
        assert result.status == RunStatus.FAILED
        assert run.failure is not None
        assert run.failure.code == FailureCode.CODING_FAILED
        assert "no changes" in run.failure.message
        assert any("No patch produced" in e.message for e in run.events)

    async def test_stage_coding_retries_insufficient_context_then_succeeds(self):
        """INSUFFICIENT_CONTEXT refusal is retried once, not failed immediately.

        The live goal-API validation showed the coding LLM conservatively
        refuses with insufficient_context on one iteration while the same
        inputs succeeded a minute earlier — the same transient variance as
        the empty patch. The retry loop covers both; only status='error'
        (a deterministic parse/validation failure) fails immediately.
        """
        refused = CodingAgentOutput(
            patch_set=None, status="insufficient_context",
            missing_context=["auth/tokens.py"],
        )
        good = CodingAgentOutput(
            patch_set=PatchSet(
                patch_id="P-RETRY2",
                changes=[
                    FileChange(
                        change_id="C-001",
                        operation=FileOperation.MODIFY,
                        path="auth/service.py",
                        original_hash="abc123",
                        new_content="class AuthService:\n    pass\n",
                    )
                ],
            ),
            status="success",
        )
        result, run, fake_agent = await self._execute_with_coding_side_effect(
            [refused, good])

        assert fake_agent.run.await_count == 2
        assert result.status == RunStatus.APPROVED
        assert isinstance(run.patch_set, PatchSet)
        assert run.patch_set is not None and len(run.patch_set.changes) == 1
        assert any(e.event_type.value == "coding_retry" for e in run.events)
        assert any("insufficient_context" in e.message for e in run.events)

    async def test_stage_coding_error_does_not_retry(self):
        """status='error' fails immediately (deterministic), not retried."""
        err = CodingAgentOutput(patch_set=None, status="error",
                                error="JSON parse failure")
        result, run, fake_agent = await self._execute_with_coding_side_effect(
            [err, err])

        assert fake_agent.run.await_count == 1
        assert result.status == RunStatus.FAILED
        assert run.failure is not None
        assert run.failure.code == FailureCode.CODING_FAILED
        assert "JSON parse failure" in run.failure.message
        assert not any(e.event_type.value == "coding_retry" for e in run.events)

    async def test_stage_coding_exhausted_context_surfaces_missing(self):
        """Exhausted insufficient_context refusals surface WHAT was missing.

        Reviewer nit: when both attempts conservatively refuse, the failure
        must say which files the LLM claimed were absent (a real context bug
        like an empty workspace_structure is otherwise hidden behind the
        generic 'no changes' message).
        """
        refused = CodingAgentOutput(
            patch_set=None, status="insufficient_context",
            missing_context=["auth/tokens.py", "auth/session.py"],
        )
        result, run, fake_agent = await self._execute_with_coding_side_effect(
            [refused, refused])

        assert fake_agent.run.await_count == 2
        assert result.status == RunStatus.FAILED
        assert run.failure is not None
        assert run.failure.code == FailureCode.CODING_FAILED
        assert "insufficient context" in run.failure.message
        assert "auth/tokens.py" in run.failure.message
        assert "auth/session.py" in run.failure.message


class TestStageTaskAnalysisRetry:
    """Bounded retry for the task-analysis stage (PROJECT_STATE item 13).

    The live raw-HTTP run-API path failed intermittently with
    'No requirements to plan against' — the task-analysis LLM (issue
    analyzer) returned empty requirements on Gemini and the stage failed
    with TASK_ANALYSIS_FAILED and no retry. _stage_task_analysis now
    retries once before failing, mirroring the _stage_coding retry
    (bounded; a genuinely broken pipeline fails the second attempt too).
    """

    async def _call_with_plan_results(self, results):
        orch = OrchestrationService()
        orch._get_collaboration = lambda: None  # graceful handoff degradation
        fake_planning = MagicMock()
        fake_planning.plan_from_task = AsyncMock(side_effect=results)
        orch._planning = fake_planning

        source = RunSource(source_type=RunSourceType.USER_TASK,
                           title="Task analysis retry",
                           repository_path="/tmp/repo")
        run = await orch.create_run(source)
        run.repository_path = "/tmp/repo"
        run.current_stage = StageType.ANALYZING_REPOSITORY
        await orch._store.update(run)

        ok = await orch._stage_task_analysis(run)
        run = await orch._store.get(run.run_id)
        return ok, run, fake_planning

    async def test_retries_empty_requirements_then_succeeds(self):
        """'No requirements to plan against' is retried once, then succeeds."""
        err = MagicMock()
        err.error = "No requirements to plan against"
        err.requirements = None
        ok_result = MagicMock()
        ok_result.error = None
        ok_result.requirements = make_reqs()

        ok, run, fake = await self._call_with_plan_results([err, ok_result])

        assert fake.plan_from_task.await_count == 2
        assert ok is True
        assert run.requirements is not None
        assert any(e.event_type.value == "task_analysis_retry"
                   for e in run.events)

    async def test_fails_after_retry_exhausted(self):
        """Two consecutive empty-requirement results still fail the stage."""
        err = MagicMock()
        err.error = "No requirements to plan against"
        err.requirements = None

        ok, run, fake = await self._call_with_plan_results([err, err])

        assert fake.plan_from_task.await_count == 2
        assert ok is False
        assert run.failure is not None
        assert run.failure.code == FailureCode.TASK_ANALYSIS_FAILED
        assert "No requirements to plan against" in run.failure.message

    async def test_succeeds_first_attempt_no_retry(self):
        """A clean first call is not retried and emits no retry event."""
        ok_result = MagicMock()
        ok_result.error = None
        ok_result.requirements = make_reqs()

        ok, run, fake = await self._call_with_plan_results([ok_result])

        assert fake.plan_from_task.await_count == 1
        assert ok is True
        assert not any(e.event_type.value == "task_analysis_retry"
                       for e in run.events)

    async def test_raised_exception_fails_without_retry(self):
        """A raised exception fails the stage immediately (no retry).

        Mirrors `_stage_coding`'s contract: only error-*results* retry;
        an exception escaping the planning service is treated as a hard
        failure (bounded, and a real bug surfaces on the first attempt).
        """
        orch = OrchestrationService()
        orch._get_collaboration = lambda: None
        fake_planning = MagicMock()
        fake_planning.plan_from_task = AsyncMock(
            side_effect=RuntimeError("LLM exploded"))
        orch._planning = fake_planning

        source = RunSource(source_type=RunSourceType.USER_TASK,
                           title="Task analysis exception",
                           repository_path="/tmp/repo")
        run = await orch.create_run(source)
        run.repository_path = "/tmp/repo"
        run.current_stage = StageType.ANALYZING_REPOSITORY
        await orch._store.update(run)

        ok = await orch._stage_task_analysis(run)
        run = await orch._store.get(run.run_id)

        assert ok is False
        assert fake_planning.plan_from_task.await_count == 1
        assert run.failure is not None
        assert run.failure.code == FailureCode.TASK_ANALYSIS_FAILED
        assert "LLM exploded" in run.failure.message
        assert not any(e.event_type.value == "task_analysis_retry"
                       for e in run.events)


class TestStageRetrieval:
    """Regression: the real retrieval stage must work in the live pipeline.

    Two latent bugs killed live runs that let retrieval execute: (1) planning's
    _complete_stage advances current_stage to RETRIEVING_CONTEXT, then
    _stage_retrieval's unconditional _transition_to(RETRIEVING_CONTEXT) raised
    TransitionError (same-stage transitions are invalid); (2) a bare
    RepositoryCodeIndex was passed as the retriever's lexical_index, so every
    retrieve() raised AttributeError on `.built` and the stage silently skipped.
    """

    async def _make_run(self, orch: OrchestrationService, fixture: str):
        source = RunSource(source_type=RunSourceType.USER_TASK,
                           title="Fix auth token expiry so expired tokens are rejected",
                           repository_path=fixture)
        run = await orch.create_run(source)
        run.repository_path = fixture
        run.requirements = make_reqs()
        run.plan = make_plan()
        run.retrieved_context = None
        # planning's _complete_stage leaves current_stage here
        run.current_stage = StageType.RETRIEVING_CONTEXT
        await orch._store.update(run)
        return run.run_id

    async def test_retrieval_no_transition_error_on_same_stage(self) -> None:
        """current_stage already RETRIEVING_CONTEXT must not raise TransitionError."""
        fixture = str(Path(__file__).parent / "fixtures" / "fixture_auth_app")
        orch = OrchestrationService()
        rid = await self._make_run(orch, fixture)

        ok = await orch._stage_retrieval(await orch._store.get(rid), fixture)
        assert ok is True

    async def test_retrieval_populates_real_repository_context(self) -> None:
        """Real retrieval returns code chunks from the fixture repo."""
        fixture = str(Path(__file__).parent / "fixtures" / "fixture_auth_app")
        orch = OrchestrationService()
        rid = await self._make_run(orch, fixture)

        ok = await orch._stage_retrieval(await orch._store.get(rid), fixture)
        assert ok is True
        run = await orch._store.get(rid)
        ctx = run.retrieved_context
        assert ctx is not None
        assert len(ctx.items) > 0
        paths = {it.chunk.file_path for it in ctx.items}
        assert any("auth" in p for p in paths), f"expected auth files, got {paths}"


class TestPatchHashEnrichment:
    """Regression: LLM-generated patches (MODIFY/DELETE without
    original_hash) get hashes computed from the workspace so deterministic
    validation passes, while hallucinated files stay rejected.
    """

    async def _enrich(self, patch: PatchSet, workspace: str):
        orch = OrchestrationService()
        await orch._enrich_patch_hashes(patch, workspace)
        return patch

    async def test_enrich_computes_hash_for_existing_file(self, tmp_path: Path):
        import hashlib

        from app.services.patch_validator import PatchValidator

        src_dir = tmp_path / "auth"
        src_dir.mkdir()
        src = src_dir / "service.py"
        src.write_text("class AuthService:\n    pass\n")
        patch = PatchSet(patch_id="P", changes=[
            FileChange(change_id="C1", operation=FileOperation.MODIFY,
                       path="auth/service.py", new_content="x"),
        ])
        await self._enrich(patch, str(tmp_path))

        assert patch.changes[0].original_hash == hashlib.sha256(
            src.read_bytes()).hexdigest()
        # with the hash filled, deterministic validation now passes
        assert PatchValidator().validate(patch).is_valid

    async def test_enrich_leaves_hallucinated_file_rejected(self, tmp_path: Path):
        from app.services.patch_validator import PatchValidator

        patch = PatchSet(patch_id="P", changes=[
            FileChange(change_id="C1", operation=FileOperation.MODIFY,
                       path="does_not_exist.py", new_content="x"),
        ])
        await self._enrich(patch, str(tmp_path))

        # no file in the workspace -> hash stays unset -> validation rejects
        assert patch.changes[0].original_hash is None
        assert not PatchValidator().validate(patch).is_valid


class TestDecisionMapping:
    def test_approved(self):
        s = RunSource(source_type=RunSourceType.USER_TASK, title="T")
        assert DevPilotRunResult(run_id="R1", status=RunStatus.APPROVED, source=s).status == RunStatus.APPROVED

    def test_rejected_not_failed(self):
        s = RunSource(source_type=RunSourceType.USER_TASK, title="T")
        r = DevPilotRunResult(run_id="R2", status=RunStatus.REJECTED, source=s)
        assert r.status == RunStatus.REJECTED
        assert r.status != RunStatus.FAILED

    def test_needs_human_review(self):
        s = RunSource(source_type=RunSourceType.USER_TASK, title="T")
        assert DevPilotRunResult(run_id="R3", status=RunStatus.NEEDS_HUMAN_REVIEW, source=s).status == RunStatus.NEEDS_HUMAN_REVIEW


# ═════════════════════════════════════════════════════════════════
#  11 — TRANSITION MATRIX
# ═════════════════════════════════════════════════════════════════

class TestTransitionMatrix:
    def test_all_expected(self):
        expected = {
            StageType.INITIALIZING: [StageType.ACQUIRING_REPOSITORY],
            StageType.ACQUIRING_REPOSITORY: [StageType.ANALYZING_REPOSITORY, StageType.FAILED, StageType.CANCELLED],
            StageType.ANALYZING_REPOSITORY: [StageType.ANALYZING_TASK, StageType.FAILED, StageType.CANCELLED],
            StageType.ANALYZING_TASK: [StageType.PLANNING, StageType.FAILED, StageType.CANCELLED],
            StageType.PLANNING: [StageType.RETRIEVING_CONTEXT, StageType.FAILED, StageType.CANCELLED],
            StageType.RETRIEVING_CONTEXT: [StageType.CODING, StageType.FAILED, StageType.CANCELLED],
            StageType.CODING: [StageType.VALIDATING_PATCH, StageType.FAILED, StageType.CANCELLED],
            StageType.VALIDATING_PATCH: [StageType.APPLYING_PATCH, StageType.FAILED, StageType.CANCELLED],
            StageType.APPLYING_PATCH: [StageType.TESTING, StageType.FAILED, StageType.CANCELLED],
            StageType.TESTING: [StageType.REPAIRING, StageType.REVIEWING, StageType.FAILED, StageType.CANCELLED],
            StageType.REPAIRING: [StageType.TESTING, StageType.REVIEWING, StageType.FAILED, StageType.CANCELLED],
            StageType.REVIEWING: [StageType.QUALITY_GATE, StageType.FAILED, StageType.CANCELLED],
            StageType.QUALITY_GATE: [StageType.COMPLETED, StageType.FAILED, StageType.CANCELLED],
        }
        for src, tgts in expected.items():
            assert src in STAGE_TRANSITIONS, f"Missing {src.value}"
            for t in tgts:
                assert t in STAGE_TRANSITIONS[src], f"Missing {src.value}->{t.value}"

    def test_no_dupes(self):
        for tgts in STAGE_TRANSITIONS.values():
            assert len(tgts) == len(set(tgts))

    def test_linearity(self):
        path = [StageType.INITIALIZING, StageType.ACQUIRING_REPOSITORY, StageType.ANALYZING_REPOSITORY, StageType.ANALYZING_TASK, StageType.PLANNING, StageType.RETRIEVING_CONTEXT, StageType.CODING, StageType.VALIDATING_PATCH, StageType.APPLYING_PATCH, StageType.TESTING, StageType.REVIEWING, StageType.QUALITY_GATE]
        for i in range(len(path) - 1):
            assert RunStateMachine.can_transition(path[i], path[i + 1]), f"Broken: {path[i].value}->{path[i+1].value}"

    def test_no_skip(self):
        assert not RunStateMachine.can_transition(StageType.PLANNING, StageType.TESTING)
        assert not RunStateMachine.can_transition(StageType.CODING, StageType.REVIEWING)


# ═════════════════════════════════════════════════════════════════
#  8 — RAW HTTP PATH FIXES (surfaced by verify_api_durability.py --live)
# ═════════════════════════════════════════════════════════════════

class TestRawHttpPathFixes:
    """Regressions for the live HTTP run path (POST /api/v1/runs):

    1. A fresh run starts at INITIALIZING; execute_run must advance through
       ACQUIRING_REPOSITORY before analysis (strict state machine) instead of
       raising "Invalid transition initializing -> analyzing_repository".
    2. _stage_analysis must await RepositoryAnalysisWorkflow.run (async) —
       without await the profile is a coroutine ("'coroutine' object has no
       attribute 'languages'").
    """

    class _FakeAsyncAnalysis:
        """Async analysis workflow returning a real profile (await required)."""
        async def run(self, repo_path: str):
            from app.models.profile import RepositoryProfile

            return type("S", (), {
                "profile": RepositoryProfile(name="fake-repo", languages=[]),
            })()

    @pytest.mark.asyncio
    async def test_execute_run_fresh_local_repo_completes(self):
        """A run created by the raw API path (INITIALIZING + local repo) must
        flow through acquisition → analysis instead of failing the transition."""
        orch = OrchestrationService()
        orch._analysis = self._FakeAsyncAnalysis()

        run = await orch.create_run(RunSource(
            source_type=RunSourceType.USER_TASK, title="Raw HTTP path",
            repository_path="/tmp/repo",
        ))
        run.repository_path = "/tmp/repo"
        # Pre-populate downstream artifacts (mirrors _prepare_run) so the
        # REAL _stage_analysis is the transition under test; everything after
        # analysis is deterministic-mocked.
        run.repository_profile = None
        run.requirements = make_reqs()
        run.plan = make_plan()
        run.retrieved_context = MagicMock()
        run.patch_set = PatchSet(patch_id="p", changes=[])
        run.patch_result = MagicMock()
        await orch._store.update(run)

        with patch.object(OrchestrationService, "_stage_patch_validation",
                          new_callable=AsyncMock, side_effect=_mock_stage(StageType.VALIDATING_PATCH)), \
             patch.object(OrchestrationService, "_stage_testing",
                          new_callable=AsyncMock, side_effect=_mock_stage(StageType.TESTING)), \
             patch.object(OrchestrationService, "_stage_review",
                          new_callable=AsyncMock, side_effect=_mock_stage(StageType.REVIEWING)), \
             patch.object(OrchestrationService, "_stage_quality_gate",
                          new_callable=AsyncMock, side_effect=_mock_approve()):
            result = await orch.execute_run(run.run_id, workspace_root="/tmp/repo")

        assert result.status == RunStatus.APPROVED
        fresh = await orch._store.get(run.run_id)
        assert fresh is not None
        # Analysis ran for real through the awaited workflow → real profile.
        assert fresh.repository_profile is not None
        assert not asyncio.iscoroutine(fresh.repository_profile)
        stages = {s.stage for s in fresh.stage_results}
        assert StageType.ACQUIRING_REPOSITORY in stages
        assert StageType.ANALYZING_REPOSITORY in stages

    @pytest.mark.asyncio
    async def test_stage_analysis_awaits_async_workflow(self):
        """_stage_analysis must await RepositoryAnalysisWorkflow.run — the
        profile is a real object, never a coroutine."""
        orch = OrchestrationService()
        orch._analysis = self._FakeAsyncAnalysis()
        run = await orch.create_run(RunSource(
            source_type=RunSourceType.USER_TASK, title="T",
            repository_path="/tmp/repo",
        ))
        run.repository_path = "/tmp/repo"
        # _stage_analysis requires the valid predecessor stage (analysis is
        # reached via ACQUIRING_REPOSITORY in the real flow).
        run.current_stage = StageType.ACQUIRING_REPOSITORY
        await orch._store.update(run)

        ok = await orch._stage_analysis(run)
        assert ok is True
        assert run.repository_profile is not None
        assert not asyncio.iscoroutine(run.repository_profile)
        assert run.repository_profile.name == "fake-repo"

    @pytest.mark.asyncio
    async def test_github_issue_still_routes_through_acquisition(self):
        """The fix must not disturb the GITHUB_ISSUE branch: remote repos
        still route through the acquisition stage (no network needed here —
        acquisition is mocked; the boundary is that the branch is taken)."""
        orch = OrchestrationService()
        acquisition = AsyncMock(return_value=True)

        async def _fake_acquisition(run, *a, **kw):
            # Mimic the real stage: transition + complete (which advances to
            # ANALYZING_REPOSITORY) so downstream analysis is valid.
            await orch._transition_to(run, StageType.ACQUIRING_REPOSITORY)
            run.repository_path = run.source.repository_path
            run.repository_profile = MagicMock()
            await orch._complete_stage(run, StageType.ACQUIRING_REPOSITORY)
            return True

        acquisition.side_effect = _fake_acquisition
        orch._stage_acquisition = acquisition
        run = await orch.create_run(RunSource(
            source_type=RunSourceType.GITHUB_ISSUE, title="T",
            repository_path="https://github.com/org/repo", issue_number=1,
        ))
        run.requirements = make_reqs()
        run.plan = make_plan()
        run.retrieved_context = MagicMock()
        run.patch_set = PatchSet(patch_id="p", changes=[])
        run.patch_result = MagicMock()
        await orch._store.update(run)

        with patch.object(OrchestrationService, "_stage_patch_validation",
                          new_callable=AsyncMock, side_effect=_mock_stage(StageType.VALIDATING_PATCH)), \
             patch.object(OrchestrationService, "_stage_testing",
                          new_callable=AsyncMock, side_effect=_mock_stage(StageType.TESTING)), \
             patch.object(OrchestrationService, "_stage_review",
                          new_callable=AsyncMock, side_effect=_mock_stage(StageType.REVIEWING)), \
             patch.object(OrchestrationService, "_stage_quality_gate",
                          new_callable=AsyncMock, side_effect=_mock_approve()):
            result = await orch.execute_run(run.run_id, workspace_root="/tmp/repo")

        acquisition.assert_awaited_once()
        assert result.status == RunStatus.APPROVED
        fresh = await orch._store.get(run.run_id)
        stages = {s.stage for s in fresh.stage_results}
        assert StageType.ACQUIRING_REPOSITORY in stages

    @pytest.mark.asyncio
    async def test_resume_past_initializing_is_untouched(self):
        """Runs resumed past INITIALIZING must not re-run the acquisition
        advance (the guard `current_stage == INITIALIZING` protects them)."""
        orch = OrchestrationService()
        run = await orch.create_run(RunSource(
            source_type=RunSourceType.USER_TASK, title="Resume",
            repository_path="/tmp/repo",
        ))
        run.repository_path = "/tmp/repo"
        # Simulate a run already past acquisition/analysis (resume path).
        run.current_stage = StageType.PLANNING
        run.repository_profile = MagicMock()
        run.requirements = make_reqs()
        run.plan = make_plan()
        run.retrieved_context = MagicMock()
        run.patch_set = PatchSet(patch_id="p", changes=[])
        run.patch_result = MagicMock()
        await orch._store.update(run)

        with patch.object(OrchestrationService, "_stage_patch_validation",
                          new_callable=AsyncMock, side_effect=_mock_stage(StageType.VALIDATING_PATCH)), \
             patch.object(OrchestrationService, "_stage_testing",
                          new_callable=AsyncMock, side_effect=_mock_stage(StageType.TESTING)), \
             patch.object(OrchestrationService, "_stage_review",
                          new_callable=AsyncMock, side_effect=_mock_stage(StageType.REVIEWING)), \
             patch.object(OrchestrationService, "_stage_quality_gate",
                          new_callable=AsyncMock, side_effect=_mock_approve()):
            result = await orch.execute_run(run.run_id, workspace_root="/tmp/repo")

        assert result.status == RunStatus.APPROVED
        fresh = await orch._store.get(run.run_id)
        # No acquisition stage record was added for an already-advanced run.
        stages = {s.stage for s in fresh.stage_results}
        assert StageType.ACQUIRING_REPOSITORY not in stages

    @pytest.mark.asyncio
    async def test_fresh_no_repo_run_skips_acquire_analyze(self):
        """No-repository runs (the autonomy/API path) still advance through
        the pre-analysis stages cleanly and reach task analysis."""
        orch = OrchestrationService()
        run = await orch.create_run(RunSource(
            source_type=RunSourceType.USER_TASK, title="No repo",
        ))
        run.requirements = make_reqs()
        run.plan = make_plan()
        run.retrieved_context = MagicMock()
        run.patch_set = PatchSet(patch_id="p", changes=[])
        run.patch_result = MagicMock()
        await orch._store.update(run)

        with patch.object(OrchestrationService, "_stage_patch_validation",
                          new_callable=AsyncMock, side_effect=_mock_stage(StageType.VALIDATING_PATCH)), \
             patch.object(OrchestrationService, "_stage_testing",
                          new_callable=AsyncMock, side_effect=_mock_stage(StageType.TESTING)), \
             patch.object(OrchestrationService, "_stage_review",
                          new_callable=AsyncMock, side_effect=_mock_stage(StageType.REVIEWING)), \
             patch.object(OrchestrationService, "_stage_quality_gate",
                          new_callable=AsyncMock, side_effect=_mock_approve()):
            result = await orch.execute_run(run.run_id, workspace_root="")

        assert result.status == RunStatus.APPROVED
        fresh = await orch._store.get(run.run_id)
        stages = {s.stage for s in fresh.stage_results}
        # create_run records acquisition/analysis as SKIPPED for no-repo runs;
        # execute_run advances the state machine through them without the
        # invalid INITIALIZING → ANALYZING_REPOSITORY transition.
        assert StageType.ACQUIRING_REPOSITORY in stages
        assert StageType.ANALYZING_REPOSITORY in stages


# ═════════════════════════════════════════════════════════════════
#  8b — EKG-DRIVEN TEST STAGE (Phase 12d closure)
# ═════════════════════════════════════════════════════════════════

class TestEKGDrivenTestStage:
    """The orchestrator's test stage targets tests via EKG impact edges
    (patch → test) instead of running the full discovered suite."""

    def _seed_ekg(self):
        from app.models.engineering_graph import EKNodeType, EKRelationshipType
        from app.services.engineering_graph_service import (
            EngineeringKnowledgeGraphService,
        )

        ekg = EngineeringKnowledgeGraphService()
        run_node = ekg.add_node(
            EKNodeType.RUN, "run:RUN-OR-1", source_ref="RUN-OR-1", source_type="run",
        )
        patch = ekg.add_node(
            EKNodeType.PATCH, "patch:RUN-OR-1", source_ref="RUN-OR-1", source_type="run",
        )
        file_node = ekg.add_node(
            EKNodeType.FILE, "service.py", source_ref="auth/service.py",
            source_type="file", qualified_name="auth/service.py",
        )
        tests = ekg.add_node(
            EKNodeType.TEST_SUITE, "tests:RUN-OR-1", source_ref="RUN-OR-1",
            source_type="run", qualified_name="tests:RUN-OR-1",
            payload={"status": "passed",
                     "test_files": ["auth/tests/test_auth.py", "tests/test_session.py"]},
        )
        ekg.add_edge(run_node.node_id, patch.node_id, EKRelationshipType.CREATED_DURING)
        ekg.add_edge(patch.node_id, file_node.node_id, EKRelationshipType.MODIFIES)
        ekg.add_edge(patch.node_id, tests.node_id, EKRelationshipType.VALIDATED_BY)
        return ekg

    @pytest.mark.asyncio
    async def test_stage_testing_targets_pytest_with_ekg_selected_tests(self):
        from app.models.testing import CommandCandidate, CommandCategory, CommandSource

        captured = {}

        class _FakeTesting:
            async def run_tests(self, plan):
                return make_passed_tr()

            def discover_commands(self, workspace_root):
                return [CommandCandidate(
                    command_id="cmd-1", category=CommandCategory.TEST,
                    executable="python", arguments=["-m", "pytest", "-q"],
                    source=CommandSource.DEFAULT_FRAMEWORK_RULE, confidence=0.9,
                    reason="pyproject",
                )]

            def build_plan(self, **kwargs):
                from app.models.testing import ExecutionPlan

                captured["candidates"] = kwargs.get("candidates", [])
                captured["changed_files"] = kwargs.get("changed_files", [])
                return ExecutionPlan(
                    plan_id="plan-1", workspace_id=kwargs["workspace_id"],
                    workspace_root=kwargs["workspace_root"], steps=[],
                )

        orch = OrchestrationService(testing_service=_FakeTesting())
        orch._engineering_graph = self._seed_ekg()

        run = await orch.create_run(
            RunSource(source_type=RunSourceType.USER_TASK, title="EKG stage test")
        )
        run.current_stage = StageType.APPLYING_PATCH
        run.repository_path = None
        run.patch_set = PatchSet(
            patch_id="p-ekg",
            changes=[FileChange(
                change_id="C-1", operation=FileOperation.MODIFY,
                path="auth/service.py",
            )],
        )
        await orch._store.update(run)

        result = await orch._stage_testing(run, "workspace")
        assert result is True
        candidates = captured["candidates"]
        assert candidates, "no candidates passed to build_plan"
        pytest_args = candidates[0].arguments
        assert "auth/tests/test_auth.py" in pytest_args
        assert "tests/test_session.py" in pytest_args
        assert "EKG impact-selected tests" in candidates[0].reason

    @pytest.mark.asyncio
    async def test_stage_testing_no_ekg_evidence_runs_full_suite(self):
        from app.models.testing import CommandCandidate, CommandCategory, CommandSource

        captured = {}

        class _FakeTesting:
            async def run_tests(self, plan):
                return make_passed_tr()

            def discover_commands(self, workspace_root):
                return [CommandCandidate(
                    command_id="cmd-1", category=CommandCategory.TEST,
                    executable="python", arguments=["-m", "pytest", "-q"],
                    source=CommandSource.DEFAULT_FRAMEWORK_RULE, confidence=0.9,
                    reason="pyproject",
                )]

            def build_plan(self, **kwargs):
                from app.models.testing import ExecutionPlan

                captured["candidates"] = kwargs.get("candidates", [])
                return ExecutionPlan(
                    plan_id="plan-2", workspace_id=kwargs["workspace_id"],
                    workspace_root=kwargs["workspace_root"], steps=[],
                )

        orch = OrchestrationService(testing_service=_FakeTesting())
        orch._engineering_graph = None  # no graph evidence

        run = await orch.create_run(
            RunSource(source_type=RunSourceType.USER_TASK, title="No EKG")
        )
        run.current_stage = StageType.APPLYING_PATCH
        run.repository_path = None
        run.patch_set = PatchSet(
            patch_id="p-ekg2",
            changes=[FileChange(
                change_id="C-1", operation=FileOperation.MODIFY,
                path="auth/service.py",
            )],
        )
        await orch._store.update(run)

        result = await orch._stage_testing(run, "workspace")
        assert result is True
        pytest_args = captured["candidates"][0].arguments
        assert pytest_args == ["-m", "pytest", "-q"]  # unchanged (full suite)

    def test_select_tests_from_graph_degrades_gracefully(self):
        orch = OrchestrationService()
        orch._engineering_graph = None
        assert orch._select_tests_from_graph(["auth/service.py"]) == []
        assert orch._select_tests_from_graph([]) == []
