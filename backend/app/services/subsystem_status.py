"""
Subsystem Status — Phase 20B health/readiness matrix.

Builds a single status snapshot of every operational subsystem so the
readiness endpoint and the Operations Dashboard report the same truth:

- providers     (router health, circuit state, configured providers)
- database      (PostgreSQL connectivity, redacted)
- graph         (Engineering Knowledge Graph availability + version)
- repository_memory (memory service availability)
- inference     (routing enabled, active provider, configured count)
- orchestration (run throughput / active runs)
- websocket     (active connections per channel)
- resources     (process RSS, open asyncio tasks)

Each subsystem carries ``status`` in {ok, degraded, error, unknown} plus a
secret-safe ``detail``. ``ready`` is True when no *required* subsystem is in
an error state (database when configured, providers when routing is enabled).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.config import settings
from app.core.logging import logger


async def _database_status() -> Dict[str, Any]:
    if not settings.DATABASE_URL:
        return {
            "status": "unknown",
            "detail": {"configured": False, "message": "PostgreSQL not configured"},
        }
    from app.db.database import check_database_connection

    check = await check_database_connection(database_url=settings.DATABASE_URL)
    return {
        "status": "ok" if check.connected else "error",
        "detail": {
            "configured": True,
            "connected": check.connected,
            "database": check.database_name,
            "server_version": check.server_version,
            "error": check.error[:200] if check.error else None,
        },
    }


def _providers_status() -> Dict[str, Any]:
    try:
        from app.llm.router import get_router

        r = get_router()
        snap = r.health_snapshot()
        configured = [p for p in snap["providers"] if p["configured"] and p.get("enabled")]
        healthy = [p for p in configured if p["status"] in (
            "healthy", "degraded", "warming", "unknown")]
        return {
            "status": (
                "unknown" if not snap["routing_enabled"] else
                "ok" if healthy else
                "degraded" if configured else
                "error"
            ),
            "detail": {
                "routing_enabled": snap["routing_enabled"],
                "active_provider": snap["active_provider"],
                "configured_count": len(configured),
                "healthy_count": len(healthy),
                "providers": [
                    {
                        "name": p["name"],
                        "status": p["status"],
                        "circuit_state": p["circuit_state"],
                        "configured": p["configured"],
                        "enabled": p["enabled"],
                    }
                    for p in snap["providers"]
                ],
            },
        }
    except Exception as exc:
        logger.debug("Provider subsystem status unavailable: %s", exc)
        return {"status": "unknown", "detail": {"error": str(exc)[:200]}}


def _graph_status() -> Dict[str, Any]:
    try:
        from app.api.v1.engineering_graph import _get_service

        svc = _get_service()
        stats = svc.stats().summary()
        return {
            "status": "ok",
            "detail": {
                "available": True,
                "version": stats.get("version"),
                "node_count": stats.get("active_node_count") or stats.get("node_count"),
                "edge_count": stats.get("active_edge_count") or stats.get("edge_count"),
            },
        }
    except Exception as exc:
        logger.debug("Graph subsystem status unavailable: %s", exc)
        return {"status": "unknown", "detail": {"available": False, "error": str(exc)[:200]}}


def _repository_memory_status() -> Dict[str, Any]:
    try:
        from app.services.repository_memory_service import RepositoryMemoryService

        svc = RepositoryMemoryService()
        return {"status": "ok", "detail": {"available": True, "type": type(svc).__name__}}
    except Exception as exc:
        logger.debug("Repository memory subsystem status unavailable: %s", exc)
        return {"status": "unknown", "detail": {"available": False, "error": str(exc)[:200]}}


def _inference_status() -> Dict[str, Any]:
    try:
        from app.llm.router import get_router

        r = get_router()
        return {
            "status": (
                "unknown" if not r._settings.PROVIDER_ROUTING_ENABLED
                else "ok" if r.active_provider else "degraded"
            ),
            "detail": {
                "routing_enabled": bool(r._settings.PROVIDER_ROUTING_ENABLED),
                "active_provider": r.active_provider,
                "priority": r._priority(),
            },
        }
    except Exception as exc:
        return {"status": "unknown", "detail": {"error": str(exc)[:200]}}


def _orchestration_status() -> Dict[str, Any]:
    try:
        from app.services.system_metrics import get_system_metrics

        m = get_system_metrics()
        return {
            "status": "ok",
            "detail": {
                "active_runs": m.active_runs(),
                "completed_total": m.run_completed_total,
                "throughput_per_minute": m.run_throughput_per_minute(),
            },
        }
    except Exception as exc:
        return {"status": "unknown", "detail": {"error": str(exc)[:200]}}


def _websocket_status() -> Dict[str, Any]:
    try:
        from app.services.ws_manager import ws_manager

        return {
            "status": "ok",
            "detail": {
                "active_connections": ws_manager.active_connections,
                "channels": ws_manager.channel_counts(),
            },
        }
    except Exception as exc:
        return {"status": "unknown", "detail": {"error": str(exc)[:200]}}


def _resources_status() -> Dict[str, Any]:
    from app.services.system_metrics import SystemMetricsService

    return {
        "status": "ok",
        "detail": {
            "memory_mb": SystemMetricsService.memory_usage_mb(),
            "open_tasks": SystemMetricsService.open_task_count(),
        },
    }


async def build_subsystem_status() -> Dict[str, Any]:
    """Build the full subsystem status matrix + readiness summary."""
    subsystems: Dict[str, Dict[str, Any]] = {}
    tasks: Dict[str, Any] = {}

    async def _run(name: str, value) -> None:
        """Evaluate a subsystem check — accepts a coroutine or a plain value."""
        try:
            import asyncio

            tasks[name] = await value if asyncio.iscoroutine(value) else value
        except Exception as exc:
            logger.debug("Subsystem %s check failed: %s", name, exc)
            tasks[name] = {"status": "error", "detail": {"error": str(exc)[:200]}}

    await _run("providers", _providers_status())
    await _run("database", _database_status())
    await _run("graph", _graph_status())
    await _run("repository_memory", _repository_memory_status())
    await _run("inference", _inference_status())
    await _run("orchestration", _orchestration_status())
    await _run("websocket", _websocket_status())
    await _run("resources", _resources_status())

    for name, result in tasks.items():
        subsystems[name] = result

    # Readiness: no REQUIRED subsystem may be in an error state.
    errors = {
        name: sub["status"]
        for name, sub in subsystems.items()
        if sub["status"] == "error"
    }
    ready = not errors
    return {
        "summary": {
            "ready": ready,
            "status": "ok" if ready else "error",
            "error_subsystems": errors,
            "checked_at": None,  # filled by the API layer with ISO timestamp
        },
        "subsystems": subsystems,
    }
