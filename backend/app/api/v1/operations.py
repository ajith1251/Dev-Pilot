"""
Operations API — Phase 20B operational observability.

Endpoints (all responses are secret-safe):

    GET /api/v1/operations/status             — subsystem health matrix + readiness
    GET /api/v1/operations/metrics            — runtime operational metrics
    GET /api/v1/operations/startup-validation — startup configuration findings
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, Request

from app.core.startup_validation import validate_settings

router = APIRouter(prefix="/api/v1/operations", tags=["operations"])


@router.get("/status")
async def operations_status(request: Request) -> Dict[str, Any]:
    """Subsystem status matrix + readiness summary."""
    from app.services.subsystem_status import build_subsystem_status

    payload = await build_subsystem_status()
    payload["summary"]["checked_at"] = datetime.now(timezone.utc).isoformat()
    return {"success": True, "data": payload}


@router.get("/metrics")
async def operations_metrics() -> Dict[str, Any]:
    """Runtime operational metrics (runs, repositories, autonomy, resources)."""
    from app.services.system_metrics import get_system_metrics

    snapshot = get_system_metrics().snapshot()
    snapshot["checked_at"] = datetime.now(timezone.utc).isoformat()
    return {"success": True, "data": snapshot}


@router.get("/startup-validation")
async def operations_startup_validation(request: Request) -> Dict[str, Any]:
    """Startup configuration findings (fresh validation each call)."""
    findings = validate_settings()
    errors = [f for f in findings if f["severity"] == "error"]
    warnings = [f for f in findings if f["severity"] == "warning"]
    return {
        "success": True,
        "data": {
            "strict": bool(getattr(
                request.app.state, "startup_validation_strict", False)),
            "error_count": len(errors),
            "warning_count": len(warnings),
            "findings": findings,
        },
    }
