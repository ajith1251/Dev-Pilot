"""
Provider router metrics persistence.

Stores periodic provider-metric snapshots in PostgreSQL so routing health is
observable across restarts (Phase 19B reliability requirement).

Design:
- One row per (provider, recorded_at) — a point-in-time snapshot.
- ``record_snapshot`` is idempotent and safe: it is a no-op when persistence is
  disabled or when PostgreSQL is not configured, so the router never blocks on
  the database.
- All reads go through ``latest()`` / ``history()`` which return plain dicts
  suitable for the API and dashboard.

The table is created by alembic migration ``014_add_provider_metrics.py``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.core.logging import logger
from app.db.session import create_session_factory

_SNAPSHOT_SQL = text(
    """
    INSERT INTO provider_metric_snapshots (
        provider, status, circuit_state, total_requests,
        successful_requests, failed_requests, retries, failovers,
        avg_latency_ms, success_rate, recorded_at
    ) VALUES (
        :provider, :status, :circuit_state, :total_requests,
        :successful_requests, :failed_requests, :retries, :failovers,
        :avg_latency_ms, :success_rate, :recorded_at
    )
    """
)

_LATEST_SQL = text(
    """
    SELECT provider, status, circuit_state, total_requests,
           successful_requests, failed_requests, retries, failovers,
           avg_latency_ms, success_rate, recorded_at
    FROM provider_metric_snapshots
    WHERE provider = :provider
    ORDER BY recorded_at DESC
    LIMIT 1
    """
)

_HISTORY_SQL = text(
    """
    SELECT provider, status, circuit_state, total_requests,
           successful_requests, failed_requests, retries, failovers,
           avg_latency_ms, success_rate, recorded_at
    FROM provider_metric_snapshots
    WHERE provider = :provider
    ORDER BY recorded_at DESC
    LIMIT :limit
    """
)

_PROVIDERS_SQL = text(
    """
    SELECT DISTINCT provider FROM provider_metric_snapshots
    """
)


def _row_to_dict(row: Any) -> Dict[str, Any]:
    return {
        "provider": row[0],
        "status": row[1],
        "circuit_state": row[2],
        "total_requests": row[3],
        "successful_requests": row[4],
        "failed_requests": row[5],
        "retries": row[6],
        "failovers": row[7],
        "avg_latency_ms": row[8],
        "success_rate": row[9],
        "recorded_at": row[10].isoformat() if isinstance(row[10], datetime) else row[10],
    }


class ProviderMetricsStore:
    """Best-effort PostgreSQL persistence for provider router metrics."""

    def __init__(
        self,
        session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
    ) -> None:
        self._session_factory = session_factory
        self._owned_factory: Optional[async_sessionmaker[AsyncSession]] = None

    @property
    def enabled(self) -> bool:
        return bool(settings.PROVIDER_METRICS_PERSIST) and bool(settings.DATABASE_URL)

    def _get_factory(self) -> Optional[async_sessionmaker[AsyncSession]]:
        if not self.enabled:
            return None
        if self._session_factory is not None:
            return self._session_factory
        if self._owned_factory is None:
            self._owned_factory = create_session_factory()
        return self._owned_factory

    async def record_snapshot(self, entries: List[Dict[str, Any]]) -> bool:
        """Persist one snapshot row per provider entry.

        Args:
            entries: List of per-provider dicts (from the router's
                health_snapshot() providers, or metrics per_provider values).

        Returns:
            True if persisted, False if disabled/unavailable (never raises).
        """
        factory = self._get_factory()
        if factory is None or not entries:
            return False
        try:
            now = datetime.now(timezone.utc)
            async with factory() as session:
                for entry in entries:
                    await session.execute(
                        _SNAPSHOT_SQL,
                        {
                            "provider": entry.get("provider") or entry.get("name", "unknown"),
                            "status": entry.get("status", "unknown"),
                            "circuit_state": entry.get("circuit_state", "closed"),
                            "total_requests": int(entry.get("total_requests", 0) or 0),
                            "successful_requests": int(entry.get("successful_requests", 0) or 0),
                            "failed_requests": int(entry.get("failed_requests", 0) or 0),
                            "retries": int(entry.get("retries", 0) or 0),
                            "failovers": int(entry.get("failovers", 0) or 0),
                            "avg_latency_ms": entry.get("avg_latency_ms"),
                            "success_rate": entry.get("success_rate"),
                            "recorded_at": now,
                        },
                    )
                await session.commit()
            return True
        except Exception as exc:
            logger.warning("Provider metrics persistence skipped: %s", exc)
            return False

    async def latest(self, provider: str) -> Optional[Dict[str, Any]]:
        """Return the most recent persisted snapshot for a provider (or None)."""
        factory = self._get_factory()
        if factory is None:
            return None
        try:
            async with factory() as session:
                result = await session.execute(_LATEST_SQL, {"provider": provider})
                row = result.fetchone()
                return _row_to_dict(row) if row else None
        except Exception as exc:
            logger.debug("Provider metrics latest read skipped: %s", exc)
            return None

    async def history(self, provider: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Return recent persisted snapshots for a provider, newest first."""
        factory = self._get_factory()
        if factory is None:
            return []
        try:
            async with factory() as session:
                result = await session.execute(
                    _HISTORY_SQL, {"provider": provider, "limit": limit}
                )
                return [_row_to_dict(row) for row in result.fetchall()]
        except Exception as exc:
            logger.debug("Provider metrics history read skipped: %s", exc)
            return []

    async def all_providers(self) -> List[str]:
        """Return providers that have persisted snapshots."""
        factory = self._get_factory()
        if factory is None:
            return []
        try:
            async with factory() as session:
                result = await session.execute(_PROVIDERS_SQL)
                return [row[0] for row in result.fetchall()]
        except Exception as exc:
            logger.debug("Provider metrics providers read skipped: %s", exc)
            return []

    async def latest_all(self) -> Dict[str, Dict[str, Any]]:
        """Return latest persisted snapshot per provider."""
        result: Dict[str, Dict[str, Any]] = {}
        for provider in await self.all_providers():
            entry = await self.latest(provider)
            if entry:
                result[provider] = entry
        return result


def get_provider_metrics_store() -> ProviderMetricsStore:
    """Return the shared ProviderMetricsStore instance."""
    global _store
    if _store is None:
        _store = ProviderMetricsStore()
    return _store


_store: Optional[ProviderMetricsStore] = None
