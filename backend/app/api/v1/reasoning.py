"""
Phase 17 reasoning API — evidence consensus, contradictions, and the
shared engineering notebook for observability.

GET /api/v1/runs/{run_id}/consensus          — evidence consensus records
GET /api/v1/runs/{run_id}/contradictions     — detected contradictions
GET /api/v1/runs/{run_id}/notebook           — shared engineering notebook
GET /api/v1/runs/{run_id}/reasoning          — combined reasoning snapshot

Security invariant: only evidence, confidence, decisions and consensus are
exposed — never chain-of-thought, hidden prompts, or internal reasoning.
Responses are bounded and evidence-only.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Query

from app.models.base import Response

router = APIRouter(prefix="/api/v1/runs", tags=["reasoning"])

# Lazy singleton (gracefully degrades like other Phase 13-16 services)
_service: Optional[Any] = None


def _get_service() -> Any:
    global _service
    if _service is None:
        from app.services.reasoning_service import CollaborativeReasoningEngine

        _service = CollaborativeReasoningEngine()
    return _service


def _consensus_to_api(consensus: Any) -> Dict[str, Any]:
    return {
        "consensus_id": consensus.consensus_id,
        "topic": consensus.topic,
        "summary": consensus.summary[:200],
        "status": consensus.status.value,
        "confidence": consensus.confidence.summary(),
        "supporting_evidence": [
            {
                "type": e.type.value,
                "reference": e.reference[:100],
                "confidence": round(float(e.confidence), 2),
            }
            for e in consensus.supporting_evidence[:5]
        ],
        "conflicting_evidence": [
            {
                "type": e.type.value,
                "reference": e.reference[:100],
                "confidence": round(float(e.confidence), 2),
            }
            for e in consensus.conflicting_evidence[:5]
        ],
        "final_decision": consensus.final_decision[:200],
        "contributing_agents": consensus.contributing_agents[:10],
        "created_at": consensus.created_at,
    }


def _contradiction_to_api(c: Any) -> Dict[str, Any]:
    return {
        "contradiction_id": c.contradiction_id,
        "kind": c.kind.value,
        "description": c.description[:200],
        "claim_evidence": {
            "type": c.claim_evidence.type.value,
            "reference": c.claim_evidence.reference[:100],
            "detail": c.claim_evidence.detail[:100],
        },
        "deterministic_evidence": (
            {
                "type": c.deterministic_evidence.type.value,
                "reference": c.deterministic_evidence.reference[:100],
                "detail": c.deterministic_evidence.detail[:100],
            }
            if c.deterministic_evidence else None
        ),
        "resolution": c.resolution,
        "created_at": c.created_at,
    }


def _notebook_to_api(notebook: Any) -> Dict[str, Any]:
    if notebook is None:
        return {"notebook_id": None, "run_id": None, "exists": False}
    return {
        "notebook_id": notebook.notebook_id,
        "run_id": notebook.run_id,
        "task": notebook.task[:200],
        "accepted_decisions": notebook.accepted_decisions[:20],
        "rejected_decisions": notebook.rejected_decisions[:20],
        "conflicts": [_contradiction_to_api(c) for c in notebook.conflicts[:20]],
        "resolved_conflicts": [
            _contradiction_to_api(c) for c in notebook.resolved_conflicts[:20]
        ],
        "consensus": [_consensus_to_api(c) for c in notebook.consensus[:20]],
        "timeline": [
            {
                "entry_id": t.entry_id,
                "entry_type": t.entry_type.value,
                "label": t.label[:100],
                "detail": t.detail[:200],
                "created_at": t.created_at,
            }
            for t in notebook.timeline[:50]
        ],
        "version": notebook.version,
        "updated_at": notebook.updated_at,
    }


@router.get("/{run_id}/consensus", response_model=Response)
async def list_consensus(
    run_id: str,
    limit: int = Query(20, ge=1, le=50),
) -> Response:
    """List evidence consensus records for a run (evidence-only)."""
    try:
        consensus = await _get_service().list_consensus(run_id, limit=limit)
        return Response(
            success=True,
            data=[_consensus_to_api(c) for c in consensus],
            message=f"{len(consensus)} consensus record(s)",
        )
    except Exception as exc:
        return Response(success=False, error="ReasoningError", message=str(exc))


@router.get("/{run_id}/contradictions", response_model=Response)
async def list_contradictions(
    run_id: str,
    limit: int = Query(20, ge=1, le=50),
) -> Response:
    """List detected contradictions for a run."""
    try:
        contradictions = await _get_service().list_contradictions(run_id, limit=limit)
        return Response(
            success=True,
            data=[_contradiction_to_api(c) for c in contradictions],
            message=f"{len(contradictions)} contradiction(s)",
        )
    except Exception as exc:
        return Response(success=False, error="ReasoningError", message=str(exc))


@router.get("/{run_id}/notebook", response_model=Response)
async def get_notebook(run_id: str) -> Response:
    """Get the shared engineering notebook for a run."""
    try:
        notebook = await _get_service().get_notebook(run_id)
        if notebook is None:
            return Response(
                success=False,
                error="NotFound",
                message=f"Engineering notebook not found for {run_id}",
            )
        return Response(success=True, data=_notebook_to_api(notebook))
    except Exception as exc:
        return Response(success=False, error="ReasoningError", message=str(exc))


@router.get("/{run_id}/reasoning", response_model=Response)
async def get_reasoning(run_id: str) -> Response:
    """Combined reasoning snapshot for a run."""
    try:
        svc = _get_service()
        await svc.recover(run_id)
        consensus = await svc.list_consensus(run_id, limit=50)
        contradictions = await svc.list_contradictions(run_id, limit=50)
        notebook = await svc.get_notebook(run_id)
        return Response(
            success=True,
            data={
                "run_id": run_id,
                "consensus": [_consensus_to_api(c) for c in consensus],
                "contradictions": [
                    _contradiction_to_api(c) for c in contradictions
                ],
                "notebook": _notebook_to_api(notebook),
            },
            message=f"Reasoning snapshot for {run_id}",
        )
    except Exception as exc:
        return Response(success=False, error="ReasoningError", message=str(exc))
