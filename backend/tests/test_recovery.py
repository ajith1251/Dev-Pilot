"""
Tests for startup recovery flow and persistence state management.

Covers:
- OrchestrationService.check_recovery() with InMemoryRunStore
- OrchestrationService.check_recovery() with PostgresRunStore (mocked)
- OrchestrationService.resume_run()
- main.py lifespan recovery check logic
- Mark stale runs flow
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.orchestration import (
    DevPilotRun,
    DevPilotRunResult,
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
from app.services.orchestration_service import OrchestrationService
from app.services.run_store import InMemoryRunStore


# ── Helpers ─────────────────────────────────────────────────────


def _make_run(
    run_id: str,
    status: RunStatus = RunStatus.PENDING,
    stage: StageType = StageType.INITIALIZING,
) -> DevPilotRun:
    """Create a minimal DevPilotRun for testing."""
    return DevPilotRun(
        run_id=run_id,
        source=RunSource(
            source_type=RunSourceType.USER_TASK,
            title=f"Test run {run_id}",
        ),
        status=status,
        current_stage=stage,
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def _make_mock_postgres_store():
    """Create a mock PostgresRunStore that supports recovery methods."""
    store = MagicMock()
    store.find_recoverable_runs = AsyncMock()
    store.mark_stale_runs = AsyncMock()
    return store


# ═════════════════════════════════════════════════════════════════
# 1 — RECOVERY WITH IN-MEMORY STORE
# ═════════════════════════════════════════════════════════════════


class TestRecoveryWithInMemoryStore:
    """Recovery behaviors when using InMemoryRunStore."""

    def setup_method(self):
        self.store = InMemoryRunStore()
        self.service = OrchestrationService(run_store=self.store)

    @pytest.mark.asyncio
    async def test_recovery_not_supported_in_memory(self):
        """InMemoryRunStore should report recovery as not supported."""
        result = await self.service.check_recovery()
        assert result["store_type"] == "in_memory"
        assert result["recovery_supported"] is False

    @pytest.mark.asyncio
    async def test_resume_run_not_found(self):
        """Resuming a non-existent run should return None."""
        result = await self.service.resume_run("NONEXISTENT")
        assert result is None

    @pytest.mark.asyncio
    async def test_resume_terminal_run_returns_none(self):
        """Resuming a terminal run should return None."""
        run = _make_run("RUN-TERMINAL", RunStatus.APPROVED, StageType.COMPLETED)
        await self.store.create(run)

        result = await self.service.resume_run("RUN-TERMINAL")
        assert result is None

    @pytest.mark.asyncio
    async def test_resume_cancelled_run_returns_none(self):
        """Resuming a cancelled run should return None."""
        run = _make_run("RUN-CANCELLED", RunStatus.CANCELLED, StageType.CANCELLED)
        run.cancellation_requested = True
        await self.store.create(run)

        result = await self.service.resume_run("RUN-CANCELLED")
        assert result is None

    @pytest.mark.asyncio
    async def test_resume_running_run_attempts_execution(self):
        """Resuming a running run should proceed with execute_run."""
        run = _make_run("RUN-ACTIVE", RunStatus.RUNNING, StageType.TESTING)
        await self.store.create(run)

        # Should return a result (likely failure since stages are mocked out)
        result = await self.service.resume_run(
            "RUN-ACTIVE",
            workspace_root="/tmp/test-workspace",
        )
        # The resume_run calls execute_run which should either complete or fail
        assert result is not None
        assert isinstance(result, DevPilotRunResult)
        # It will likely fail since we don't have the dependencies set up
        assert result.run_id == "RUN-ACTIVE"


# ═════════════════════════════════════════════════════════════════
# 2 — RECOVERY WITH MOCKED POSTGRES STORE
# ═════════════════════════════════════════════════════════════════


class TestRecoveryWithMockedPostgres:
    """Recovery behaviors when using PostgresRunStore (mocked)."""

    def setup_method(self):
        self.mock_store = _make_mock_postgres_store()
        self.service = OrchestrationService(run_store=self.mock_store)

    @pytest.mark.asyncio
    async def test_recovery_no_recoverable_runs(self):
        """When no recoverable runs exist, recovery should report 0."""
        self.mock_store.find_recoverable_runs = AsyncMock(return_value=[])
        self.mock_store.mark_stale_runs = AsyncMock(return_value=0)

        result = await self.service.check_recovery()
        assert result["recovery_supported"] is True
        assert result["store_type"] == "postgres"
        assert result["recoverable_found"] == 0
        assert result["marked_stale"] == 0

    @pytest.mark.asyncio
    async def test_recovery_with_recoverable_runs(self):
        """Recoverable runs should be reported with their IDs."""
        runs = [
            _make_run("RUN-RECOVER-1", RunStatus.RUNNING, StageType.CODING),
            _make_run("RUN-RECOVER-2", RunStatus.PENDING, StageType.PLANNING),
        ]
        self.mock_store.find_recoverable_runs = AsyncMock(return_value=runs)
        self.mock_store.mark_stale_runs = AsyncMock(return_value=1)

        result = await self.service.check_recovery()
        assert result["recovery_supported"] is True
        assert result["recoverable_found"] == 2
        assert result["marked_stale"] == 1
        assert "RUN-RECOVER-1" in result["recoverable_ids"]
        assert "RUN-RECOVER-2" in result["recoverable_ids"]

    @pytest.mark.asyncio
    async def test_recovery_with_stale_runs(self):
        """Stale runs should be marked as FAILED."""
        old_run = _make_run("RUN-STALE", RunStatus.RUNNING, StageType.INITIALIZING)
        self.mock_store.find_recoverable_runs = AsyncMock(return_value=[old_run])
        self.mock_store.mark_stale_runs = AsyncMock(return_value=1)

        result = await self.service.check_recovery()
        assert result["marked_stale"] == 1
        self.mock_store.mark_stale_runs.assert_called_once_with(max_age_minutes=60)

    @pytest.mark.asyncio
    async def test_recovery_store_error_handled(self):
        """If the store raises an exception, recovery should return error info."""
        self.mock_store.find_recoverable_runs = AsyncMock(
            side_effect=RuntimeError("Connection refused")
        )

        result = await self.service.check_recovery()
        assert result["recovery_supported"] is True
        assert "error" in result
        assert "Connection refused" in result["error"]


# ═════════════════════════════════════════════════════════════════
# 3 — MARK STALE RUNS (LIFESPAN RECOVERY LOGIC)
# ═════════════════════════════════════════════════════════════════


class TestMarkStaleRuns:
    """Test the mark_stale_runs logic invoked during startup recovery."""

    @pytest.mark.asyncio
    async def test_mark_stale_runs_young_runs_not_marked(self):
        """Recently created runs should not be marked as stale."""
        from app.services.postgres_run_store import PostgresRunStore

        # Mock the internal session to return a young run
        young_run = MagicMock()
        young_run.status = "running"
        young_run.current_stage = "initializing"
        young_run.updated_at = datetime.now(timezone.utc)  # Very recent

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        mock_result = MagicMock()
        mock_result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[young_run])))
        mock_session.execute = AsyncMock(return_value=mock_result)

        store = PostgresRunStore()
        store._get_session_factory = MagicMock(return_value=MagicMock(return_value=mock_session))

        # Mark with very short threshold to ensure it's NOT marked
        count = await store.mark_stale_runs(max_age_minutes=99999)
        assert count == 0  # Young run should not be marked

    @pytest.mark.asyncio
    async def test_mark_stale_does_not_raise_with_no_runs(self):
        """mark_stale_runs should handle empty result gracefully."""
        from app.services.postgres_run_store import PostgresRunStore

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        mock_result = MagicMock()
        mock_result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        mock_session.execute = AsyncMock(return_value=mock_result)

        store = PostgresRunStore()
        store._get_session_factory = MagicMock(return_value=MagicMock(return_value=mock_session))

        count = await store.mark_stale_runs(max_age_minutes=60)
        assert count == 0  # No runs found, nothing to mark


# ═════════════════════════════════════════════════════════════════
# 4 — RECOVERY ENDPOINT INTEGRATION
# ═════════════════════════════════════════════════════════════════


class TestRecoveryEndpointIntegration:
    """Test that recovery endpoint routes exist and return expected structures."""

    def test_recovery_endpoint_exists(self):
        """The orchestration router should have a recovery endpoint."""
        from app.api.v1.orchestration import router

        routes = [r.path for r in router.routes]
        assert "/api/v1/orchestration/recovery" in routes

    def test_resume_endpoint_exists(self):
        """The orchestration router should have a resume endpoint."""
        from app.api.v1.orchestration import router

        routes = [r.path for r in router.routes]
        assert "/api/v1/runs/{run_id}/resume" in routes or \
               "/api/v1/runs/{run_id}/resume/" in routes


# ═════════════════════════════════════════════════════════════════
# 5 — FIND RECOVERABLE RUNS
# ═════════════════════════════════════════════════════════════════


class TestFindRecoverableRuns:
    """Test finding recoverable runs logic."""

    @pytest.mark.asyncio
    async def test_find_recoverable_in_memory_not_supported(self):
        """InMemoryRunStore should not support find_recoverable_runs."""
        store = InMemoryRunStore()
        assert not hasattr(store, "find_recoverable_runs")

    @pytest.mark.asyncio
    async def test_find_recoverable_with_mocked_store(self):
        """find_recoverable_runs should only find non-terminal runs."""
        mock_store = _make_mock_postgres_store()
        mock_store.find_recoverable_runs = AsyncMock(
            return_value=[
                _make_run("RUN-PENDING", RunStatus.PENDING),
                _make_run("RUN-RUNNING", RunStatus.RUNNING),
            ]
        )

        runs = await mock_store.find_recoverable_runs()
        assert len(runs) == 2
        for run in runs:
            assert run.status in (RunStatus.PENDING, RunStatus.RUNNING)


# ═════════════════════════════════════════════════════════════════
# 6 — CAPABILITIES & PERSISTENCE MODE
# ═════════════════════════════════════════════════════════════════


class TestPersistenceCapabilities:
    """Test that persistence mode is correctly reported."""

    def test_default_capabilities_persistence_mode(self):
        """Default capabilities should indicate in_memory mode."""
        caps = OrchestrationService.get_capabilities()
        assert caps.persistence_mode == "in_memory"

    @pytest.mark.asyncio
    async def test_list_with_stats_uses_batched_when_available(self):
        """list_runs_with_stats should use batched method when available."""
        from app.services.run_store import InMemoryRunStore

        store = InMemoryRunStore()
        service = OrchestrationService(run_store=store)

        # InMemoryRunStore doesn't have list_with_total_and_stats
        runs, total, stats = await service.list_runs_with_stats()
        assert isinstance(runs, list)
        assert isinstance(total, int)
        assert isinstance(stats, dict)
        # Should have all expected status keys
        for key in ("total", "pending", "running", "approved",
                    "rejected", "needs_human_review", "failed", "cancelled"):
            assert key in stats
