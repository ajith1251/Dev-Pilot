"""
Tests for Phase 16 live autonomy WebSocket updates.

Covers:
- WebSocketManager.broadcast_autonomy fan-out to the global feed and a
  goal's per-goal feed
- broadcast_autonomy with no connections / tolerated send failures
- AutonomousExecutionController emission hooks:
    * create_goal  → "goal_created"
    * _record_decision → "decision" (live timeline)
    * _checkpoint  → "status" (live heartbeat)
    * _escalate    → "escalation" (queue refresh)

Deterministic — no LLM, no live PostgreSQL, no live sockets.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import WebSocket

from app.models.autonomy import (
    AutonomousAction,
    ExecutionState,
    IterationEvidence,
)
from app.services.autonomy_service import AutonomousExecutionController
from app.services.ws_manager import WebSocketManager


# ═════════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════════


def _make_mock_websocket() -> MagicMock:
    ws = MagicMock(spec=WebSocket)
    ws.send_text = AsyncMock()
    return ws


def _evidence(n: int, test_status: str = "passed", tests_failed: int = 0,
              gate: str = "approved", plan: str = "Plan v1") -> IterationEvidence:
    return IterationEvidence(
        iteration=n,
        run_id=f"RUN-{n}",
        test_status=test_status,
        tests_passed=5 if test_status == "passed" else 0,
        tests_failed=tests_failed,
        failing_test_names=[],
        quality_gate_decision=gate,
        plan_summary=plan,
        plan_objective="Fix tokens",
        plan_step_count=1,
    )


def _runner_script(script):
    """Wrap a list-of-evidence as an async iteration runner (matches the
    deterministic pattern used across the autonomy test suite)."""
    calls = {"n": 0}

    async def runner(state, action, reason_code):
        idx = calls["n"]
        calls["n"] += 1
        return script[min(idx, len(script) - 1)]

    return runner, calls


def _mock_ws_manager() -> MagicMock:
    """A WebSocket-manager stand-in that records broadcast_autonomy calls."""
    mgr = MagicMock()
    mgr.active_connections = 1
    mgr.broadcast_autonomy = AsyncMock(return_value=1)
    return mgr


# ═════════════════════════════════════════════════════════════════
# 1 — WebSocketManager.broadcast_autonomy FAN-OUT
# ═════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestBroadcastAutonomy:
    async def test_fans_out_to_global_and_goal_feeds(self):
        """Both the global feed and the per-goal feed receive the event."""
        mgr = WebSocketManager()
        global_ws = _make_mock_websocket()
        goal_ws = _make_mock_websocket()
        other_ws = _make_mock_websocket()

        await mgr.connect(global_ws, "__autonomy__")
        await mgr.connect(goal_ws, "__autonomy__:GOAL-ABC")
        await mgr.connect(other_ws, "RUN-UNRELATED")

        sent = await mgr.broadcast_autonomy(
            "GOAL-ABC", "decision", {"status": {"state": "running"}}, message="Decision"
        )
        assert sent == 2

        payload = json.loads(global_ws.send_text.call_args.args[0])
        assert payload["type"] == "autonomy_event"
        assert payload["goal_id"] == "GOAL-ABC"
        assert payload["event_type"] == "decision"
        assert payload["message"] == "Decision"
        assert payload["data"]["status"]["state"] == "running"
        assert "timestamp" in payload

        # Unrelated run connections must not receive autonomy events.
        other_ws.send_text.assert_not_called()

    async def test_no_connections_returns_zero(self):
        mgr = WebSocketManager()
        sent = await mgr.broadcast_autonomy("GOAL-ABC", "status", {"status": {}})
        assert sent == 0

    async def test_send_failure_is_tolerated(self):
        """A failing client must not break delivery to the others."""
        mgr = WebSocketManager()
        failing = _make_mock_websocket()
        failing.send_text = AsyncMock(side_effect=RuntimeError("socket closed"))
        ok = _make_mock_websocket()

        await mgr.connect(failing, "__autonomy__")
        await mgr.connect(ok, "__autonomy__:GOAL-ABC")

        sent = await mgr.broadcast_autonomy("GOAL-ABC", "status", {"status": {}})
        assert sent == 1
        ok.send_text.assert_called_once()


# ═════════════════════════════════════════════════════════════════
# 2 — CONTROLLER EMISSION HOOKS
# ═════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestControllerBroadcasts:
    async def test_create_goal_emits_goal_created(self):
        mgr = _mock_ws_manager()
        with patch("app.services.autonomy_service._get_ws_manager", return_value=mgr):
            ctrl = AutonomousExecutionController()
            state = await ctrl.create_goal(task="Fix tokens", repository="repo")

        mgr.broadcast_autonomy.assert_called_once()
        args = mgr.broadcast_autonomy.call_args
        assert args.args[0] == state.goal_id
        assert args.args[1] == "goal_created"
        assert args.args[2]["status"]["goal_id"] == state.goal_id

    async def test_loop_emits_decision_and_status(self):
        """A completing run emits per-iteration decisions plus status
        heartbeats — the live timeline + live chip data source."""
        mgr = _mock_ws_manager()
        with patch("app.services.autonomy_service._get_ws_manager", return_value=mgr):
            ctrl = AutonomousExecutionController()
            state = await ctrl.create_goal(task="Fix tokens")
            runner, _ = _runner_script([
                _evidence(1, test_status="failed", tests_failed=1, gate="incomplete"),
                _evidence(2, test_status="passed", gate="approved"),
            ])
            ctrl._iteration_runner = runner
            final = await ctrl.start(state.goal_id)

        assert final.state == ExecutionState.COMPLETED
        events = [c.args[1] for c in mgr.broadcast_autonomy.call_args_list]
        assert "goal_created" in events
        assert "decision" in events
        assert "status" in events

        # Every decision event carries the public decision shape + snapshot.
        for c in mgr.broadcast_autonomy.call_args_list:
            if c.args[1] == "decision":
                decision = c.args[2]["decision"]
                assert decision["decision_id"]
                assert decision["action"]
                assert "timestamp" in decision
                assert c.args[2]["status"]["goal_id"] == final.goal_id

    async def test_escalation_emits_escalation_event(self):
        mgr = _mock_ws_manager()
        with patch("app.services.autonomy_service._get_ws_manager", return_value=mgr):
            ctrl = AutonomousExecutionController()
            state = await ctrl.create_goal(task="Fix")  # too short → ambiguous
            runner, _ = _runner_script([
                _evidence(1, test_status="passed", gate="approved"),
            ])
            ctrl._iteration_runner = runner
            final = await ctrl.start(state.goal_id)

        assert final.state == ExecutionState.WAITING_FOR_HUMAN
        events = [c.args[1] for c in mgr.broadcast_autonomy.call_args_list]
        assert "escalation" in events
        escalation_evt = next(
            c for c in mgr.broadcast_autonomy.call_args_list if c.args[1] == "escalation"
        )
        assert escalation_evt.args[2]["escalation"]["escalation_id"]
        assert escalation_evt.args[2]["escalation"]["reason"] == "ambiguous_requirement"

    async def test_broadcast_never_raises(self):
        """Broadcast failures are swallowed — the autonomy loop is unaffected."""
        mgr = _mock_ws_manager()
        mgr.broadcast_autonomy = AsyncMock(side_effect=RuntimeError("ws down"))
        with patch("app.services.autonomy_service._get_ws_manager", return_value=mgr):
            ctrl = AutonomousExecutionController()
            state = await ctrl.create_goal(task="Fix tokens")
            runner, _ = _runner_script([
                _evidence(1, test_status="passed", gate="approved"),
            ])
            ctrl._iteration_runner = runner
            final = await ctrl.start(state.goal_id)

        assert final.state == ExecutionState.COMPLETED
        assert final.decisions[-1].action == AutonomousAction.COMPLETE


# ═════════════════════════════════════════════════════════════════
# 3 — WS ROUTE SHAPE (route registration + key helpers)
# ═════════════════════════════════════════════════════════════════


class TestRouteRegistration:
    def test_autonomy_routes_registered(self):
        """The ws router exposes the two autonomy WebSocket routes."""
        from app.api.v1.ws import router

        paths = [r.path for r in router.routes]
        assert "/api/v1/ws/autonomy" in paths
        assert "/api/v1/ws/autonomy/{goal_id}" in paths

    def test_autonomy_key_helper(self):
        from app.api.v1.ws import AUTONOMY_GLOBAL, _autonomy_key

        assert AUTONOMY_GLOBAL == "__autonomy__"
        assert _autonomy_key("GOAL-X") == "__autonomy__:GOAL-X"
