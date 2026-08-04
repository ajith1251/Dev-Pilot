"""
Health-check endpoint.

Used by monitoring, load balancers, and the frontend to verify
the backend is running.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

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
