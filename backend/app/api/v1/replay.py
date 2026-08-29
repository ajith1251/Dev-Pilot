"""
Phase 21 replay API — Run Replay & Deterministic Reproduction.

GET    /api/v1/runs/{run_id}/replay/manifest     — build the replay manifest
POST   /api/v1/runs/{run_id}/replay              — execute a replay (mode:
                                                    exact | deterministic |
                                                    compare)
GET    /api/v1/runs/{run_id}/replay/compare/{other_run_id} — compare two runs
GET    /api/v1/runs/{run_id}/replay/audit        — full no-LLM audit report
GET    /api/v1/runs/{run_id}/replay              — replay history for a run

Security invariant: replay NEVER calls an LLM. Responses expose only
evidence, decisions, confidence, consensus and verdicts — never
chain-of-thought. LLMs PROPOSE; deterministic systems DECIDE; replay
re-executes only the deterministic part.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Query

from app.models.base import Response
from app.models.replay import ReplayMode

router = APIRouter(prefix="/api/v1/runs", tags=["replay"])

# Lazy singleton (matches reasoning/collaboration service patterns)
_service: Optional[Any] = None


def _get_service() -> Any:
    global _service
    if _service is None:
        from app.services.replay_service import ReplayService

        _service = ReplayService()
    return _service


def _manifest_to_api(manifest: Any) -> Dict[str, Any]:
    if manifest is None:
        return {"exists": False}
    summary = manifest.summary()
    return {
        "exists": True,
        "manifest_id": summary["manifest_id"],
        "run_id": summary["run_id"],
        "source_run_status": summary["source_run_status"],
        "created_at": summary["created_at"],
        "repository_state": summary["repository_state"],
        "stage_count": summary["stage_count"],
        "stages": [s.summary() for s in manifest.stages],
        "deterministic_decisions": [
            d.summary() for d in manifest.deterministic_decisions
        ],
        "handoffs": manifest.agent_handoffs,
        "reasoning": manifest.reasoning,
        "graph_memory_versions": manifest.graph_memory_versions,
        "content_hash": summary["content_hash"],
        "version": summary["version"],
    }


def _result_to_api(result: Any) -> Dict[str, Any]:
    return {
        **result.summary_dict(),
        "checks": [
            {
                "stage": c.stage,
                "check": c.check,
                "status": c.status.value,
                "expected": c.expected[:200],
                "actual": c.actual[:200],
                "note": c.note[:200],
            }
            for c in result.checks
        ],
        "stage_comparisons": [
            {
                "stage": c.stage,
                "kind": c.kind,
                "recorded_hash": c.recorded_hash,
                "replay_hash": c.replay_hash,
                "matched": c.matched,
                "detail": c.detail[:200],
            }
            for c in result.stage_comparisons
        ],
        "divergences": result.divergences[:20],
    }


@router.get("/{run_id}/replay/manifest", response_model=Response)
async def get_manifest(run_id: str) -> Response:
    """Build (or fetch) the replay manifest for a run. No LLM involved."""
    try:
        svc = _get_service()
        run = await svc.get_run(run_id)
        if run is None:
            return Response(
                success=False,
                error="NotFound",
                message=f"Run {run_id} not found",
            )
        manifest = await svc.build_manifest(run)
        return Response(
            success=True,
            data=_manifest_to_api(manifest),
            message=f"Replay manifest for {run_id}",
        )
    except Exception as exc:
        return Response(success=False, error="ReplayError", message=str(exc)[:300])


@router.post("/{run_id}/replay", response_model=Response)
async def execute_replay(
    run_id: str,
    body: Dict[str, Any] = Body(default={}),
) -> Response:
    """Execute a replay for a run.

    Body:
        mode: "exact" (default) | "deterministic" | "compare"
        workspace: optional workspace path (deterministic mode)
        other_run_id: required for compare mode
    """
    try:
        mode_raw = body.get("mode", "exact")
        try:
            mode = ReplayMode(mode_raw)
        except ValueError:
            return Response(
                success=False,
                error="InvalidMode",
                message=f"mode must be one of exact|deterministic|compare, got {mode_raw!r}",
            )
        result = await _get_service().replay(
            run_id=run_id,
            mode=mode,
            workspace=body.get("workspace"),
            other_run_id=body.get("other_run_id"),
        )
        return Response(
            success=True,
            data=_result_to_api(result),
            message=f"Replay {mode.value} for {run_id}: {result.verdict.value}",
        )
    except Exception as exc:
        return Response(success=False, error="ReplayError", message=str(exc)[:300])


@router.get("/{run_id}/replay/compare/{other_run_id}", response_model=Response)
async def compare_runs(run_id: str, other_run_id: str) -> Response:
    """Compare two runs stage by stage (which stages matched, which decision diverged)."""
    try:
        result = await _get_service().replay(
            run_id=run_id,
            mode=ReplayMode.COMPARE,
            other_run_id=other_run_id,
        )
        return Response(
            success=True,
            data=_result_to_api(result),
            message=f"COMPARE {run_id} vs {other_run_id}: {result.verdict.value}",
        )
    except Exception as exc:
        return Response(success=False, error="ReplayError", message=str(exc)[:300])


@router.get("/{run_id}/replay/audit", response_model=Response)
async def audit_run(run_id: str) -> Response:
    """Full no-LLM audit: manifest + EXACT replay + per-decision outcomes."""
    try:
        audit = await _get_service().audit(run_id)
        if not audit.get("available"):
            return Response(
                success=False,
                error="NotFound",
                message=audit.get("error", f"Run {run_id} not found"),
            )
        return Response(
            success=True,
            data=audit,
            message=f"Audit for {run_id}: {audit['verdict']}",
        )
    except Exception as exc:
        return Response(success=False, error="ReplayError", message=str(exc)[:300])


@router.get("/{run_id}/replay", response_model=Response)
async def list_replays(
    run_id: str,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> Response:
    """Replay execution history for a run."""
    try:
        replays = await _get_service().list_replays(
            run_id=run_id, limit=limit, offset=offset,
        )
        return Response(
            success=True,
            data=[r.summary_dict() for r in replays],
            message=f"{len(replays)} replay record(s) for {run_id}",
        )
    except Exception as exc:
        return Response(success=False, error="ReplayError", message=str(exc)[:300])
