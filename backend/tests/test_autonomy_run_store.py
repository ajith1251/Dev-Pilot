"""
Tests for the Phase 16/17 durable-run fix: autonomy runs persist through
PostgresRunStore so the `runs` table is populated (full audit trail).

Three areas:
1. Context round-trip — PostgresRunStore must serialize/deserialize the run's
   context fields (repository_profile, requirements, plan, retrieved_context,
   stage outputs) or execute_run's re-hydration drops the autonomy
   controller's pre-populated context and the strict state machine rejects
   the first real transition.
2. Controller wiring — the AutonomousExecutionController builds an
   OrchestrationService bound to a durable run store when a session factory
   is available, and falls back to in-memory otherwise.
3. Evidence re-fetch regression — with a store that returns FRESH copies on
   get() (like Postgres), the in-memory `run` object is stale after
   execute_run; _run_iteration must re-fetch so evidence reflects the
   persisted stage outputs.
"""

from __future__ import annotations

from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.autonomy import AutonomousAction
from app.models.issues import ImplementationPlan, ImplementationStep
from app.models.orchestration import (
    RunSource,
    RunSourceType,
    StageType,
)
from app.services.autonomy_service import AutonomousExecutionController
from app.services.orchestration_service import OrchestrationService
from app.services.postgres_run_store import (
    _deserialize_context,
    _serialize_context,
)
from app.services.run_store import InMemoryRunStore

FIXTURE = "tests/fixtures/fixture_auth_app"


# ── 1. Context round-trip ─────────────────────────────────────────


def _run_with_context():
    from app.models.profile import RepositoryProfile

    from app.models.rag import RetrievedContext, RetrievalQuery

    from app.models.issues import StructuredRequirements, Requirement

    run = MagicMock()
    run.repository_path = FIXTURE
    run.repository_profile = RepositoryProfile(name="fixture_auth_app")
    run.requirements = StructuredRequirements(
        objective="Fix tokens",
        requirements=[Requirement(description="Reject expired tokens")],
    )
    run.plan = ImplementationPlan(
        summary="Add token validation",
        objective="Reject expired tokens",
        steps=[ImplementationStep(id="S1", title="Validate", description="Check expiry")],
    )
    run.retrieved_context = RetrievedContext(query=RetrievalQuery(text="tokens"))
    run.patch_set = None
    run.patch_result = None
    run.test_result = None
    run.repair_result = None
    run.review_report = None
    run.quality_gate_result = None
    return run


class TestContextRoundTrip:
    def test_serialize_context_roundtrip(self):
        """Context fields must survive serialize -> deserialize unchanged."""
        run = _run_with_context()
        payload = _serialize_context(run)

        assert payload is not None
        assert payload["repository_profile"]["name"] == "fixture_auth_app"
        assert payload["requirements"]["objective"] == "Fix tokens"
        assert payload["plan"]["summary"] == "Add token validation"
        assert payload["retrieved_context"]["query"]["text"] == "tokens"

        restored = _deserialize_context(payload)
        assert restored["repository_profile"].name == "fixture_auth_app"
        assert restored["requirements"].objective == "Fix tokens"
        assert restored["plan"].summary == "Add token validation"
        assert restored["retrieved_context"].query.text == "tokens"

    def test_serialize_context_skips_attribute_less_stubs(self):
        """Attribute-less stub objects (demo/test doubles) are skipped, not fatal."""
        run = _run_with_context()
        run.retrieved_context = type("RC", (), {})()
        run.test_result = type("TR", (), {})()
        payload = _serialize_context(run)

        assert payload is not None
        assert "retrieved_context" not in payload
        assert "test_result" not in payload
        # Real context still round-trips.
        assert payload["repository_profile"]["name"] == "fixture_auth_app"

    def test_deserialize_context_none_safe(self):
        assert _deserialize_context(None) == {}
        assert _deserialize_context({}) == {}


# ── 2. Controller run-store wiring ────────────────────────────────


