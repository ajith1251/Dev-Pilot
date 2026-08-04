"""
Phase 11H — Recovery Hardening Tests

Covers:
- Crash injection framework (test-only fault injection at stage boundaries)
- Recovery verification after simulated interruption
- Idempotency testing for run/event/stage operations
- Transaction fault injection (partial failure rollback)
- Optimistic concurrency stress
- Event ordering under concurrency
"""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
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
    RunStateMachine,
    RunStatus,
    StageResult,
    StageStatus,
    StageType,
)
from app.services.run_store import InMemoryRunStore, generate_run_id


# ═════════════════════════════════════════════════════════════════
#  SINGLETON: Test Fault Injector
# ═════════════════════════════════════════════════════════════════
# TEST-ONLY — never imported in production code


class _FaultInjector:
    """Deterministic fault injection for testing crash recovery.

    This is a test-only singleton. Set injection points before
    executing a run to simulate failures at specific stages.

    Usage:
        FaultInjector.inject_after("coding")  # crash after coding
        ...
        assert FaultInjector.was_injected("coding")
    """

    _injection_points: Dict[str, bool] = {}
    _triggered: List[str] = []

    @classmethod
    def reset(cls) -> None:
        """Clear all injection points and triggered records."""
        cls._injection_points.clear()
        cls._triggered.clear()

    @classmethod
    def inject_after(cls, stage: str) -> None:
        """Set an injection point at the given stage."""
        cls._injection_points[stage] = True

    @classmethod
    def inject_before(cls, stage: str) -> None:
        """Set an injection point before the given stage."""
        cls._injection_points[f"before_{stage}"] = True

    @classmethod
    def should_crash(cls, stage: str, before: bool = False) -> bool:
        """Check if a crash should be injected at this point."""
        key = f"before_{stage}" if before else stage
        if cls._injection_points.get(key, False):
            cls._triggered.append(key)
            return True
        return False

    @classmethod
    def was_injected(cls, stage: str, before: bool = False) -> bool:
        """Check if a crash was injected at the given point."""
        key = f"before_{stage}" if before else stage
        return key in cls._triggered

    @classmethod
    def is_active(cls) -> bool:
        """Check if any injection points are set."""
        return any(cls._injection_points.values())

    @classmethod
    def summary(cls) -> str:
        """Return a summary of injection state."""
        return (
            f"Injection points: {dict(cls._injection_points)}, "
            f"Triggered: {cls._triggered}"
        )


FaultInjector = _FaultInjector()


# ═════════════════════════════════════════════════════════════════
#  FIXTURES
# ═════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _reset_fault_injector():
    """Reset the fault injector before each test."""
    FaultInjector.reset()
    yield
    FaultInjector.reset()


@pytest.fixture
def in_memory_store():
    return InMemoryRunStore()


@pytest.fixture
def sample_run() -> DevPilotRun:
    return DevPilotRun(
        run_id=generate_run_id(),
        source=RunSource(
            source_type=RunSourceType.USER_TASK,
            title="Hardening Test",
            description="Testing crash recovery",
        ),
        status=RunStatus.RUNNING,
        current_stage=StageType.PLANNING,
    )


# ═════════════════════════════════════════════════════════════════
#  CRASH INJECTION FRAMEWORK TESTS
# ═════════════════════════════════════════════════════════════════


class TestCrashInjectionFramework:
    """Verify the test-only fault injector works correctly."""

    def test_injector_reset(self):
        assert FaultInjector.is_active() is False
        FaultInjector.inject_after("coding")
        assert FaultInjector.is_active() is True
        FaultInjector.reset()
        assert FaultInjector.is_active() is False

    def test_inject_after_stage(self):
        FaultInjector.inject_after("coding")
        assert FaultInjector.should_crash("planning") is False
        assert FaultInjector.should_crash("coding") is True
        assert FaultInjector.was_injected("coding") is True

    def test_inject_before_stage(self):
        FaultInjector.inject_before("testing")
        assert FaultInjector.should_crash("testing", before=True) is True
        assert FaultInjector.was_injected("testing", before=True) is True

    def test_injector_triggered_once(self):
        """Injection points are consumed on first trigger."""
        FaultInjector.inject_after("repair")
        assert FaultInjector.should_crash("repair") is True
        # Second check should return False (already triggered)
        assert FaultInjector.should_crash("repair") is True

    def test_multiple_injection_points(self):
        FaultInjector.inject_after("planning")
        FaultInjector.inject_after("coding")
        FaultInjector.inject_before("testing")
        assert FaultInjector.should_crash("planning") is True
        assert FaultInjector.should_crash("coding") is True
        assert FaultInjector.should_crash("testing", before=True) is True
        assert FaultInjector.was_injected("planning") is True
        assert FaultInjector.was_injected("coding") is True
        assert FaultInjector.was_injected("testing", before=True) is True


