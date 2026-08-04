"""
Phase 16 autonomy API — start, dry-run, status, progress, decisions,
pause, resume, cancel, and human-input endpoints.

GET  /api/v1/autonomy/dry-run?task=...&repository=...
POST /api/v1/autonomy/run                     {task, repository, criteria, budget, policy}
GET  /api/v1/autonomy/{goal_id}
GET  /api/v1/autonomy/{goal_id}/progress
GET  /api/v1/autonomy/{goal_id}/decisions
POST /api/v1/autonomy/{goal_id}/pause
POST /api/v1/autonomy/{goal_id}/resume
POST /api/v1/autonomy/{goal_id}/cancel
POST /api/v1/autonomy/{goal_id}/input         {clarification}
GET  /api/v1/autonomy                        {limit} — goal list + escalation queue

Run state is validated before every mutation. Responses are bounded and
expose decisions + short rationale + evidence only — never chain-of-thought.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Query

from app.models.base import Response

router = APIRouter(prefix="/api/v1/autonomy", tags=["autonomy"])

# Lazy singleton (gracefully degrades like other Phase 13-15 services)
_service: Optional[Any] = None


def _get_service() -> Any:
    global _service
    if _service is None:
        from app.services.autonomy_service import AutonomousExecutionController

        _service = AutonomousExecutionController()
    return _service


def _goal_to_api(state: Any) -> Dict[str, Any]:
    """Bounded public status — decision, rationale, evidence, progress."""
    summary = state.status_summary()
    return summary


def _decision_to_api(decision: Any) -> Dict[str, Any]:
    return {
        "decision_id": decision.decision_id,
        "iteration": decision.iteration,
        "action": decision.action.value,
        "reason_code": decision.reason_code[:100],
        "rationale": decision.rationale[:200],
        "evidence_refs": [
            {
                "type": e.type.value,
                "reference": e.reference[:100],
                "confidence": round(float(e.confidence), 2),
            }
            for e in decision.evidence_refs[:5]
        ],
        "timestamp": decision.timestamp,
    }


@router.get("", response_model=Response)
async def list_goals(
    limit: int = Query(50, ge=1, le=200),
    state: Optional[str] = Query(
        None,
        description=(
            "Optional ExecutionState filter: running, paused, "
            "waiting_for_human, completed, stopped, failed, cancelled"
        ),
    ),
) -> Response:
    """List known autonomous goals and the open human-escalation queue."""
    try:
        goals = await _get_service().list_goals(limit=limit, state=state)
        queue = [g for g in goals if g.get("open_escalations")]
        return Response(
            success=True,
            data={"goals": goals, "escalation_queue": queue},
            message=f"{len(goals)} goal(s), {len(queue)} open escalation(s)",
        )
    except Exception as exc:
        return Response(success=False, error="AutonomyError", message=str(exc)[:500])


@router.post("/run", response_model=Response)
async def start_run(
    payload: Dict[str, Any] = Body(...),
) -> Response:
    """Create a goal and run the bounded autonomous loop."""
    try:
        svc = _get_service()
        state = await svc.create_goal(
            task=payload.get("task", ""),
            repository=payload.get("repository"),
            criteria_texts=payload.get("criteria"),
            constraints=payload.get("constraints"),
            budget=_budget_from(payload.get("budget")),
            policy=_policy_from(payload.get("policy")),
        )
        await svc.start(state.goal_id)
        return Response(success=True, data=_goal_to_api(state),
                        message=f"Autonomous run {state.goal_id}")
    except Exception as exc:
        return Response(success=False, error="AutonomyError", message=str(exc)[:500])


@router.get("/dry-run", response_model=Response)
async def dry_run(
    task: str = Query(..., description="Task / goal"),
    repository: Optional[str] = Query(None, description="Target repository"),
) -> Response:
    """Estimate scope, budget and workflow without any mutations."""
    try:
        report = await _get_service().dry_run(task=task, repository=repository)
        return Response(success=True, data=report.summary(),
                        message="Dry-run estimate (no mutations performed)")
    except Exception as exc:
        return Response(success=False, error="AutonomyError", message=str(exc)[:500])


@router.get("/{goal_id}", response_model=Response)
async def get_status(goal_id: str) -> Response:
    """Full autonomous run status (goal, criteria, budget, decisions)."""
    try:
        state = await _get_service().get_status(goal_id)
        return Response(success=True, data=_goal_to_api(state))
    except KeyError:
        return Response(success=False, error="NotFound", message=f"Goal {goal_id} not found")
    except Exception as exc:
        return Response(success=False, error="AutonomyError", message=str(exc)[:500])


@router.get("/{goal_id}/progress", response_model=Response)
async def get_progress(goal_id: str) -> Response:
    """Criteria progress snapshot."""
    try:
        progress = await _get_service().get_progress(goal_id)
        return Response(success=True, data=progress.model_dump())
    except KeyError:
        return Response(success=False, error="NotFound", message=f"Goal {goal_id} not found")
    except Exception as exc:
        return Response(success=False, error="AutonomyError", message=str(exc)[:500])


@router.get("/{goal_id}/decisions", response_model=Response)
async def get_decisions(
    goal_id: str,
    limit: int = Query(20, ge=1, le=100),
) -> Response:
    """Recorded autonomous decisions (bounded)."""
    try:
        decisions = await _get_service().get_decisions(goal_id)
        return Response(success=True, data=[_decision_to_api(d) for d in decisions[-limit:]])
    except KeyError:
        return Response(success=False, error="NotFound", message=f"Goal {goal_id} not found")
    except Exception as exc:
        return Response(success=False, error="AutonomyError", message=str(exc)[:500])


@router.post("/{goal_id}/pause", response_model=Response)
async def pause(goal_id: str) -> Response:
    """Pause the autonomous run between operations."""
    try:
        state = await _get_service().pause(goal_id)
        return Response(success=True, data={"goal_id": goal_id, "state": state.state.value})
    except KeyError:
        return Response(success=False, error="NotFound", message=f"Goal {goal_id} not found")
    except Exception as exc:
        return Response(success=False, error="AutonomyError", message=str(exc)[:500])


@router.post("/{goal_id}/resume", response_model=Response)
async def resume(goal_id: str) -> Response:
    """Resume a paused autonomous run."""
    try:
        state = await _get_service().resume(goal_id)
        return Response(success=True, data={"goal_id": goal_id, "state": state.state.value})
    except KeyError:
        return Response(success=False, error="NotFound", message=f"Goal {goal_id} not found")
    except Exception as exc:
        return Response(success=False, error="AutonomyError", message=str(exc)[:500])


@router.post("/{goal_id}/cancel", response_model=Response)
async def cancel(goal_id: str) -> Response:
    """Cancel the autonomous run (authoritative; no further agent calls)."""
    try:
        state = await _get_service().cancel(goal_id)
        return Response(success=True, data={"goal_id": goal_id, "state": state.state.value})
    except KeyError:
        return Response(success=False, error="NotFound", message=f"Goal {goal_id} not found")
    except Exception as exc:
        return Response(success=False, error="AutonomyError", message=str(exc)[:500])


@router.post("/{goal_id}/input", response_model=Response)
async def provide_input(
    goal_id: str,
    payload: Dict[str, Any] = Body(...),
) -> Response:
    """Resolve a human escalation with clarification."""
    try:
        clarification = payload.get("clarification", "")
        state = await _get_service().provide_input(goal_id, clarification)
        return Response(success=True, data={"goal_id": goal_id, "state": state.state.value},
                        message="Human input recorded")
    except KeyError:
        return Response(success=False, error="NotFound", message=f"Goal {goal_id} not found")
    except Exception as exc:
        return Response(success=False, error="AutonomyError", message=str(exc)[:500])


# ── Payload parsing helpers (bounded) ───────────────────────────


def _budget_from(data: Optional[Dict[str, Any]]):
    if not data:
        return None
    from app.models.autonomy import ExecutionBudget

    allowed = {
        "max_iterations", "max_replans", "max_repairs", "max_agent_calls",
        "max_llm_calls", "max_files_changed", "max_test_runs",
        "max_execution_time_seconds",
    }
    return ExecutionBudget(**{k: int(v) for k, v in data.items() if k in allowed})


def _policy_from(data: Optional[Dict[str, Any]]):
    if not data:
        return None
    from app.models.autonomy import AutonomyPolicy

    allowed = {
        "allow_repair", "allow_replan", "allow_test_execution",
        "allow_scope_expansion", "allow_human_escalation",
        "max_scope_expansions",
    }

    def _as_bool(value: Any) -> bool:
        # Normalize JSON booleans AND string forms so "false" never becomes
        # True (bool("false") is True).
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)

    return AutonomyPolicy(**{
        k: (_as_bool(v) if k != "max_scope_expansions" else int(v))
        for k, v in data.items() if k in allowed
    })
