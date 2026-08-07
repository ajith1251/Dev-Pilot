"""
Health-check endpoints.

Used by monitoring, load balancers, and the frontend to verify
the backend is running.

Phase 20B adds the liveness / readiness split:

    GET /health       — detailed status (backwards compatible)
    GET /health/live  — liveness: the process is up (always 200)
    GET /health/ready — readiness: required subsystems are healthy (200/503)
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app import __version__
from app.config import settings
from app.db.database import check_database_connection
from app.models.base import Response

router = APIRouter(tags=["health"])


@router.get("/health", response_model=Response)
async def health_check(request: Request) -> Response:
    """Return basic health information about the service."""
    data = {
        "app": settings.APP_NAME,
        "version": __version__,
        "status": "healthy",
        "debug": settings.is_debug,
        "llm_provider": settings.LLM_PROVIDER,
    }

    # Database status (sanitized — never expose credentials)
    db_configured = settings.DATABASE_URL is not None
    data["database"] = {
        "type": "postgresql" if db_configured else "none",
        "configured": db_configured,
    }

    if db_configured:
        engine = getattr(request.app.state, "db_engine", None)
        check = await check_database_connection(engine=engine, database_url=settings.DATABASE_URL)
        data["database"]["connected"] = check.connected
        data["database"]["database"] = check.database_name
        data["database"]["server_version"] = check.server_version if check.connected else ""
        if check.error:
            data["database"]["error"] = check.error[:100]

    return Response(
        success=True,
        data=data,
        message="Service is running",
    )


@router.get("/health/live")
async def health_live() -> JSONResponse:
    """Liveness probe — the process is up and serving requests.

    Always 200 while the process runs; load balancers use this to decide
    whether to keep the instance in rotation (never fails on dependencies).
    """
    return JSONResponse(
        status_code=200,
        content={
            "success": True,
            "status": "ok",
            "app": settings.APP_NAME,
            "version": __version__,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


@router.get("/health/ready")
async def health_ready(request: Request) -> JSONResponse:
    """Readiness probe — required subsystems are healthy.

    Reads the same subsystem matrix as ``GET /api/v1/operations/status``.
    Returns 200 when no required subsystem is in an error state, 503 with
    the failing subsystems otherwise. Optional subsystems (graph, memory,
    websocket) report ``unknown`` and never fail readiness.
    """
    from app.services.subsystem_status import build_subsystem_status

    payload = await build_subsystem_status()
    summary = payload["summary"]
    ready = bool(summary.get("ready"))
    return JSONResponse(
        status_code=200 if ready else 503,
        content={
            "success": ready,
            "status": "ok" if ready else "not_ready",
            "ready": ready,
            "error_subsystems": summary.get("error_subsystems", {}),
            "subsystems": {
                name: {"status": sub["status"]}
                for name, sub in payload["subsystems"].items()
            },
            "checked_at": datetime.now(timezone.utc).isoformat(),
        },
    )