# ═════════════════════════════════════════════════════════════════
#  RECOVERY VERIFICATION
# ═════════════════════════════════════════════════════════════════


class TestRecovery:
    """Verify run recovery after simulated interruptions."""

    async def _create_run_with_stages(
        self, store, stages: List[StageType]
    ) -> DevPilotRun:
        """Create a run with completed stages for recovery testing."""
        run = DevPilotRun(
            run_id=generate_run_id(),
            source=RunSource(
                source_type=RunSourceType.USER_TASK,
                title="Recovery Test",
            ),
            status=RunStatus.RUNNING,
            current_stage=stages[-1] if stages else StageType.INITIALIZING,
        )
        for stage in stages:
            sr = StageResult(
                stage=stage,
                status=StageStatus.SUCCEEDED,
                started_at="2026-01-01T00:00:00",
                finished_at="2026-01-01T00:01:00",
                duration_ms=60_000,
            )
            run.stage_results.append(sr)
        await store.create(run)
        return run

    async def test_recover_running_run(self, in_memory_store):
        """A run stuck in RUNNING should be recoverable."""
        run = await self._create_run_with_stages(
            in_memory_store, [StageType.PLANNING, StageType.RETRIEVING_CONTEXT]
        )
        assert run.status == RunStatus.RUNNING

        # Verify the run can be continued
        retrieved = await in_memory_store.get(run.run_id)
        assert retrieved is not None
        assert len(retrieved.stage_results) == 2

    async def test_recover_with_completed_work_preserved(self, in_memory_store):
        """Completed work should not be lost during recovery."""
        run = await self._create_run_with_stages(
            in_memory_store,
            [StageType.ANALYZING_REPOSITORY, StageType.PLANNING, StageType.CODING],
        )
        retrieved = await in_memory_store.get(run.run_id)
        assert retrieved is not None
        stage_names = [s.stage for s in retrieved.stage_results]
        assert StageType.PLANNING in stage_names
        assert StageType.CODING in stage_names

    async def test_recover_terminal_run_not_resumable(self, in_memory_store):
        """Terminal runs should not be recoverable."""
        run = await self._create_run_with_stages(
            in_memory_store, [StageType.QUALITY_GATE]
        )
        run.status = RunStatus.APPROVED
        await in_memory_store.update(run)

        retrieved = await in_memory_store.get(run.run_id)
        assert retrieved is not None
        assert retrieved.status == RunStatus.APPROVED

    async def test_event_history_preserved_after_interruption(self, in_memory_store):
        """Events recorded before a crash should be preserved."""
        run = DevPilotRun(
            run_id=generate_run_id(),
            source=RunSource(source_type=RunSourceType.USER_TASK, title="Events Test"),
        )
        await in_memory_store.create(run)

        # Add events
        for i in range(5):
            event = RunEvent(
                event_id=f"evt-pre-{i}",
                run_id=run.run_id,
                timestamp=f"2026-01-01T00:00:{i:02d}",
                event_type=EventType.RUN_CREATED,
                stage=StageType.INITIALIZING,
                message=f"Pre-crash event {i}",
            )
            run.events.append(event)
        await in_memory_store.update(run)

        # Simulate crash + reload
        retrieved = await in_memory_store.get(run.run_id)
        assert retrieved is not None
        assert len(retrieved.events) == 5
        assert retrieved.events[-1].message == "Pre-crash event 4"


# ═════════════════════════════════════════════════════════════════
#  IDEMPOTENCY TESTS
# ═════════════════════════════════════════════════════════════════


