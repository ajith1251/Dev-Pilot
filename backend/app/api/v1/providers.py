"""
Phase 19B API — Provider Router observability.

Endpoints (all responses are secret-safe — API keys/credentials are never
serialized; the config endpoint returns masked key suffixes only):

    GET  /api/v1/providers           — registered providers + priority + active
    GET  /api/v1/providers/health    — per-provider health status
    GET  /api/v1/providers/metrics   — runtime metrics + failover events
    GET  /api/v1/providers/config    — redacted routing configuration
    POST /api/v1/providers/test      — route one benign chat call (optional)
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException

from app.llm.base import LLMConfig, LLMMessage

router = APIRouter(prefix="/api/v1/providers", tags=["providers"])


def _get_router():
    """Return the shared ProviderRouter singleton (patchable in tests)."""
    from app.llm.router import get_router

    return get_router()


@router.get("")
async def provider_overview() -> Dict[str, Any]:
    """Registered providers, priority order and the active provider."""
    r = _get_router()
    return {
        "success": True,
        "data": {
            "routing_enabled": bool(r._settings.PROVIDER_ROUTING_ENABLED),
            "active_provider": r.active_provider,
            "priority": r._priority(),
            "providers": r.provider_snapshots(),
        },
    }


@router.get("/health")
async def provider_health() -> Dict[str, Any]:
    """Per-provider health: status, circuit state, latency, success rate."""
    r = _get_router()
    return {"success": True, "data": r.health_snapshot()}


@router.get("/metrics")
async def provider_metrics() -> Dict[str, Any]:
    """Runtime metrics: totals, per-provider counters, failover events."""
    r = _get_router()
    snapshot = r.metrics_snapshot()
    # Best-effort: attach the latest persisted snapshot when PostgreSQL is
    # available, so the dashboard can show history across restarts.
    persisted = None
    if r._settings.PROVIDER_METRICS_PERSIST:
        try:
            from app.services.provider_metrics_store import get_provider_metrics_store

            persisted = await get_provider_metrics_store().latest_all()
        except Exception:
            persisted = None
    if persisted:
        snapshot["persisted"] = persisted
    return {"success": True, "data": snapshot}


@router.get("/metrics/history")
async def provider_metrics_history(
    provider: str, limit: int = 20
) -> Dict[str, Any]:
    """Persisted metric history for a single provider (newest first)."""
    r = _get_router()
    if not r._settings.PROVIDER_METRICS_PERSIST:
        return {"success": True, "data": []}
    try:
        from app.services.provider_metrics_store import get_provider_metrics_store

        rows = await get_provider_metrics_store().history(provider, limit=min(max(limit, 1), 200))
        return {"success": True, "data": rows}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)[:300]) from exc


@router.get("/config")
async def provider_config() -> Dict[str, Any]:
    """Routing configuration with all secrets redacted."""
    r = _get_router()
    return {"success": True, "data": r.config_snapshot()}


@router.post("/test")
async def provider_test(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Route one benign test call through the router (failover included).

    Optional body: {"message": "...", "model": "..."}. Uses the router's
    configured priority chain — provider-test exercises the same path agents
    use. Fails with 503 when no provider is configured/available.
    """
    payload = payload or {}
    message = payload.get("message") or "Reply with exactly: provider-ok"
    config = LLMConfig(temperature=0.0, max_tokens=32)
    r = _get_router()
    try:
        response = await r.chat(
            [LLMMessage(role="user", content=message)],
            config=config,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)[:300]) from exc
    return {
        "success": True,
        "data": {
            "provider": r.active_provider,
            "content": response.content[:200],
            "finish_reason": response.finish_reason,
        },
    }