class _AsyncSessionCtx:
    """Minimal async context manager wrapper for a mock session."""

    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *args):
        return None


def _make_probe_factory(probe_ok: bool):
    """Factory mock whose async context session succeeds/fails the schema probe."""
    session = AsyncMock()
    if not probe_ok:
        session.execute.side_effect = RuntimeError("relation \"runs\" does not exist")
    factory = MagicMock()
    factory.return_value = _AsyncSessionCtx(session)
    return factory, session


class TestControllerRunStoreWiring:
    async def test_factory_present_builds_durable_orchestration(self):
        """With a probe-able session factory, _get_orchestration uses PostgresRunStore."""
        factory, _ = _make_probe_factory(probe_ok=True)
        ctrl = AutonomousExecutionController(session_factory=factory)
        orch = await ctrl._get_orchestration()
        from app.services.postgres_run_store import PostgresRunStore

        assert isinstance(orch._store, PostgresRunStore)
        assert orch._store._session_factory is factory

    async def test_unmigrated_schema_falls_back_to_in_memory(self):
        """A DB without migration 008 (context_json) degrades to in-memory."""
        factory, session = _make_probe_factory(probe_ok=False)
        ctrl = AutonomousExecutionController(session_factory=factory)
        orch = await ctrl._get_orchestration()

        assert isinstance(orch._store, InMemoryRunStore)
        session.execute.assert_called_once()

    async def test_no_factory_falls_back_to_in_memory(self):
        """Without a DB, the controller degrades to InMemoryRunStore."""
        ctrl = AutonomousExecutionController(session_factory=None)
        ctrl._get_factory = MagicMock(return_value=None)  # force no-DB path
        orch = await ctrl._get_orchestration()

        assert isinstance(orch._store, InMemoryRunStore)

    async def test_injected_run_store_is_respected(self):
        store = InMemoryRunStore()
        ctrl = AutonomousExecutionController(run_store=store)
        orch = await ctrl._get_orchestration()

        assert orch._store is store


# ── 3. Evidence re-fetch regression (deep-copy store) ─────────────


class _StrictUpdateOrderStore(InMemoryRunStore):
    """Simulates PostgresRunStore: update() before create() must raise.

    PostgresRunStore.update() raises RunNotFoundError when the row does not
    exist yet. InMemoryRunStore silently tolerates update-before-create,
    which is why the create_run ordering bug only surfaced on the real
    PostgreSQL path (autonomy API runs without a repository).
    """

    def __init__(self) -> None:
        super().__init__()
        self._created_ids: set = set()

    async def create(self, run):
        stored = await super().create(run)
        self._created_ids.add(run.run_id)
        return stored

    async def update(self, run):
        if run.run_id not in self._created_ids:
            from app.services.postgres_run_store import RunNotFoundError

            raise RunNotFoundError(f"Run {run.run_id} not found")
        return await super().update(run)


class TestCreateRunOrderRegression:
    """create_run must persist BEFORE recording skipped stages.

    Regression for the live-API bug: OrchestrationService.create_run recorded
    ACQUIRING_REPOSITORY/ANALYZING_REPOSITORY as SKIPPED (via _record_stage,
    which calls _store.update) before _store.create — the InMemory store
    tolerated it, but PostgresRunStore raised RunNotFoundError for runs
    without a repository, so /api/v1/autonomy/run landed in waiting_for_human.
    """

    async def test_create_run_without_repo_does_not_update_before_create(self):
        from app.models.orchestration import RunSource, RunSourceType
        from app.services.orchestration_service import OrchestrationService

        store = _StrictUpdateOrderStore()
        orch = OrchestrationService(run_store=store)
        source = RunSource(
            source_type=RunSourceType.USER_TASK,
            title="Fix tokens",
            repository_path=None,
        )
        run = await orch.create_run(source)
        assert run.run_id
        assert run.run_id in store._created_ids

    async def test_create_run_with_repo_still_works(self):
        from app.models.orchestration import RunSource, RunSourceType
        from app.services.orchestration_service import OrchestrationService

        store = _StrictUpdateOrderStore()
        orch = OrchestrationService(run_store=store)
        source = RunSource(
            source_type=RunSourceType.USER_TASK,
            title="Fix tokens",
            repository_path="/repo/path",
        )
        run = await orch.create_run(source)
        assert run.run_id in store._created_ids


