"""
Provider Metrics Persistence Loop — Phase 20B.

Periodically snapshots the router's per-provider health into PostgreSQL via
``ProviderMetricsStore`` so routing health history survives restarts even
when there is no request traffic. Complements the existing manual snapshot
API with an automatic, background cadence.

The loop is a no-op (never raises) when persistence is disabled or no
database is configured — the store already degrades gracefully. Managed by
the FastAPI lifespan alongside the provider health probe loop.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

from app.config import settings as _default_settings
from app.core.logging import logger


class ProviderMetricsPersistenceLoop:
    """Periodic provider-metric snapshot persister."""

    def __init__(
        self,
        router: Any = None,
        store: Any = None,
        settings: Any = None,
        sleep: Any = asyncio.sleep,
    ) -> None:
        from app.llm.router import get_router

        self._router = router if router is not None else get_router()
        self._settings = settings if settings is not None else _default_settings
        self._sleep = sleep
        self._store = store
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self.runs = 0
        self.last_run_at: Optional[float] = None

    @property
    def enabled(self) -> bool:
        return bool(getattr(self._settings, "PROVIDER_METRICS_PERSIST", True))

    @property
    def interval_seconds(self) -> float:
        return float(getattr(
            self._settings, "PROVIDER_METRICS_PERSIST_INTERVAL_SECONDS", 300.0))

    def _get_store(self):
        if self._store is not None:
            return self._store
        from app.services.provider_metrics_store import get_provider_metrics_store

        return get_provider_metrics_store()

    def start(self) -> None:
        """Start the background persistence loop (idempotent)."""
        if self._task is not None or not self.enabled:
            return
        self._stop.clear()
        self._task = asyncio.create_task(
            self._loop(), name="provider-metrics-persistence"
        )
        logger.info(
            "Provider metrics persistence loop started (every %.0fs)",
            self.interval_seconds,
        )

    async def stop(self) -> None:
        """Stop the background loop and await its completion."""
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=10.0)
            except (asyncio.TimeoutError, Exception):
                self._task.cancel()
            self._task = None
        logger.debug("Provider metrics persistence loop stopped")

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.persist_once()
            except Exception as exc:
                logger.debug("Provider metrics persistence run failed: %s", exc)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                continue

    async def persist_once(self) -> bool:
        """Persist one snapshot round; returns True when rows were written."""
        store = self._get_store()
        if not store.enabled:
            return False
        snap = self._router.health_snapshot()
        entries = []
        for p in snap.get("providers", []):
            h = p.get("health", {})
            entries.append({
                "provider": p.get("name", "unknown"),
                "status": p.get("status", "unknown"),
                "circuit_state": p.get("circuit_state", "closed"),
                "total_requests": h.get("total_requests", 0),
                "successful_requests": h.get("successful_requests", 0),
                "failed_requests": h.get("failed_requests", 0),
                "retries": h.get("retries", 0),
                "failovers": h.get("failovers", 0),
                "avg_latency_ms": h.get("avg_latency_ms"),
                "success_rate": h.get("success_rate"),
            })
        ok = await store.record_snapshot(entries)
        self.runs += 1
        self.last_run_at = time.time()
        return ok

    def snapshot(self) -> dict:
        return {
            "enabled": self.enabled,
            "interval_seconds": self.interval_seconds,
            "runs": self.runs,
            "last_run_at": self.last_run_at,
        }


_persistence: Optional[ProviderMetricsPersistenceLoop] = None


def get_provider_metrics_persistence() -> ProviderMetricsPersistenceLoop:
    """Return the shared persistence-loop instance."""
    global _persistence
    if _persistence is None:
        _persistence = ProviderMetricsPersistenceLoop()
    return _persistence
