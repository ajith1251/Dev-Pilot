"""
Provider Health Probe — Phase 20B provider reliability.

Runs a periodic background loop that issues minimal chat calls to every
configured provider through the router's ``probe_all()``, so outages are
detected and recoveries are observed even when there is no real traffic.

Design:

- Probes are PASSIVE: they never trip a circuit breaker and never enter the
  traffic success-rate window (probing must not distort real-traffic health).
- A probe success after a failure spell is recorded as a recovery and the
  provider enters a short warm-up period (ranked below fully-healthy
  providers by the router's health-based selection).
- The loop is an idempotent singleton managed by the FastAPI lifespan
  (``start()`` on startup, ``await stop()`` on shutdown). An injected sleep /
  stop-event makes the loop fully deterministic in tests.

Enabled only when ``DEVPILOT_PROVIDER_HEALTH_PROBE_ENABLED`` is true AND
``DEVPILOT_PROVIDER_HEALTH_PROBE_INTERVAL_SECONDS`` > 0.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional

from app.config import settings as _default_settings
from app.core.logging import logger


class ProviderHealthProbe:
    """Periodic background prober over a ProviderRouter."""

    def __init__(
        self,
        router: Any = None,
        settings: Any = None,
        sleep: Any = asyncio.sleep,
    ) -> None:
        from app.llm.router import get_router

        self._router = router if router is not None else get_router()
        self._settings = settings if settings is not None else _default_settings
        self._sleep = sleep
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self.runs = 0
        self.last_run_at: Optional[float] = None
        self.last_results: Dict[str, bool] = {}

    # ── config ─────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        """True when probing is configured on and has a positive interval."""
        return (
            bool(getattr(self._settings, "PROVIDER_HEALTH_PROBE_ENABLED", True))
            and float(getattr(
                self._settings, "PROVIDER_HEALTH_PROBE_INTERVAL_SECONDS", 120.0)) > 0
        )

    @property
    def interval_seconds(self) -> float:
        return float(getattr(
            self._settings, "PROVIDER_HEALTH_PROBE_INTERVAL_SECONDS", 120.0))

    # ── lifecycle ───────────────────────────────────────────────

    def start(self) -> None:
        """Start the background probe loop (idempotent)."""
        if self._task is not None or not self.enabled:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="provider-health-probe")
        logger.info(
            "Provider health probe loop started (every %.0fs)",
            self.interval_seconds,
        )

    async def stop(self) -> None:
        """Stop the background probe loop and await its completion."""
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=10.0)
            except (asyncio.TimeoutError, Exception):
                self._task.cancel()
            self._task = None
        logger.debug("Provider health probe loop stopped")

    async def _loop(self) -> None:
        """Run probes every interval until stopped."""
        while not self._stop.is_set():
            try:
                await self.probe_once()
            except Exception as exc:
                logger.warning("Provider probe run failed (non-fatal): %s", exc)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                continue

    async def probe_once(self) -> Dict[str, bool]:
        """Run one probe round immediately and record the results."""
        self.last_results = await self._router.probe_all()
        self.last_run_at = time.time()
        self.runs += 1
        return self.last_results

    def snapshot(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "interval_seconds": self.interval_seconds,
            "runs": self.runs,
            "last_run_at": self.last_run_at,
            "last_results": dict(self.last_results),
        }


_probe: Optional[ProviderHealthProbe] = None


def get_provider_probe() -> ProviderHealthProbe:
    """Return the shared ProviderHealthProbe instance."""
    global _probe
    if _probe is None:
        _probe = ProviderHealthProbe()
    return _probe