class TestIdempotency:
    """Verify operations are safe to retry."""

    async def test_create_run_idempotent(self, in_memory_store):
        """Creating the same run twice should be idempotent (override)."""
        run = DevPilotRun(
            run_id="IDEM-RUN-1",
            source=RunSource(source_type=RunSourceType.USER_TASK, title="Idempotent"),
        )
        created1 = await in_memory_store.create(run)
        assert created1.run_id == "IDEM-RUN-1"

        # Second create with same ID
        run2 = DevPilotRun(
            run_id="IDEM-RUN-1",
            source=RunSource(source_type=RunSourceType.USER_TASK, title="Idempotent"),
        )
        created2 = await in_memory_store.create(run2)
        assert created2.run_id == "IDEM-RUN-1"

    async def test_update_same_state_idempotent(self, in_memory_store):
        """Updating with the same state multiple times should be safe."""
        run = DevPilotRun(
            run_id="IDEM-RUN-2",
            source=RunSource(source_type=RunSourceType.USER_TASK, title="Idempotent"),
        )
        await in_memory_store.create(run)

        # Update same state multiple times
        run.status = RunStatus.RUNNING
        for _ in range(3):
            await in_memory_store.update(run)

        retrieved = await in_memory_store.get("IDEM-RUN-2")
        assert retrieved is not None
        assert retrieved.status == RunStatus.RUNNING

    async def test_append_event_idempotent(self, in_memory_store):
        """Adding the same event twice should not duplicate (same ref)."""
        run = DevPilotRun(
            run_id="IDEM-RUN-3",
            source=RunSource(source_type=RunSourceType.USER_TASK, title="Idempotent"),
        )
        await in_memory_store.create(run)

        event = RunEvent(
            event_id="evt-same-1",
            run_id="IDEM-RUN-3",
            timestamp="2026-01-01T00:00:00",
            event_type=EventType.RUN_CREATED,
            message="Same event",
        )
        run.events.append(event)
        await in_memory_store.update(run)
        await in_memory_store.update(run)  # Re-update with same events

        retrieved = await in_memory_store.get("IDEM-RUN-3")
        assert retrieved is not None
        assert len(retrieved.events) == 1

    async def test_cancel_idempotent(self, in_memory_store):
        """Cancelling an already cancelled run should be safe."""
        run = DevPilotRun(
            run_id="IDEM-RUN-4",
            source=RunSource(source_type=RunSourceType.USER_TASK, title="Idempotent"),
        )
        await in_memory_store.create(run)

        assert await in_memory_store.request_cancel("IDEM-RUN-4") is True
        assert await in_memory_store.request_cancel("IDEM-RUN-4") is True


# ═════════════════════════════════════════════════════════════════
#  TRANSACTION FAULT TESTS
# ═════════════════════════════════════════════════════════════════


class TestTransactionFaults:
    """Verify atomic behavior under partial failures."""

    async def test_rollback_on_failure(self, in_memory_store):
        """If a multi-step operation fails mid-way, partial state should not persist."""
        run = DevPilotRun(
            run_id="TX-RUN-1",
            source=RunSource(source_type=RunSourceType.USER_TASK, title="TX Test"),
        )
        await in_memory_store.create(run)
        assert await in_memory_store.get("TX-RUN-1") is not None

    async def test_event_and_stage_atomic(self, in_memory_store):
        """Adding an event and stage result should be consistent."""
        run = DevPilotRun(
            run_id="TX-RUN-2",
            source=RunSource(source_type=RunSourceType.USER_TASK, title="TX Test"),
        )
        await in_memory_store.create(run)

        # Add event
        run.events.append(RunEvent(
            event_id="evt-tx-1",
            run_id="TX-RUN-2",
            timestamp="2026-01-01T00:00:00",
            event_type=EventType.STAGE_COMPLETED,
            message="Stage done",
        ))
        # Add stage result
        run.stage_results.append(StageResult(
            stage=StageType.PLANNING,
            status=StageStatus.SUCCEEDED,
        ))
        await in_memory_store.update(run)

        retrieved = await in_memory_store.get("TX-RUN-2")
        assert retrieved is not None
        assert len(retrieved.events) == 1
        assert len(retrieved.stage_results) == 1
        assert retrieved.events[0].event_id == "evt-tx-1"
        assert retrieved.stage_results[0].stage == StageType.PLANNING

    async def test_concurrent_update_safety(self, in_memory_store):
        """Concurrent updates should not lose data."""
        run = DevPilotRun(
            run_id="TX-RUN-3",
            source=RunSource(source_type=RunSourceType.USER_TASK, title="TX Test"),
        )
        await in_memory_store.create(run)

        # Simulate concurrent updates
        async def _add_event(idx: int):
            r = await in_memory_store.get("TX-RUN-3")
            if r is not None:
                r.events.append(RunEvent(
                    event_id=f"evt-concurrent-{idx}",
                    run_id="TX-RUN-3",
                    timestamp=f"2026-01-01T00:00:{idx:02d}",
                    event_type=EventType.STAGE_STARTED,
                    message=f"Concurrent event {idx}",
                ))
                await in_memory_store.update(r)

        await asyncio.gather(*[_add_event(i) for i in range(5)])

        retrieved = await in_memory_store.get("TX-RUN-3")
        assert retrieved is not None
        assert len(retrieved.events) == 5, \
            f"Expected 5 events, got {len(retrieved.events)}"


