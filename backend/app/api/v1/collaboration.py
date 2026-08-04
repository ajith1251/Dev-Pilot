"""
Phase 15 collaboration API — structured handoffs, decisions, and
shared run summaries for observability.

GET /api/v1/runs/{run_id}/handoffs                 — paginated handoffs
GET /api/v1/runs/{run_id}/handoffs/{handoff_id}    — single handoff
GET /api/v1/runs/{run_id}/decisions                — paginated decisions
GET /api/v1/runs/{run_id}/collaboration            — shared run summary + metrics

Only engineering evidence is exposed — never hidden reasoning or raw
internal prompts. Responses are bounded and paginated.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Query

from app.models.base import Response

router = APIRouter(prefix="/api/v1/runs", tags=["collaboration"])

# Lazy singleton (gracefully degrades like other Phase 13/15 services)
_service: Optional[Any] = None


def _get_service() -> Any:
    global _service
    if _service is None:
        from app.services.collaboration_service import CollaborationService

        _service = CollaborationService()
    return _service


def _handoff_to_api(handoff: Any) -> Dict[str, Any]:
    return {
        "handoff_id": handoff.handoff_id,
        "from_agent": handoff.from_agent,
        "to_agent": handoff.to_agent,
        "stage": handoff.stage,
        "summary": handoff.summary[:200],
        "decisions": handoff.decisions[:5],
        "affected_symbols": handoff.affected_symbols[:10],
        "evidence_refs": [
            {
                "type": e.type.value,
                "reference": e.reference[:100],
                "confidence": round(float(e.confidence), 2),
            }
            for e in handoff.evidence_refs[:5]
        ],
        "artifact_refs": handoff.artifact_refs[:5],
        "warnings": handoff.warnings[:3],
        "open_questions": handoff.open_questions[:3],
        "status": handoff.status.value,
        "created_at": handoff.created_at,
    }


def _decision_to_api(decision: Any) -> Dict[str, Any]:
    return {
        "decision_id": decision.decision_id,
        "decision_type": decision.decision_type.value,
        "statement": decision.statement[:200],
        "made_by": decision.made_by,
        "created_at": decision.created_at,
    }


def _conflict_to_api(conflict: Any) -> Dict[str, Any]:
    return {
        "conflict_id": conflict.conflict_id,
        "description": conflict.description[:200],
        "resolution": conflict.resolution.value,
        "claim_evidence": {
            "type": conflict.claim_evidence.type.value,
            "reference": conflict.claim_evidence.reference[:100],
        },
        "created_at": conflict.created_at,
    }


@router.get("/{run_id}/handoffs", response_model=Response)
async def list_handoffs(
    run_id: str,
    to_agent: Optional[str] = Query(None, description="Filter by recipient agent"),
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0),
) -> Response:
    """List structured handoffs for a run (paginated, oldest first)."""
    try:
        handoffs = await _get_service().list_handoffs(
            run_id=run_id, limit=limit, offset=offset, to_agent=to_agent
        )
        return Response(
            success=True,
            data=[_handoff_to_api(h) for h in handoffs],
            message=f"{len(handoffs)} handoff(s)",
        )
    except Exception as exc:
        return Response(success=False, error="CollaborationError", message=str(exc))


@router.get("/{run_id}/handoffs/{handoff_id}", response_model=Response)
async def get_handoff(run_id: str, handoff_id: str) -> Response:
    """Get a single handoff by ID."""
    try:
        handoff = await _get_service().get_handoff(run_id, handoff_id)
        if handoff is None:
            return Response(success=False, error="NotFound", message=f"Handoff {handoff_id} not found")
        return Response(success=True, data=_handoff_to_api(handoff))
    except Exception as exc:
        return Response(success=False, error="CollaborationError", message=str(exc))


@router.get("/{run_id}/decisions", response_model=Response)
async def list_decisions(
    run_id: str,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> Response:
    """List engineering decision records for a run."""
    try:
        decisions = await _get_service().list_decisions(
            run_id=run_id, limit=limit, offset=offset
        )
        return Response(
            success=True,
            data=[_decision_to_api(d) for d in decisions],
            message=f"{len(decisions)} decision(s)",
        )
    except Exception as exc:
        return Response(success=False, error="CollaborationError", message=str(exc))


@router.get("/{run_id}/collaboration", response_model=Response)
async def get_collaboration(run_id: str) -> Response:
    """Shared run summary + collaboration metrics."""
    try:
        svc = _get_service()
        metrics = await svc.get_collaboration_metrics(run_id)
        handoffs = await svc.list_handoffs(run_id, limit=50)
        decisions = await svc.list_decisions(run_id, limit=100)
        conflicts = await svc.list_conflicts(run_id, limit=50)
        return Response(
            success=True,
            data={
                **metrics,
                "handoffs": [_handoff_to_api(h) for h in handoffs],
                "decisions": [_decision_to_api(d) for d in decisions],
                "conflicts": [_conflict_to_api(c) for c in conflicts],
            },
            message=f"Collaboration summary for {run_id}",
        )
    except Exception as exc:
        return Response(success=False, error="CollaborationError", message=str(exc))
