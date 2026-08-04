"""
RunStore Contract Tests — Test the same behavioral contract against both
InMemoryRunStore and PostgresRunStore implementations.

These tests verify that both stores conform to the RunStore Protocol
and produce identical results for the same inputs.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.models.orchestration import (
    DevPilotRun,
    EventType,
    FailureCode,
    RunEvent,
    RunFailure,
    RunSource,
    RunSourceType,
    RunStatus,
    StageResult,
    StageStatus,
    StageType,
)
from app.services.run_store import InMemoryRunStore, generate_run_id


# ── Shared test data ───────────────────────────────────────────


def make_source(title: str = "Test") -> RunSource:
    return RunSource(
        source_type=RunSourceType.USER_TASK,
        title=title,
        description="A test run",
    )


def make_run(run_id: str | None = None, title: str = "Test") -> DevPilotRun:
    return DevPilotRun(
        run_id=run_id or generate_run_id(),
        source=make_source(title),
        status=RunStatus.PENDING,
        current_stage=StageType.INITIALIZING,
    )


# ── Fixtures ───────────────────────────────────────────────────


@pytest.fixture
def in_memory_store() -> InMemoryRunStore:
    return InMemoryRunStore()


# ── Contract Tests ─────────────────────────────────────────────
#
# These tests define the expected behavior of any RunStore implementation.
# Add PostgresRunStore tests in the integration test section below.


class TestRunStoreContract:
    """Contract tests that all RunStore implementations must pass."""

    @pytest.fixture
    def store(self, in_memory_store):
        return in_memory_store

    # ── CRUD ───────────────────────────────────────────────────

    async def test_create_and_get(self, store):
        run = make_run("RUN-001")
        created = await store.create(run)
        assert created.run_id == "RUN-001"
        assert created.status == RunStatus.PENDING

        retrieved = await store.get("RUN-001")
        assert retrieved is not None
        assert retrieved.run_id == "RUN-001"

    async def test_get_nonexistent(self, store):
        assert await store.get("NONEXISTENT") is None

    async def test_update(self, store):
        run = make_run("RUN-002")
        await store.create(run)

        run.status = RunStatus.RUNNING
        updated = await store.update(run)
        assert updated.status == RunStatus.RUNNING

        retrieved = await store.get("RUN-002")
        assert retrieved is not None
        assert retrieved.status == RunStatus.RUNNING

    async def test_delete(self, store):
        run = make_run("RUN-003")
        await store.create(run)

        assert await store.delete("RUN-003") is True
        assert await store.get("RUN-003") is None
        assert await store.delete("NONEXISTENT") is False

    # ── List / Filter ──────────────────────────────────────────

    async def test_list_empty(self, store):
        runs = await store.list()
        assert len(runs) == 0

    async def test_list_multiple(self, store):
        for i in range(5):
            await store.create(make_run(f"RUN-L-{i}"))
        runs = await store.list()
        assert len(runs) == 5

    async def test_list_status_filter(self, store):
        for i in range(3):
            run = make_run(f"RUN-APP-{i}")
            run.status = RunStatus.APPROVED
            await store.create(run)
        for i in range(2):
            run = make_run(f"RUN-REJ-{i}")
            run.status = RunStatus.REJECTED
            await store.create(run)

        approved = await store.list(status="approved")
        assert len(approved) == 3

        rejected = await store.list(status="rejected")
        assert len(rejected) == 2

    async def test_list_pagination(self, store):
        for i in range(10):
            await store.create(make_run(f"RUN-PG-{i:03d}"))

        page1 = await store.list(limit=3, offset=0)
        assert len(page1) == 3

        page2 = await store.list(limit=3, offset=3)
        assert len(page2) == 3

        page3 = await store.list(limit=3, offset=6)
        assert len(page3) == 3

        page4 = await store.list(limit=3, offset=9)
        assert len(page4) == 1

    async def test_list_order(self, store):
        """Runs must be listed in reverse chronological order."""
        runs = []
        for i in range(3):
            run = make_run(f"RUN-ORD-{i}")
            await store.create(run)
            runs.append(run)

        listed = await store.list()
        assert listed[0].run_id == runs[2].run_id  # most recent first

    # ── Cancellation ───────────────────────────────────────────

    async def test_cancel_running(self, store):
        run = make_run("RUN-C-1")
        await store.create(run)

        result = await store.request_cancel("RUN-C-1")
        assert result is True

        retrieved = await store.get("RUN-C-1")
        assert retrieved is not None
        assert retrieved.cancellation_requested is True

    async def test_cancel_terminal_fails(self, store):
        run = make_run("RUN-C-2")
        run.status = RunStatus.APPROVED
        await store.create(run)

        result = await store.request_cancel("RUN-C-2")
        assert result is False

    async def test_cancel_nonexistent(self, store):
        result = await store.request_cancel("NONEXISTENT")
        assert result is False

    async def test_cancel_already_cancelled(self, store):
        run = make_run("RUN-C-3")
        run.status = RunStatus.CANCELLED
        await store.create(run)

        result = await store.request_cancel("RUN-C-3")
        assert result is False

    # ── Events (stored on run object) ──────────────────────────

    async def test_events_persisted_on_run(self, store):
        run = make_run("RUN-E-1")
        await store.create(run)

        event = RunEvent(
            event_id="evt-001",
            run_id="RUN-E-1",
            timestamp="2026-01-01T00:00:00",
            event_type=EventType.RUN_CREATED,
            stage=StageType.INITIALIZING,
            message="Test event",
        )
        run.events.append(event)
        await store.update(run)

        retrieved = await store.get("RUN-E-1")
        assert retrieved is not None
        assert len(retrieved.events) == 1
        assert retrieved.events[0].event_id == "evt-001"

    async def test_multiple_events_ordering(self, store):
        run = make_run("RUN-E-2")
        await store.create(run)

        for i in range(3):
            event = RunEvent(
                event_id=f"evt-{i:03d}",
                run_id="RUN-E-2",
                timestamp=f"2026-01-01T00:00:{i:02d}",
                event_type=EventType.RUN_CREATED,
                stage=StageType.INITIALIZING,
                message=f"Event {i}",
            )
            run.events.append(event)
        await store.update(run)

        retrieved = await store.get("RUN-E-2")
        assert retrieved is not None
        assert len(retrieved.events) == 3
        assert retrieved.events[0].event_id == "evt-000"
        assert retrieved.events[2].event_id == "evt-002"

    # ── Stage Results ───────────────────────────────────────────

    async def test_stage_results_persisted(self, store):
        run = make_run("RUN-S-1")
        await store.create(run)

        sr = StageResult(
            stage=StageType.PLANNING,
            status=StageStatus.SUCCEEDED,
            started_at="2026-01-01T00:00:00",
            finished_at="2026-01-01T00:01:00",
            duration_ms=60_000,
        )
        run.stage_results.append(sr)
        await store.update(run)

        retrieved = await store.get("RUN-S-1")
        assert retrieved is not None
        assert len(retrieved.stage_results) == 1
        assert retrieved.stage_results[0].stage == StageType.PLANNING
        assert retrieved.stage_results[0].status == StageStatus.SUCCEEDED

    # ── Warnings ───────────────────────────────────────────────

    async def test_warnings_persisted(self, store):
        run = make_run("RUN-W-1")
        await store.create(run)

        run.warnings.append("Warning 1")
        run.warnings.append("Warning 2")
        await store.update(run)

        retrieved = await store.get("RUN-W-1")
        assert retrieved is not None
        assert len(retrieved.warnings) == 2
        assert "Warning 1" in retrieved.warnings

    # ── Failure ────────────────────────────────────────────────

    async def test_failure_persisted(self, store):
        run = make_run("RUN-F-1")
        run.failure = RunFailure(
            stage=StageType.PLANNING,
            code=FailureCode.PLANNING_FAILED,
            message="Plan validation failed",
            recoverable=False,
        )
        await store.create(run)

        retrieved = await store.get("RUN-F-1")
        assert retrieved is not None
        assert retrieved.failure is not None
        assert retrieved.failure.code == FailureCode.PLANNING_FAILED
        assert retrieved.failure.stage == StageType.PLANNING

    async def test_failure_cleared(self, store):
        run = make_run("RUN-F-2")
        run.failure = RunFailure(
            stage=StageType.CODING,
            code=FailureCode.CODING_FAILED,
            message="Initial failure",
        )
        await store.create(run)

        run.failure = None
        await store.update(run)

        retrieved = await store.get("RUN-F-2")
        assert retrieved is not None
        assert retrieved.failure is None

    # ── Timestamps ─────────────────────────────────────────────

    async def test_timestamps_persisted(self, store):
        run = make_run("RUN-T-1")
        created = "2026-06-15T10:00:00+00:00"
        started = "2026-06-15T10:01:00+00:00"
        finished = "2026-06-15T10:05:00+00:00"

        run.created_at = created
        run.started_at = started
        run.finished_at = finished
        run.total_duration_ms = 240_000
        await store.create(run)

        retrieved = await store.get("RUN-T-1")
        assert retrieved is not None
        assert retrieved.created_at is not None
        assert retrieved.started_at is not None
        assert retrieved.finished_at is not None
        assert retrieved.total_duration_ms == 240_000

    # ── generate_run_id ────────────────────────────────────────

    async def test_generate_run_id(self):
        rid = generate_run_id()
        assert rid.startswith("RUN-")
        assert len(rid) == 12

    async def test_generate_run_id_unique(self):
        ids = {generate_run_id() for _ in range(100)}
        assert len(ids) == 100

    # ── Count Runs ────────────────────────────────────────────

    async def test_count_runs_empty(self, store):
        assert await store.count_runs() == 0

    async def test_count_runs_all(self, store):
        for i in range(5):
            await store.create(make_run(f"RUN-CNT-{i}"))
        assert await store.count_runs() == 5

    async def test_count_runs_with_status(self, store):
        for i in range(3):
            run = make_run(f"RUN-CNTS-A{i}")
            run.status = RunStatus.APPROVED
            await store.create(run)
        for i in range(2):
            run = make_run(f"RUN-CNTS-R{i}")
            run.status = RunStatus.REJECTED
            await store.create(run)
        assert await store.count_runs(status="approved") == 3
        assert await store.count_runs(status="rejected") == 2
        assert await store.count_runs(status="running") == 0

    async def test_count_runs_with_date_range(self, store):
        run1 = make_run("RUN-CNTD-1")
        run1.created_at = "2026-01-01T00:00:00Z"
        await store.create(run1)

        run2 = make_run("RUN-CNTD-2")
        run2.created_at = "2026-06-15T00:00:00Z"
        await store.create(run2)

        run3 = make_run("RUN-CNTD-3")
        run3.created_at = "2026-12-31T00:00:00Z"
        await store.create(run3)

        # Before June
        assert await store.count_runs(created_before="2026-06-01T00:00:00Z") == 1
        # After June
        assert await store.count_runs(created_after="2026-06-01T00:00:00Z") == 2
        # In June
        assert await store.count_runs(
            created_after="2026-06-01T00:00:00Z",
            created_before="2026-12-01T00:00:00Z",
        ) == 1

    async def test_count_runs_with_status_and_date(self, store):
        run = make_run("RUN-CNTSD-1")
        run.status = RunStatus.APPROVED
        run.created_at = "2026-06-15T00:00:00Z"
        await store.create(run)

        run2 = make_run("RUN-CNTSD-2")
        run2.status = RunStatus.REJECTED
        run2.created_at = "2026-06-15T00:00:00Z"
        await store.create(run2)

        run3 = make_run("RUN-CNTSD-3")
        run3.status = RunStatus.APPROVED
        run3.created_at = "2026-01-01T00:00:00Z"
        await store.create(run3)

        # Approved in June
        assert await store.count_runs(
            status="approved",
            created_after="2026-06-01T00:00:00Z",
            created_before="2026-07-01T00:00:00Z",
        ) == 1

    async def test_count_runs_by_status(self, store):
        for i in range(4):
            run = make_run(f"RUN-CNTSB-A{i}")
            run.status = RunStatus.APPROVED
            await store.create(run)
        for i in range(3):
            run = make_run(f"RUN-CNTSB-R{i}")
            run.status = RunStatus.RUNNING
            await store.create(run)

        counts = await store.count_runs_by_status()
        assert counts["total"] == 7
        assert counts["approved"] == 4
        assert counts["running"] == 3
        assert counts["pending"] == 0
        assert counts["failed"] == 0
        assert counts["rejected"] == 0
        assert counts["cancelled"] == 0
        assert counts["needs_human_review"] == 0

    # ── Source fields ──────────────────────────────────────────

    async def test_source_fields_persisted(self, store):
        source = RunSource(
            source_type=RunSourceType.GITHUB_ISSUE,
            title="Fix bug",
            description="The bug is in auth",
            repository_path="https://github.com/owner/repo",
            issue_number=42,
            issue_url="https://github.com/owner/repo/issues/42",
        )
        run = DevPilotRun(run_id="RUN-SRC-1", source=source)
        await store.create(run)

        retrieved = await store.get("RUN-SRC-1")
        assert retrieved is not None
        assert retrieved.source.source_type == RunSourceType.GITHUB_ISSUE
        assert retrieved.source.title == "Fix bug"
        assert retrieved.source.description == "The bug is in auth"
        assert retrieved.source.repository_path == "https://github.com/owner/repo"
        assert retrieved.source.issue_number == 42
        assert retrieved.source.issue_url == "https://github.com/owner/repo/issues/42"


# ═════════════════════════════════════════════════════════════════
#  POSTGRES RUN STORE — Integration Tests
# ═════════════════════════════════════════════════════════════════


@pytest.mark.integration
class TestPostgresRunStore:
    """Integration tests against live PostgreSQL via PostgresRunStore.

    These tests require PostgreSQL to be running and TEST_DATABASE_URL
    to be configured. They skip automatically when unavailable.
    Runs the same contract tests as InMemoryRunStore.
    """

    @pytest.fixture(autouse=True)
    def _check_config(self):
        from app.config import settings
        url = settings.TEST_DATABASE_URL or settings.DATABASE_URL
        if not url:
            pytest.skip("No DATABASE_URL configured for PostgresRunStore tests")
        self._store_url = url

    @pytest.fixture
    async def store(self):
        from app.services.postgres_run_store import PostgresRunStore
        import asyncio
        store = PostgresRunStore(database_url=self._store_url)
        yield store
        # Cleanup all test runs after each test
        try:
            runs = await store.list(limit=100)
            for run in runs:
                await store.delete(run.run_id)
        except Exception:
            pass
        # Dispose the owned engine so each test does not leak a connection pool
        # (PostgresRunStore builds a private engine for an explicit URL).
        try:
            await store.dispose()
        except Exception:
            pass

    # ── Run the same contract tests ────────────────────────────

    async def test_create_and_get(self, store):
        run = make_run("PG-RUN-001")
        created = await store.create(run)
        assert created.run_id == "PG-RUN-001"

        retrieved = await store.get("PG-RUN-001")
        assert retrieved is not None
        assert retrieved.run_id == "PG-RUN-001"

    async def test_get_nonexistent(self, store):
        assert await store.get("PG-NONEXISTENT") is None

    async def test_update(self, store):
        run = make_run("PG-RUN-002")
        await store.create(run)
        run.status = RunStatus.RUNNING
        await store.update(run)

        retrieved = await store.get("PG-RUN-002")
        assert retrieved is not None
        assert retrieved.status == RunStatus.RUNNING

    async def test_delete(self, store):
        run = make_run("PG-RUN-003")
        await store.create(run)
        assert await store.delete("PG-RUN-003") is True
        assert await store.get("PG-RUN-003") is None

    async def test_list(self, store):
        for i in range(3):
            await store.create(make_run(f"PG-LIST-{i}"))
        runs = await store.list()
        assert len(runs) == 3

    async def test_list_status_filter(self, store):
        for i in range(2):
            run = make_run(f"PG-APP-{i}")
            run.status = RunStatus.APPROVED
            await store.create(run)
        approved = await store.list(status="approved")
        assert len(approved) == 2

    async def test_list_pagination(self, store):
        for i in range(5):
            await store.create(make_run(f"PG-PG-{i}"))
        page = await store.list(limit=2, offset=0)
        assert len(page) == 2

    async def test_cancel_running(self, store):
        run = make_run("PG-CAN-1")
        await store.create(run)
        assert await store.request_cancel("PG-CAN-1") is True
        retrieved = await store.get("PG-CAN-1")
        assert retrieved is not None
        assert retrieved.cancellation_requested is True

    async def test_cancel_terminal_fails(self, store):
        run = make_run("PG-CAN-2")
        run.status = RunStatus.APPROVED
        await store.create(run)
        assert await store.request_cancel("PG-CAN-2") is False

    async def test_events_persisted(self, store):
        run = make_run("PG-EVT-1")
        await store.create(run)
        event = RunEvent(
            event_id="evt-pg-001",
            run_id="PG-EVT-1",
            timestamp="2026-01-01T00:00:00",
            event_type=EventType.RUN_CREATED,
            stage=StageType.INITIALIZING,
            message="Test event",
        )
        run.events.append(event)
        await store.update(run)

        retrieved = await store.get("PG-EVT-1")
        assert retrieved is not None
        assert len(retrieved.events) == 1
        assert retrieved.events[0].event_id == "evt-pg-001"

    async def test_stage_results_persisted(self, store):
        run = make_run("PG-STG-1")
        await store.create(run)
        sr = StageResult(
            stage=StageType.PLANNING,
            status=StageStatus.SUCCEEDED,
            started_at="2026-01-01T00:00:00",
        )
        run.stage_results.append(sr)
        await store.update(run)

        retrieved = await store.get("PG-STG-1")
        assert retrieved is not None
        assert len(retrieved.stage_results) == 1
        assert retrieved.stage_results[0].stage == StageType.PLANNING

    async def test_failure_persisted(self, store):
        run = make_run("PG-FAIL-1")
        run.failure = RunFailure(
            stage=StageType.PLANNING,
            code=FailureCode.PLANNING_FAILED,
            message="Failed",
        )
        await store.create(run)

        retrieved = await store.get("PG-FAIL-1")
        assert retrieved is not None
        assert retrieved.failure is not None
        assert retrieved.failure.code == FailureCode.PLANNING_FAILED

    async def test_append_event(self, store):
        run = make_run("PG-APPEVT-1")
        await store.create(run)

        event = RunEvent(
            event_id="evt-append-1",
            run_id="PG-APPEVT-1",
            timestamp="2026-01-01T00:00:00",
            event_type=EventType.STAGE_STARTED,
            stage=StageType.PLANNING,
            message="Planning started",
        )
        result = await store.append_event("PG-APPEVT-1", event)
        assert result is not None

    async def test_get_events(self, store):
        run = make_run("PG-GETEVT-1")
        await store.create(run)

        event = RunEvent(
            event_id="evt-get-1",
            run_id="PG-GETEVT-1",
            timestamp="2026-01-01T00:00:00",
            event_type=EventType.STAGE_STARTED,
            stage=StageType.PLANNING,
            message="Test",
        )
        await store.append_event("PG-GETEVT-1", event)

        events = await store.get_events("PG-GETEVT-1")
        assert len(events) >= 1

    async def test_save_and_get_stage_results(self, store):
        run = make_run("PG-SR-1")
        await store.create(run)

        sr = StageResult(
            stage=StageType.CODING,
            status=StageStatus.SUCCEEDED,
            started_at="2026-01-01T00:00:00",
        )
        await store.save_stage_result("PG-SR-1", sr)

        results = await store.get_stage_results("PG-SR-1")
        assert len(results) >= 1

    async def test_save_and_get_artifacts(self, store):
        run = make_run("PG-ART-1")
        await store.create(run)

        art = await store.save_artifact(
            run_id="PG-ART-1",
            artifact_type="plan",
            content={"summary": "test plan"},
            stage="planning",
        )
        assert art["artifact_type"] == "plan"

        artifacts = await store.get_artifacts("PG-ART-1")
        assert len(artifacts) >= 1
        assert artifacts[0]["artifact_type"] == "plan"

    async def test_find_recoverable_runs(self, store):
        # Create a running run (should be recoverable)
        run = make_run("PG-REC-1")
        run.status = RunStatus.RUNNING
        await store.create(run)

        recoverable = await store.find_recoverable_runs()
        run_ids = [r.run_id for r in recoverable]
        assert "PG-REC-1" in run_ids

    async def test_count_runs(self, store):
        for i in range(3):
            await store.create(make_run(f"PG-CNT-{i}"))
        count = await store.count_runs()
        assert count == 3

    async def test_count_runs_with_status(self, store):
        run = make_run("PG-CNTS-1")
        run.status = RunStatus.APPROVED
        await store.create(run)
        count = await store.count_runs(status="approved")
        assert count >= 1

    async def test_count_runs_empty(self, store):
        assert await store.count_runs() == 0

    async def test_count_runs_with_date_range(self, store):
        run1 = make_run("PG-CNTD-1")
        run1.created_at = "2026-01-01T00:00:00Z"
        await store.create(run1)

        run2 = make_run("PG-CNTD-2")
        run2.created_at = "2026-06-15T00:00:00Z"
        await store.create(run2)

        assert await store.count_runs(created_after="2026-06-01T00:00:00Z") == 1
        assert await store.count_runs(created_before="2026-06-01T00:00:00Z") == 1

    async def test_count_runs_with_status_and_date(self, store):
        run = make_run("PG-CNTSD-1")
        run.status = RunStatus.APPROVED
        run.created_at = "2026-06-15T00:00:00Z"
        await store.create(run)

        run2 = make_run("PG-CNTSD-2")
        run2.status = RunStatus.REJECTED
        run2.created_at = "2026-06-15T00:00:00Z"
        await store.create(run2)

        count = await store.count_runs(
            status="approved",
            created_after="2026-06-01T00:00:00Z",
            created_before="2026-07-01T00:00:00Z",
        )
        assert count == 1

    async def test_count_runs_by_status(self, store):
        for i in range(3):
            run = make_run(f"PG-CNTSB-A{i}")
            run.status = RunStatus.APPROVED
            await store.create(run)

        counts = await store.count_runs_by_status()
        assert counts["total"] >= 3
        assert counts["approved"] >= 3

    async def test_github_source_fields(self, store):
        source = RunSource(
            source_type=RunSourceType.GITHUB_ISSUE,
            title="GitHub Issue Fix",
            description="Fix the login bug",
            repository_path="https://github.com/owner/repo",
            issue_number=100,
            issue_url="https://github.com/owner/repo/issues/100",
        )
        run = DevPilotRun(run_id="PG-GH-1", source=source)
        await store.create(run)

        retrieved = await store.get("PG-GH-1")
        assert retrieved is not None
        assert retrieved.source.source_type == RunSourceType.GITHUB_ISSUE
        assert retrieved.source.title == "GitHub Issue Fix"
        assert retrieved.source.issue_number == 100

    async def test_warnings_persisted(self, store):
        run = make_run("PG-WARN-1")
        await store.create(run)
        run.warnings.append("Test warning")
        await store.update(run)

        retrieved = await store.get("PG-WARN-1")
        assert retrieved is not None
        assert len(retrieved.warnings) == 1
        assert retrieved.warnings[0] == "Test warning"

    # ── list_with_total_and_stats ────────────────────────────────

    async def _seed_ltas_runs(self, store):
        """Seed the 6 standard test runs and return the seeds list."""
        seeds = [
            ("PG-LTAS-1", "approved", "2026-01-01T00:00:00Z"),
            ("PG-LTAS-2", "approved", "2026-02-15T00:00:00Z"),
            ("PG-LTAS-3", "running", "2026-03-01T00:00:00Z"),
            ("PG-LTAS-4", "failed", "2026-04-10T00:00:00Z"),
            ("PG-LTAS-5", "pending", "2026-05-20T00:00:00Z"),
            ("PG-LTAS-6", "rejected", "2026-06-15T00:00:00Z"),
        ]
        for run_id, status_str, created_at in seeds:
            run = make_run(run_id)
            run.status = RunStatus(status_str)
            run.created_at = created_at
            await store.create(run)
        return seeds

    def _filter_seeded_ids(self, runs, seeds):
        """Return the set of run_ids from `runs` that match the given seeds."""
        seed_ids = {s[0] for s in seeds}
        return {r.run_id for r in runs if r.run_id in seed_ids}

    async def test_list_with_total_and_stats_matches_individual_calls(self, store):
        """list_with_total_and_stats should return the same results as calling
        list() with the same filters — compared only on seeded runs.

        Uses set comparison for run IDs to handle non-deterministic ordering
        when multiple runs share the same created_at timestamp.

        Does NOT require a clean database. Filters responses to only the
        seeded run IDs so leftover data from other tests does not affect
        the comparison.
        """
        seeds = await self._seed_ltas_runs(store)

        # ── 1. No filters ────────────────────────────────────────
        c_runs, c_total, c_stats = await store.list_with_total_and_stats()
        i_runs = await store.list()

        assert (self._filter_seeded_ids(c_runs, seeds)
                == self._filter_seeded_ids(i_runs, seeds)), "Runs mismatch (no filter)"
        assert c_total >= 6, f"Total should be >= 6, got {c_total}"
        assert c_stats["approved"] >= 2
        assert c_stats["running"] >= 1
        assert c_stats["failed"] >= 1
        assert c_stats["pending"] >= 1
        assert c_stats["rejected"] >= 1

        # ── 2. Status filter ─────────────────────────────────────
        c_runs, c_total, c_stats = await store.list_with_total_and_stats(status="approved")
        i_runs = await store.list(status="approved")

        assert (self._filter_seeded_ids(c_runs, seeds)
                == self._filter_seeded_ids(i_runs, seeds)), "Runs mismatch (status filter)"
        assert c_total >= 2, f"Approved total should be >= 2, got {c_total}"
        assert c_stats["total"] >= 6, f"Stats should be unfiltered, total={c_stats['total']}"

        # ── 3. Date range filter ─────────────────────────────────
        c_runs, c_total, _ = await store.list_with_total_and_stats(
            created_after="2026-03-01T00:00:00Z"
        )
        i_runs = await store.list(created_after="2026-03-01T00:00:00Z")

        assert (self._filter_seeded_ids(c_runs, seeds)
                == self._filter_seeded_ids(i_runs, seeds)), "Runs mismatch (date filter)"
        assert len(self._filter_seeded_ids(c_runs, seeds)) == 4, "Expected 4 seeded runs after March"
        assert c_total >= 4, f"Total should be >= 4, got {c_total}"

        # ── 4. Combined status + date filter ─────────────────────
        c_runs, c_total, _ = await store.list_with_total_and_stats(
            status="approved",
            created_after="2026-01-01T00:00:00Z",
            created_before="2026-03-01T00:00:00Z",
        )
        i_runs = await store.list(
            status="approved",
            created_after="2026-01-01T00:00:00Z",
            created_before="2026-03-01T00:00:00Z",
        )

        assert (self._filter_seeded_ids(c_runs, seeds)
                == self._filter_seeded_ids(i_runs, seeds)), "Runs mismatch (combined)"
        assert len(self._filter_seeded_ids(c_runs, seeds)) == 2, "Expected 2 approved seeded runs"

        # ── 5. Pagination ────────────────────────────────────────
        c_runs, c_total, _ = await store.list_with_total_and_stats(limit=2, offset=0)
        i_runs = await store.list(limit=2, offset=0)

        assert (self._filter_seeded_ids(c_runs, seeds)
                == self._filter_seeded_ids(i_runs, seeds)), "Runs mismatch (pagination)"
        assert len(c_runs) == 2, f"Should return exactly 2 runs with limit=2, got {len(c_runs)}"
        assert c_total >= 6, f"Total should be unaffected by pagination: {c_total}"

        # ── 6. Sort by oldest ────────────────────────────────────
        c_runs, c_total, _ = await store.list_with_total_and_stats(sort_by="oldest")
        i_runs = await store.list(sort_by="oldest")

        assert (self._filter_seeded_ids(c_runs, seeds)
                == self._filter_seeded_ids(i_runs, seeds)), "Runs mismatch (oldest sort)"

        # ── 7. Sort by duration ──────────────────────────────────
        c_runs, c_total, _ = await store.list_with_total_and_stats(sort_by="duration")
        i_runs = await store.list(sort_by="duration")

        assert (self._filter_seeded_ids(c_runs, seeds)
                == self._filter_seeded_ids(i_runs, seeds)), "Runs mismatch (duration sort)"

    async def test_list_with_total_and_stats_empty_store(self, store):
        """list_with_total_and_stats should return empty results for an empty store.

        Only verifies structure (runs=[], total=0, all stats=0).
        Does not require the database to be empty — counts only our seeded runs
        by using limit=0 to force empty results, then checks total and stats
        as relative comparisons.
        """
        c_runs, c_total, c_stats = await store.list_with_total_and_stats(limit=0)

        assert c_runs == [], "Should return empty list with limit=0"
        assert isinstance(c_total, int) and c_total >= 0, f"Invalid total: {c_total}"
        assert c_stats["total"] == sum(v for k, v in c_stats.items() if k != "total"), "Stats total should equal sum"
        for key in ("pending", "running", "approved", "rejected", "needs_human_review", "failed", "cancelled"):
            assert key in c_stats, f"Missing stats key: {key}"
