"""
System Metrics — Phase 20B runtime observability.

Tracks the operational pulse of a long-running deployment:

- run throughput (started/completed + per-minute rate) and durations
- repository processing time (analysis + retrieval stages)
- autonomous execution goals + durations
- provider health passthrough (latency, failover, retry, recovery counts)
- resource utilization (process RSS memory, active WebSocket connections,
  open asyncio tasks)

All windows are bounded deques (``OPERATIONS_METRICS_HISTORY``) so the
service cannot grow without limit during long-running execution. Memory
measurement degrades gracefully: ``psutil`` when installed, ``resource`` on
POSIX, else ``None`` (Windows without psutil).

The service is a plain-thread-locked counter store: callers may record from
any coroutine, and ``snapshot()`` is safe to call from API handlers and the
dashboard poller.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional

from app.config import settings as _default_settings

_MAX_HISTORY = 1000


class SystemMetricsService:
    """Bounded in-process operational metrics store."""

    def __init__(self, max_history: int = _MAX_HISTORY) -> None:
        self._max_history = max(10, int(max_history))
        self._lock = threading.Lock()
        self.started_at = time.time()
        self._run_events: Deque[Dict[str, Any]] = deque(maxlen=self._max_history)
        self._repo_events: Deque[Dict[str, Any]] = deque(maxlen=self._max_history)
        self._autonomy_events: Deque[Dict[str, Any]] = deque(maxlen=self._max_history)
        self._active_runs: Dict[str, float] = {}
        self._active_goals: Dict[str, float] = {}
        self.run_started_total = 0
        self.run_completed_total = 0
        self.repository_processed_total = 0
        self.autonomy_goals_total = 0

    # ── recording ───────────────────────────────────────────────

    def record_run_started(self, run_id: str) -> None:
        with self._lock:
            self.run_started_total += 1
            self._active_runs[run_id] = time.time()
            self._run_events.append({"event": "started", "run_id": run_id, "ts": time.time()})

    def record_run_completed(self, run_id: str, duration_ms: float) -> None:
        with self._lock:
            self.run_completed_total += 1
            self._active_runs.pop(run_id, None)
            self._run_events.append({
                "event": "completed",
                "run_id": run_id,
                "ts": time.time(),
                "duration_ms": duration_ms,
            })

    def record_repository_processing(self, repository_id: str, seconds: float) -> None:
        with self._lock:
            self.repository_processed_total += 1
            self._repo_events.append({
                "repository_id": repository_id,
                "seconds": seconds,
                "ts": time.time(),
            })

    def record_autonomy_goal(self, goal_id: str, duration_seconds: float, state: str) -> None:
        with self._lock:
            self.autonomy_goals_total += 1
            self._active_goals.pop(goal_id, None)
            self._autonomy_events.append({
                "goal_id": goal_id,
                "duration_seconds": duration_seconds,
                "state": state,
                "ts": time.time(),
            })

    def record_autonomy_goal_started(self, goal_id: str) -> None:
        with self._lock:
            self._active_goals[goal_id] = time.time()

    # ── derived metrics ─────────────────────────────────────────

    def active_runs(self) -> int:
        with self._lock:
            return len(self._active_runs)

    def active_goals(self) -> int:
        with self._lock:
            return len(self._active_goals)

    def run_throughput_per_minute(self) -> float:
        """Completed runs in the trailing 60s window (a raw count)."""
        cutoff = time.time() - 60.0
        with self._lock:
            count = sum(
                1 for e in self._run_events
                if e["event"] == "completed" and e["ts"] >= cutoff
            )
        return round(count, 3)

    def recent_run_durations_ms(self) -> List[float]:
        with self._lock:
            return [
                e["duration_ms"]
                for e in self._run_events
                if e["event"] == "completed" and e.get("duration_ms") is not None
            ]

    @staticmethod
    def memory_usage_mb() -> Optional[float]:
        """Current process RSS in MiB, or None when unmeasurable."""
        try:
            import psutil  # type: ignore

            return round(psutil.Process().memory_info().rss / (1024 * 1024), 2)
        except Exception:
            pass
        try:
            import resource  # type: ignore

            return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 2)
        except Exception:
            return None

    @staticmethod
    def open_task_count() -> int:
        try:
            return len(asyncio.all_tasks())
        except RuntimeError:
            return 0

    @staticmethod
    def active_ws_connections() -> int:
        try:
            from app.services.ws_manager import ws_manager

            return ws_manager.active_connections
        except Exception:
            return 0

    # ── snapshot ────────────────────────────────────────────────

    def snapshot(self) -> Dict[str, Any]:
        """Full operational metrics snapshot (secret-safe, bounded)."""
        with self._lock:
            active_runs = len(self._active_runs)
            active_goals = len(self._active_goals)
            completed_durations = [
                e["duration_ms"]
                for e in self._run_events
                if e["event"] == "completed" and e.get("duration_ms") is not None
            ]
            repo_seconds = [e["seconds"] for e in self._repo_events]
            autonomy_durations = [
                e["duration_seconds"] for e in self._autonomy_events
            ]
            # Completed runs in the trailing 60s window (the SAME definition
            # as run_throughput_per_minute()) — a raw count, not a per-second
            # rate, so the metric genuinely means "runs per minute".
            throughput = round(sum(
                1 for e in self._run_events
                if e["event"] == "completed" and e["ts"] >= time.time() - 60.0
            ), 3)

        # Provider passthrough (never raises; the router is always importable).
        provider: Dict[str, Any] = {}
        try:
            from app.llm.router import get_router

            r = get_router()
            provider = {
                "active_provider": r.active_provider,
                "latency_ms": {
                    name: snap.get("avg_latency_ms")
                    for name, snap in r.metrics_snapshot().get("per_provider", {}).items()
                },
                "totals": r.metrics.totals(),
            }
        except Exception:
            provider = {}

        return {
            "uptime_seconds": round(time.time() - self.started_at, 2),
            "runs": {
                "active": active_runs,
                "started_total": self.run_started_total,
                "completed_total": self.run_completed_total,
                "throughput_per_minute": throughput,
                "avg_duration_ms": round(
                    sum(completed_durations) / len(completed_durations), 2
                ) if completed_durations else None,
                "recent_duration_ms": completed_durations[-20:],
            },
            "repositories": {
                "processed_total": self.repository_processed_total,
                "avg_processing_seconds": round(
                    sum(repo_seconds) / len(repo_seconds), 3
                ) if repo_seconds else None,
                "recent_seconds": repo_seconds[-20:],
            },
            "autonomy": {
                "active_goals": active_goals,
                "goals_total": self.autonomy_goals_total,
                "avg_duration_seconds": round(
                    sum(autonomy_durations) / len(autonomy_durations), 2
                ) if autonomy_durations else None,
                "recent_states": [e["state"] for e in self._autonomy_events][-20:],
            },
            "providers": provider,
            "resources": {
                "memory_mb": self.memory_usage_mb(),
                "active_ws_connections": self.active_ws_connections(),
                "open_tasks": self.open_task_count(),
            },
            "recorded_at": time.time(),
        }


_system_metrics: Optional[SystemMetricsService] = None


def get_system_metrics() -> SystemMetricsService:
    """Return the shared SystemMetricsService instance."""
    global _system_metrics
    if _system_metrics is None:
        _system_metrics = SystemMetricsService(
            max_history=int(getattr(_default_settings, "OPERATIONS_METRICS_HISTORY", _MAX_HISTORY))
        )
    return _system_metrics
