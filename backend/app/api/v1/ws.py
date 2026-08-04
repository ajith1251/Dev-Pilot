"""
WebSocket API — real-time run status updates.

Endpoints:
    WS /api/v1/ws/runs/{run_id}  — Subscribe to a specific run's updates
    WS /api/v1/ws/runs           — Subscribe to the global run list

Protocol:
    Server → Client (JSON text frames):

    Run update:
    {
        "type": "run_update",
        "run_id": "RUN-...",
        "timestamp": "2026-07-30T...",
        "data": { ... run data ... }
    }

    Event notification:
    {
        "type": "event",
        "run_id": "RUN-...",
        "event_type": "stage_completed",
        "timestamp": "2026-07-30T...",
        "message": "...",
        "metadata": { ... }
    }

    Run list update:
    {
        "type": "run_list_update",
        "timestamp": "2026-07-30T...",
        "data": [ ... run summaries ... ]
    }

    Client → Server:
    Pong (connection keepalive response):
    {
        "type": "pong"
    }
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.logging import logger
from app.services.ws_manager import ws_manager
from app.workflows.orchestration import OrchestrationWorkflow

router = APIRouter(prefix="/api/v1/ws", tags=["websocket"])

workflow = OrchestrationWorkflow()

# Cached _sanitize_run import to avoid circular dependency at module level
_sanitize_run = None


def _get_sanitize_run():
    global _sanitize_run
    if _sanitize_run is None:
        from app.api.v1.orchestration import _sanitize_run as sr
        _sanitize_run = sr
    return _sanitize_run


# Cached autonomy service accessor (same lazy pattern)
_autonomy_getter = None


def _get_autonomy_service():
    global _autonomy_getter
    if _autonomy_getter is None:
        from app.api.v1.autonomy import _get_service as gs
        _autonomy_getter = gs
    return _autonomy_getter()


# Autonomy channels (mirror of ws_manager keys)
AUTONOMY_GLOBAL = "__autonomy__"


def _autonomy_key(goal_id: str) -> str:
    return f"{AUTONOMY_GLOBAL}:{goal_id}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.websocket("/graph")
async def graph_websocket(websocket: WebSocket) -> None:
    """Subscribe to live engineering-graph updates.

    On connect, sends the current graph version + stats snapshot.
    Then receives broadcast updates whenever the graph version increments
    (new nodes, relationship changes, superseded nodes) without a page
    refresh.
    """
    await ws_manager.connect(websocket, "__graph__")

    try:
        from app.api.v1.engineering_graph import _get_service

        svc = _get_service()
        stats = svc.stats().summary()
        await websocket.send_json({
            "type": "graph_update",
            "event_type": "snapshot",
            "timestamp": _now(),
            "message": "Graph feed connected",
            "data": {"version": stats["version"], "stats": stats},
        })
    except Exception as exc:
        logger.warning("Failed to send initial graph snapshot: %s", exc)

    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("type") == "pong":
                    pass  # Connection keepalive
            except (json.JSONDecodeError, KeyError):
                pass
    except WebSocketDisconnect:
        logger.debug("WebSocket disconnected from graph feed")
    except Exception as exc:
        logger.warning("WebSocket error on graph feed: %s", exc)
    finally:
        await ws_manager.disconnect(websocket, "__graph__")


@router.websocket("/runs/{run_id}")
async def run_websocket(websocket: WebSocket, run_id: str) -> None:
    """Subscribe to real-time updates for a specific run.

    On connect, sends the current run state immediately.
    Then broadcasts all subsequent state changes in real-time.
    """
    await ws_manager.connect(websocket, run_id)

    # Send the current run state on connect
    try:
        run = await workflow.get_run(run_id)
        if run:
            sanitize = _get_sanitize_run()
            initial_data = sanitize(run)
            await websocket.send_json({
                "type": "run_update",
                "run_id": run_id,
                "timestamp": _now(),
                "data": initial_data,
            })
        else:
            await websocket.send_json({
                "type": "error",
                "run_id": run_id,
                "message": f"Run {run_id} not found",
            })
    except Exception as exc:
        logger.warning("Failed to send initial state for run %s: %s", run_id, exc)

    # Listen for client messages (e.g. pong keepalive)
    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("type") == "pong":
                    pass  # Connection keepalive — no action needed
            except (json.JSONDecodeError, KeyError):
                pass
    except WebSocketDisconnect:
        logger.debug("WebSocket disconnected for run %s", run_id)
    except Exception as exc:
        logger.warning("WebSocket error for run %s: %s", run_id, exc)
    finally:
        await ws_manager.disconnect(websocket, run_id)


@router.websocket("/runs")
async def run_list_websocket(websocket: WebSocket) -> None:
    """Subscribe to real-time run list updates.

    On connect, sends the current run list.
    Then receives broadcast updates when any run changes state.
    """
    await ws_manager.connect(websocket, "__list__")

    # Send current run list on connect
    try:
        runs = await workflow.list_runs(limit=50)
        runs_data = [
            {
                "run_id": r.run_id,
                "status": r.status.value,
                "source": r.source.source_type.value,
                "title": r.source.title[:200],
                "current_stage": r.current_stage.value,
                "created_at": r.created_at,
                "total_duration_ms": r.total_duration_ms,
            }
            for r in runs
        ]
        await websocket.send_json({
            "type": "run_list_update",
            "timestamp": _now(),
            "data": runs_data,
        })
    except Exception as exc:
        logger.warning("Failed to send initial run list: %s", exc)

    # Listen for client messages
    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("type") == "pong":
                    pass  # Connection keepalive
            except (json.JSONDecodeError, KeyError):
                pass
    except WebSocketDisconnect:
        logger.debug("WebSocket disconnected for run list")
    except Exception as exc:
        logger.warning("WebSocket error for run list: %s", exc)
    finally:
        await ws_manager.disconnect(websocket, "__list__")


@router.websocket("/autonomy")
async def autonomy_websocket(websocket: WebSocket) -> None:
    """Subscribe to the global autonomy feed (all goals).

    On connect, sends a snapshot of known goals + the open escalation queue.
    Then receives live autonomy events (status / decision / escalation)
    broadcast by the AutonomousExecutionController.
    """
    await ws_manager.connect(websocket, AUTONOMY_GLOBAL)

    try:
        svc = _get_autonomy_service()
        goals = await svc.list_goals(limit=50)
        await websocket.send_json({
            "type": "autonomy_event",
            "goal_id": None,
            "event_type": "snapshot",
            "timestamp": _now(),
            "message": "Autonomy feed connected",
            "data": {"goals": goals},
        })
    except Exception as exc:
        logger.warning("Failed to send autonomy snapshot: %s", exc)

    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("type") == "pong":
                    pass  # Connection keepalive
            except (json.JSONDecodeError, KeyError):
                pass
    except WebSocketDisconnect:
        logger.debug("WebSocket disconnected from autonomy feed")
    except Exception as exc:
        logger.warning("WebSocket error on autonomy feed: %s", exc)
    finally:
        await ws_manager.disconnect(websocket, AUTONOMY_GLOBAL)


@router.websocket("/autonomy/{goal_id}")
async def autonomy_goal_websocket(websocket: WebSocket, goal_id: str) -> None:
    """Subscribe to live updates for a single autonomous goal.

    On connect, sends the goal's current status. Then receives live events
    for that goal only.
    """
    await ws_manager.connect(websocket, _autonomy_key(goal_id))

    try:
        svc = _get_autonomy_service()
        state = await svc.get_status(goal_id)
        await websocket.send_json({
            "type": "autonomy_event",
            "goal_id": goal_id,
            "event_type": "status",
            "timestamp": _now(),
            "message": "Goal feed connected",
            "data": {"status": state.status_summary()},
        })
    except KeyError:
        await websocket.send_json({
            "type": "error",
            "goal_id": goal_id,
            "message": f"Goal {goal_id} not found",
        })
    except Exception as exc:
        logger.warning("Failed to send initial autonomy state for %s: %s", goal_id, exc)

    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("type") == "pong":
                    pass  # Connection keepalive
            except (json.JSONDecodeError, KeyError):
                pass
    except WebSocketDisconnect:
        logger.debug("WebSocket disconnected for goal %s", goal_id)
    except Exception as exc:
        logger.warning("WebSocket error for goal %s: %s", goal_id, exc)
    finally:
        await ws_manager.disconnect(websocket, _autonomy_key(goal_id))