class _DeepCopyStore(InMemoryRunStore):
    """In-memory store that returns FRESH copies on get/update, like Postgres.

    This is the failure mode that motivated the re-fetch: without it, the
    in-memory `run` object in _run_iteration stays stale after execute_run
    re-hydrates its own copy from the store.
    """

    async def get(self, run_id):
        with self._lock:
            run = self._runs.get(run_id)
            return deepcopy(run) if run is not None else None

    async def update(self, run):
        with self._lock:
            self._runs[run.run_id] = deepcopy(run)
        return run


def _make_test_result(status: str = "passed"):
    from app.models.testing import ExecutionStatus, TestRunResult

    return TestRunResult(
        run_id="demo",
        workspace_id="demo-ws",
        status=ExecutionStatus.PASSED if status == "passed" else ExecutionStatus.FAILED,
        commands_total=1,
        commands_passed=1,
        commands_failed=0,
        commands_skipped=0,
        tests_total=5,
        tests_passed=5 if status == "passed" else 3,
        tests_failed=0 if status == "passed" else 2,
        tests_skipped=0,
        failures=[],
        process_results=[],
        duration_seconds=0.5,
        summary="5 passed",
    )


def _make_gate():
    from app.models.review import QualityGateDecision, QualityGateResult

    return QualityGateResult(
        review_id="rv-demo",
        decision=QualityGateDecision.APPROVED,
        score=92.5,
        requirements_satisfied=2,
        requirements_partial=0,
        requirements_unsatisfied=0,
        verification_status="passed",
        security_status="passed",
        reason_codes=["review_passed"],
    )


class TestEvidenceRefetchRegression:
    async def test_run_iteration_refetches_run_after_execute(self):
        """Evidence must reflect persisted stage outputs even when the store
        returns fresh copies (the Postgres behavior)."""
        orch = OrchestrationService(run_store=_DeepCopyStore())
        recorded = {}

        async def fake_execute_run(run_id, workspace_root=None):
            run = await orch._store.get(run_id)
            recorded["run_id"] = run_id
            # Simulate stage outputs persisted by the real pipeline.
            run.plan = MagicMock()
            run.plan.summary = "Add token validation"
            run.plan.objective = "Reject expired tokens"
            run.plan.steps = []
            run.test_result = _make_test_result("passed")
            run.quality_gate_result = _make_gate()
            await orch._store.update(run)
            return MagicMock(status=MagicMock(value="approved"))

        orch.execute_run = fake_execute_run

        ctrl = AutonomousExecutionController(orchestration=orch, collaboration=None)
        state = await ctrl.create_goal(task="Fix tokens", repository=FIXTURE)

        evidence = await ctrl._run_iteration(
            state, AutonomousAction.CONTINUE, "initial_iteration"
        )

        # Without the re-fetch, evidence would see an empty run object.
        assert evidence.run_id == recorded["run_id"]
        assert evidence.test_status == "passed"
        assert evidence.quality_gate_decision == "approved"
        assert evidence.plan_summary == "Add token validation"

    async def test_run_is_persisted_through_durable_store(self):
        """The run created by _run_iteration must land in the run store."""
        store = _DeepCopyStore()
        orch = OrchestrationService(run_store=store)
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
        state = await ctrl.create_goal(task="Fix tokens", repository=FIXTURE)

        await ctrl._run_iteration(state, AutonomousAction.CONTINUE, "initial_iteration")

        run = recorded["run"]
        # The run was stored (durable path) and re-fetched by _run_iteration.
        persisted = await store.get(run.run_id)
        assert persisted is not None
        assert persisted.run_id == run.run_id
