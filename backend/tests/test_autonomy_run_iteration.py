"""
Regression tests for the _run_iteration real-path pre-population fix.

The autonomy suite normally injects a deterministic `iteration_runner`, which
is exactly why the real-path bug survived: `_run_iteration` created a run that
stayed at `current_stage=INITIALIZING` with no `repository_profile`, so the
first real `execute_run` transition (`INITIALIZING -> ANALYZING_REPOSITORY`)
was rejected by the strict state machine. These tests lock in the fix:
- the run is pre-populated with a repository profile (analysis skipped)
- `current_stage` is advanced to ANALYZING_TASK (planning transition valid)
- a real iteration produces evidence instead of an environment failure
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.models.autonomy import AutonomousAction, FailureClass
from app.models.orchestration import (
    EventType,
    FailureCode,
    RunFailure,
    RunStatus,
    StageType,
)
from app.services.autonomy_service import AutonomousExecutionController
from app.services.orchestration_service import OrchestrationService

FIXTURE = "tests/fixtures/fixture_auth_app"


@pytest.mark.asyncio
class TestRunIterationRealPath:
    async def test_run_is_prepopulated_for_real_execute(self):
        """_run_iteration must build a run the strict state machine accepts."""
        orch = OrchestrationService()
        recorded = {}

        async def fake_execute_run(run_id, workspace_root=None):
            run = await orch._store.get(run_id)
            recorded["run"] = run
            run.plan = MagicMock()
            run.plan.summary = "Add token validation"
            run.plan.objective = "Reject expired tokens"
            run.plan.steps = []
            return MagicMock(status=MagicMock(value="approved"))

        orch.execute_run = fake_execute_run

        ctrl = AutonomousExecutionController(orchestration=orch, collaboration=None)
        state = await ctrl.create_goal(task="Fix tokens", repository=FIXTURE)

        evidence = await ctrl._run_iteration(
            state, AutonomousAction.CONTINUE, "initial_iteration"
        )

        run = recorded["run"]
        # The two halves of the fix:
        assert run.repository_profile is not None, (
            "repository_profile must be pre-populated so the analysis stage is skipped"
        )
        assert run.current_stage == StageType.ANALYZING_TASK, (
            "current_stage must advance so planning's transition is valid"
        )
        # The run actually executed and produced evidence (not an env failure).
        assert evidence.run_id == run.run_id
        assert evidence.failure_class != FailureClass.ENVIRONMENT

    async def test_empty_repo_still_produces_evidence(self):
        """A goal without a repository path must not crash the real path."""
        orch = OrchestrationService()
        recorded = {}

        async def fake_execute_run(run_id, workspace_root=None):
            run = await orch._store.get(run_id)
            recorded["run"] = run
            run.plan = MagicMock()
            run.plan.summary = "Plan"
            run.plan.objective = "Fix"
            run.plan.steps = []
            return MagicMock(status=MagicMock(value="approved"))

        orch.execute_run = fake_execute_run

        ctrl = AutonomousExecutionController(orchestration=orch, collaboration=None)
        state = await ctrl.create_goal(task="Fix tokens")  # no repository

        evidence = await ctrl._run_iteration(
            state, AutonomousAction.CONTINUE, "initial_iteration"
        )

        run = recorded["run"]
        assert run.current_stage == StageType.ANALYZING_TASK
        assert evidence.run_id == run.run_id
        assert evidence.failure_class != FailureClass.ENVIRONMENT


@pytest.mark.asyncio
class TestRunIterationTransientRetry:
    """Bounded goal-path retry (PROJECT_STATE item 13).

    A goal-path run that fails with 'No patch produced' (the ~20-25% Gemini
    coding variance — INSUFFICIENT_CONTEXT twice even after the orchestrator's
    own item-12 stage retry) is retried ONCE with a FRESH run. Hard coding
    errors and environmental failures are never retried.
    """

    async def _controller_with_fake(self, behavior):
        orch = OrchestrationService()
        attempts = []

        async def fake_execute_run(run_id, workspace_root=None):
            run = await orch._store.get(run_id)
            attempts.append(run_id)
            behavior(run, len(attempts))
            result = MagicMock()
            result.status = MagicMock(value=run.status.value)
            return result

        orch.execute_run = fake_execute_run
        ctrl = AutonomousExecutionController(orchestration=orch, collaboration=None)
        state = await ctrl.create_goal(task="Fix tokens", repository=FIXTURE)
        return ctrl, state, attempts

    @staticmethod
    def _transient_failure(run, n):
        run.status = RunStatus.FAILED
        run.failure = RunFailure(
            stage=StageType.CODING,
            code=FailureCode.CODING_FAILED,
            message="Coding agent produced no changes "
                    "(insufficient context: src/auth.py)",
        )

    @staticmethod
    def _transient_failure_json_parse(run, n):
        """Live-surfaced signature: malformed LLM JSON -> status=error."""
        run.status = RunStatus.FAILED
        run.failure = RunFailure(
            stage=StageType.CODING,
            code=FailureCode.CODING_FAILED,
            message="Failed to parse LLM output as JSON: Extra data: "
                    "line 1 column 15 (char 14)",
        )

    @staticmethod
    def _transient_failure_no_changes(run, n):
        """Live-surfaced signature: empty `changes` array -> status=error."""
        run.status = RunStatus.FAILED
        run.failure = RunFailure(
            stage=StageType.CODING,
            code=FailureCode.CODING_FAILED,
            message="No changes found in LLM output",
        )

    @staticmethod
    def _success(run, n):
        run.status = RunStatus.APPROVED
        run.plan = MagicMock()
        run.plan.summary = "Plan"
        run.plan.objective = "Fix"
        run.plan.steps = []

    async def test_transient_failure_retries_once_with_fresh_run(self):
        """'No patch produced' -> one retry with a NEW run, evidence from retry."""
        def behavior(run, n):
            if n == 1:
                self._transient_failure(run, n)
            else:
                self._success(run, n)

        ctrl, state, attempts = await self._controller_with_fake(behavior)
        evidence = await ctrl._run_iteration(
            state, AutonomousAction.CONTINUE, "initial_iteration")

        assert len(attempts) == 2, "must retry exactly once"
        assert attempts[0] != attempts[1], "retry must use a fresh run"
        assert evidence.run_id == attempts[1]
        assert evidence.failure_class != FailureClass.ENVIRONMENT
        assert any(e["event_type"] == EventType.RUN_RETRY for e in state.events)

    async def test_transient_failure_retry_is_bounded(self):
        """Both attempts transient-failing -> no third attempt; last run's evidence."""
        def behavior(run, n):
            self._transient_failure(run, n)

        ctrl, state, attempts = await self._controller_with_fake(behavior)
        evidence = await ctrl._run_iteration(
            state, AutonomousAction.CONTINUE, "initial_iteration")

        assert len(attempts) == 2, "retry must be bounded to one retry"
        assert evidence.run_id == attempts[1]
        assert evidence.failure_code == FailureCode.CODING_FAILED.value

    async def test_json_parse_error_signature_retries(self):
        """Live-surfaced 'Failed to parse LLM output as JSON' must retry once."""
        def behavior(run, n):
            if n == 1:
                self._transient_failure_json_parse(run, n)
            else:
                self._success(run, n)

        ctrl, state, attempts = await self._controller_with_fake(behavior)
        evidence = await ctrl._run_iteration(
            state, AutonomousAction.CONTINUE, "initial_iteration")

        assert len(attempts) == 2, "JSON-parse variance must retry once"
        assert evidence.run_id == attempts[1]
        assert evidence.failure_class != FailureClass.ENVIRONMENT
        assert any(e["event_type"] == EventType.RUN_RETRY for e in state.events)

    async def test_no_changes_signature_retries(self):
        """Live-surfaced 'No changes found in LLM output' must retry once."""
        def behavior(run, n):
            if n == 1:
                self._transient_failure_no_changes(run, n)
            else:
                self._success(run, n)

        ctrl, state, attempts = await self._controller_with_fake(behavior)
        evidence = await ctrl._run_iteration(
            state, AutonomousAction.CONTINUE, "initial_iteration")

        assert len(attempts) == 2
        assert evidence.run_id == attempts[1]
        assert any(e["event_type"] == EventType.RUN_RETRY for e in state.events)

    async def test_environment_failure_never_retried(self):
        """An exception during execution is environmental -> immediate, no retry."""
        orch = OrchestrationService()
        attempts = []

        async def fake_execute_run(run_id, workspace_root=None):
            attempts.append(run_id)
            raise RuntimeError("workspace unavailable")

        orch.execute_run = fake_execute_run
        ctrl = AutonomousExecutionController(orchestration=orch, collaboration=None)
        state = await ctrl.create_goal(task="Fix tokens")

        evidence = await ctrl._run_iteration(
            state, AutonomousAction.CONTINUE, "initial_iteration")

        assert len(attempts) == 1, "environmental failure must not retry"
        assert evidence.failure_class == FailureClass.ENVIRONMENT
        assert not any(e["event_type"] == EventType.RUN_RETRY for e in state.events)