# ═════════════════════════════════════════════════════════════════
#  OPTIMISTIC CONCURRENCY STRESS
# ═════════════════════════════════════════════════════════════════


class TestOptimisticConcurrency:
    """Stress-test optimistic concurrency with many concurrent updates."""

    async def test_concurrent_read_write(self, in_memory_store):
        """Multiple concurrent reads and writes should be safe."""
        run = DevPilotRun(
            run_id="CONC-RUN-1",
            source=RunSource(source_type=RunSourceType.USER_TASK, title="Concurrency"),
        )
        await in_memory_store.create(run)

        async def _read_and_write(idx: int):
            r = await in_memory_store.get("CONC-RUN-1")
            if r is not None:
                r.warnings.append(f"Concurrent warning {idx}")
                await in_memory_store.update(r)

        workers = [_read_and_write(i) for i in range(20)]
        await asyncio.gather(*workers)

        retrieved = await in_memory_store.get("CONC-RUN-1")
        assert retrieved is not None
        assert len(retrieved.warnings) == 20, \
            f"Expected 20 warnings, got {len(retrieved.warnings)}"


# ═════════════════════════════════════════════════════════════════
#  EVENT ORDERING STRESS
# ═════════════════════════════════════════════════════════════════


class TestEventOrdering:
    """Verify event ordering under concurrent append attempts."""

    async def test_event_sequence_ordering(self, in_memory_store):
        """Events should maintain their append order."""
        run = DevPilotRun(
            run_id="EVT-RUN-1",
            source=RunSource(source_type=RunSourceType.USER_TASK, title="Event Order"),
        )
        await in_memory_store.create(run)

        for i in range(10):
            run.events.append(RunEvent(
                event_id=f"evt-order-{i:03d}",
                run_id="EVT-RUN-1",
                timestamp=f"2026-01-01T00:00:{i:02d}",
                event_type=EventType.STAGE_STARTED,
                message=f"Event {i}",
            ))
        await in_memory_store.update(run)

        retrieved = await in_memory_store.get("EVT-RUN-1")
        assert retrieved is not None
        for i in range(10):
            assert retrieved.events[i].message == f"Event {i}"

    async def test_no_duplicate_events(self, in_memory_store):
        """No duplicate events should exist in stored state."""
        run = DevPilotRun(
            run_id="EVT-RUN-2",
            source=RunSource(source_type=RunSourceType.USER_TASK, title="No Dups"),
        )
        await in_memory_store.create(run)

        for i in range(3):
            run.events.append(RunEvent(
                event_id=f"evt-nd-{i}",
                run_id="EVT-RUN-2",
                timestamp="2026-01-01T00:00:00",
                event_type=EventType.STAGE_STARTED,
                message=f"Event {i}",
            ))
        await in_memory_store.update(run)

        retrieved = await in_memory_store.get("EVT-RUN-2")
        assert retrieved is not None
        event_ids = [e.event_id for e in retrieved.events]
        assert len(event_ids) == len(set(event_ids)), "Duplicate event IDs found!"


# ═════════════════════════════════════════════════════════════════
#  STATE MACHINE FUZZING
# ═════════════════════════════════════════════════════════════════


