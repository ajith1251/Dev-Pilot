"""
RunStore — storage abstraction for DevPilotRun persistence.

Defines an interface (RunStore) and an in-memory implementation
(InMemoryRunStore) for Phase 10 orchestration. Phase 11 introduced
PostgresRunStore for persistent storage.

All storage operations are async to support PostgreSQL-backed storage.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from app.models.base import new_id
from app.models.orchestration import DevPilotRun, RunSource, RunSourceType, RunStatus, RunEvent, StageResult


def generate_run_id() -> str:
    """Generate a unique run ID."""
    return f"RUN-{new_id()[:8].upper()}"


@runtime_checkable
class RunStore(Protocol):
    """Interface for run storage.

    All methods are async to support PostgreSQL-backed persistent storage.
    InMemoryRunStore (sync) wraps operations in asyncio for compatibility.
    """

    async def create(self, run: DevPilotRun) -> DevPilotRun:
        """Store a new run."""
        ...

    async def get(self, run_id: str) -> Optional[DevPilotRun]:
        """Retrieve a run by ID."""
        ...

    async def update(self, run: DevPilotRun) -> DevPilotRun:
        """Update an existing run."""
        ...

    async def list(
        self,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        sort_by: str = "newest",
        created_after: Optional[str] = None,
        created_before: Optional[str] = None,
    ) -> List[DevPilotRun]:
        """List runs with optional filtering."""
        ...

    async def delete(self, run_id: str) -> bool:
        """Remove a run from storage."""
        ...

    async def request_cancel(self, run_id: str) -> bool:
        """Request cancellation of a run."""
        ...

    async def count_runs(self, status: Optional[str] = None, created_after: Optional[str] = None, created_before: Optional[str] = None) -> int:
        """Count runs, with optional status filter and date range."""
        ...

    async def count_runs_by_status(self) -> Dict[str, int]:
        """Return aggregate counts for each run status.

        Returns a dict like {"total": 10, "pending": 2, "running": 1, ...}
        """
        ...


class InMemoryRunStore:
    """Thread-safe in-memory run storage.

    Limitations:
    - Data is lost on process restart
    - Not suitable for multi-process or distributed deployment
    - No query capabilities beyond basic list/filter
    """

    def __init__(self) -> None:
        self._runs: Dict[str, DevPilotRun] = {}
        self._lock = threading.Lock()

    async def create(self, run: DevPilotRun) -> DevPilotRun:
        with self._lock:
            self._runs[run.run_id] = run
        return run

    async def get(self, run_id: str) -> Optional[DevPilotRun]:
        with self._lock:
            return self._runs.get(run_id)

    async def update(self, run: DevPilotRun) -> DevPilotRun:
        with self._lock:
            self._runs[run.run_id] = run
        return run

    async def list(
        self,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        sort_by: str = "newest",
        created_after: Optional[str] = None,
        created_before: Optional[str] = None,
    ) -> List[DevPilotRun]:
        with self._lock:
            runs = list(self._runs.values())
            if status:
                runs = [r for r in runs if r.status.value == status]
            # Half-open interval semantics: created_after inclusive (>=),
            # created_before exclusive (<) — matches PostgresRunStore and the
            # API contract (TestSeededTotalCount boundary assertions).
            if created_after:
                runs = [r for r in runs if r.created_at >= created_after]
            if created_before:
                runs = [r for r in runs if r.created_at < created_before]
            if sort_by == "oldest":
                runs.sort(key=lambda r: r.created_at)
            elif sort_by == "duration":
                runs.sort(key=lambda r: r.total_duration_ms or 0, reverse=True)
            else:
                runs.sort(key=lambda r: r.created_at, reverse=True)
            return runs[offset : offset + limit]

    async def delete(self, run_id: str) -> bool:
        with self._lock:
            if run_id in self._runs:
                del self._runs[run_id]
                return True
            return False

    async def request_cancel(self, run_id: str) -> bool:
        """Request cancellation. Returns False if run not found or already terminal."""
        with self._lock:
            run = self._runs.get(run_id)
            if not run:
                return False
            if run.status in (
                RunStatus.APPROVED,
                RunStatus.REJECTED,
                RunStatus.NEEDS_HUMAN_REVIEW,
                RunStatus.FAILED,
                RunStatus.CANCELLED,
            ):
                return False
            run.cancellation_requested = True
            return True

    async def count_runs(self, status: Optional[str] = None, created_after: Optional[str] = None, created_before: Optional[str] = None) -> int:
        """Count runs, with optional status filter and date range."""
        with self._lock:
            runs = list(self._runs.values())
            if status:
                runs = [r for r in runs if r.status.value == status]
            # Half-open interval semantics (see list()): created_after inclusive,
            # created_before exclusive.
            if created_after:
                runs = [r for r in runs if r.created_at >= created_after]
            if created_before:
                runs = [r for r in runs if r.created_at < created_before]
            return len(runs)

    async def count_runs_by_status(self) -> Dict[str, int]:
        """Return aggregate counts for each run status."""
        counts: Dict[str, int] = {
            "total": 0,
            "pending": 0, "running": 0, "approved": 0,
            "rejected": 0, "needs_human_review": 0, "failed": 0, "cancelled": 0,
        }
        with self._lock:
            for run in self._runs.values():
                counts["total"] += 1
                status = run.status.value
                if status in counts:
                    counts[status] += 1
        return counts
