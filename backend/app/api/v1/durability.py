"""
Durability report API — Phase 19.

GET /api/v1/durability/report — serve the latest `scripts/durability_report.py`
JSON output (the `run_api`/`goal_api` summary) so the web dashboard can render
live run + goal verdicts, gate failures, and persisted metrics.

The report file is produced by the `live-llm-e2e` job / manual run:

    python scripts/durability_report.py --out backend/durability_report.json

This endpoint only READS that file — it never runs the live LLM paths itself
(those are expensive and provider-gated). 404 with guidance when no report
has been generated yet.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.core.logging import logger

router = APIRouter(prefix="/api/v1/durability", tags=["durability"])


def _backend_dir() -> Path:
    """The backend project root (parents[3] of app/api/v1/durability.py)."""
    return Path(__file__).resolve().parents[3]


def _default_report_path() -> Path:
    """Default report path: backend/durability_report.json."""
    return _backend_dir() / "durability_report.json"


def resolve_report_path() -> Path:
    """Resolve the configured report path (absolute), or the default."""
    raw = settings.DURABILITY_REPORT_PATH
    if not raw:
        return _default_report_path()
    p = Path(raw)
    if not p.is_absolute():
        p = _backend_dir() / p
    return p


@router.get("/report", response_model=Dict[str, Any])
async def get_durability_report() -> Dict[str, Any]:
    """Return the latest durability report JSON document.

    Wrapped in the codebase-standard ``{"success": true, "data": ...}``
    envelope so the web client's ``request()`` helper works unchanged.

    Shape (from scripts/durability_report.py):

        {
          "mode": "live" | "skipped" | "error",
          "reason": str | omitted,
          "run_api": {run_id, run_status, handoffs, decisions,
                      consensus_via_api, consensus_recovered, runs_in_table},
          "goal_api": {goal_id, goal_state, goal_runs, goal_run_statuses,
                       goal_latest_run_status, goal_handoffs, goal_decisions,
                       goal_consensus, goal_recovered},
          "gates": [str] | omitted,
          "passed": bool | omitted,
          "error": str | omitted
        }
    """
    path = resolve_report_path()
    if not path.is_file():
        logger.info("Durability report not found at %s", path)
        raise HTTPException(
            status_code=404,
            detail=(
                "No durability report found. Generate one with: "
                "cd backend && python scripts/durability_report.py "
                "--out <path> (requires a live LLM provider + test-named "
                "PostgreSQL; skips cleanly without them). Set "
                "DURABILITY_REPORT_PATH to point at the output file."
            ),
        )

    try:
        payload: Dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Durability report unreadable at %s: %s", path, exc)
        raise HTTPException(
            status_code=500,
            detail=f"Durability report at {path} is unreadable: {exc}",
        ) from exc

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=500,
            detail=f"Durability report at {path} is not a JSON object",
        )

    return {"success": True, "data": payload}