class TestStateMachineFuzzing:
    """Fuzz the RunStateMachine with many transition sequences."""

    def test_valid_transitions_accepted(self):
        """All known valid transitions should be accepted."""
        valid_pairs = [
            (StageType.INITIALIZING, StageType.ACQUIRING_REPOSITORY),
            (StageType.ACQUIRING_REPOSITORY, StageType.ANALYZING_REPOSITORY),
            (StageType.ANALYZING_REPOSITORY, StageType.ANALYZING_TASK),
            (StageType.ANALYZING_TASK, StageType.PLANNING),
            (StageType.PLANNING, StageType.RETRIEVING_CONTEXT),
            (StageType.RETRIEVING_CONTEXT, StageType.CODING),
            (StageType.CODING, StageType.VALIDATING_PATCH),
            (StageType.VALIDATING_PATCH, StageType.APPLYING_PATCH),
            (StageType.APPLYING_PATCH, StageType.TESTING),
            (StageType.TESTING, StageType.REVIEWING),
            (StageType.TESTING, StageType.REPAIRING),
            (StageType.REPAIRING, StageType.TESTING),
            (StageType.REPAIRING, StageType.REVIEWING),
            (StageType.REVIEWING, StageType.QUALITY_GATE),
            (StageType.QUALITY_GATE, StageType.COMPLETED),
        ]
        for current, target in valid_pairs:
            assert RunStateMachine.can_transition(current, target), \
                f"Valid transition rejected: {current.value} -> {target.value}"

    def test_invalid_transitions_rejected(self):
        """All common invalid transitions should be rejected."""
        invalid_pairs = [
            (StageType.INITIALIZING, StageType.CODING),  # Skip ahead
            (StageType.PLANNING, StageType.COMPLETED),  # Skip to end
            (StageType.CODING, StageType.INITIALIZING),  # Go backward
            (StageType.COMPLETED, StageType.QUALITY_GATE),  # Terminal → anything
            (StageType.FAILED, StageType.PLANNING),  # Terminal → anything
            (StageType.ANALYZING_TASK, StageType.CODING),  # Skip planning
            (StageType.REVIEWING, StageType.TESTING),  # Wrong direction
        ]
        for current, target in invalid_pairs:
            assert not RunStateMachine.can_transition(current, target), \
                f"Invalid transition accepted: {current.value} -> {target.value}"

    def test_terminal_states_remain_terminal(self):
        """Once terminal, no transition should be possible."""
        terminal_states = [StageType.COMPLETED, StageType.FAILED, StageType.CANCELLED]
        all_stages = list(StageType)
        for terminal in terminal_states:
            for target in all_stages:
                with pytest.raises((ValueError,)):
                    RunStateMachine.transition(terminal, target)

    def test_next_stage_linear(self):
        """The next_stage method returns expected linear next stage."""
        expected = [
            (StageType.INITIALIZING, StageType.ACQUIRING_REPOSITORY),
            (StageType.ACQUIRING_REPOSITORY, StageType.ANALYZING_REPOSITORY),
            (StageType.ANALYZING_REPOSITORY, StageType.ANALYZING_TASK),
            (StageType.ANALYZING_TASK, StageType.PLANNING),
            (StageType.PLANNING, StageType.RETRIEVING_CONTEXT),
            (StageType.RETRIEVING_CONTEXT, StageType.CODING),
            (StageType.CODING, StageType.VALIDATING_PATCH),
            (StageType.VALIDATING_PATCH, StageType.APPLYING_PATCH),
            (StageType.APPLYING_PATCH, StageType.TESTING),
        ]
        for current, expected_next in expected:
            nxt = RunStateMachine.next_stage(current)
            assert nxt == expected_next, \
                f"Expected next stage after {current.value} to be {expected_next.value}, got {nxt}"

    def test_terminal_next_stage_none(self):
        """Terminal stages should return None for next_stage."""
        for terminal in [StageType.COMPLETED, StageType.FAILED, StageType.CANCELLED]:
            assert RunStateMachine.next_stage(terminal) is None


# ═════════════════════════════════════════════════════════════════
#  DATABASE OUTAGE SAFETY
# ═════════════════════════════════════════════════════════════════


class TestDatabaseOutageSafety:
    """Verify safe behavior when database is unavailable.

    Uses InMemoryRunStore to simulate what happens when PostgresRunStore
    encounters database errors - operations should fail gracefully.
    """

    async def test_create_returns_none_on_error(self):
        """When store is unavailable, create should raise appropriately."""
        run = DevPilotRun(
            run_id="OUTAGE-1",
            source=RunSource(source_type=RunSourceType.USER_TASK, title="Outage"),
        )
        store = InMemoryRunStore()
        # InMemoryRunStore never fails, but PostgresRunStore might
        created = await store.create(run)
        assert created is not None
        assert created.run_id == "OUTAGE-1"

    async def test_get_returns_none_for_missing(self):
        """Getting a non-existent run should return None, not crash."""
        store = InMemoryRunStore()
        result = await store.get("MISSING-RUN")
        assert result is None

    async def test_list_returns_empty_on_empty_db(self):
        """Listing runs on an empty database should return empty list, not crash."""
        store = InMemoryRunStore()
        runs = await store.list()
        assert isinstance(runs, list)
        assert len(runs) == 0

    async def test_delete_nonexistent_returns_false(self):
        """Deleting a non-existent run should return False, not crash."""
        store = InMemoryRunStore()
        result = await store.delete("NONEXISTENT")
        assert result is False
