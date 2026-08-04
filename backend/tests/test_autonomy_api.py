"""
Tests for the Phase 16 autonomy API endpoints — run, dry-run, status,
progress, decisions, pause, resume, cancel, and human input.

Uses a mocked controller singleton so no LLM or live PostgreSQL is needed.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def _fake_status():
    return {
        "goal_id": "GOAL-ABCD1234",
        "task": "Fix token validation",
        "repository": "repo",
        "state": "running",
        "goal": {
            "goal_id": "GOAL-ABCD1234",
            "task": "Fix token validation",
            "status": "running",
            "attempt": 1,
            "replan_count": 0,
            "progress": {
                "criteria_total": 2,
                "criteria_satisfied": 1,
                "criteria_unsatisfied": 1,
                "criteria_unknown": 0,
                "criteria_blocked": 0,
                "iteration": 1,
                "trend": "progressing",
                "improved_last_iteration": False,
                "previous_satisfied": 0,
            },
            "criteria": [
                {
                    "criterion_id": "CR-1",
                    "description": "Expired tokens rejected",
                    "type": "functional",
                    "status": "satisfied",
                    "confidence": 0.9,
                },
                {
                    "criterion_id": "CR-2",
                    "description": "No regression",
                    "type": "functional",
                    "status": "unsatisfied",
                    "confidence": 0.8,
                },
            ],
        },
        "budget": {
            "limits": {"max_iterations": 5, "max_replans": 2, "max_repairs": 3},
            "usage": {"iterations": 1, "replans": 0, "repairs": 0},
        },
        "plan_versions": [],
        "latest_decision": {
            "decision_id": "AD-1",
            "iteration": 1,
            "action": "repair",
            "reason_code": "tests_failing",
            "rationale": "2 test(s) failing — repairing",
            "timestamp": "2026-08-01T00:00:00+00:00",
        },
        "escalations": [],
        "latest_checkpoint": None,
        "scope": {"allowed_modules": [], "expected_change_area": [], "forbidden_areas": []},
        "events": [],
        "version": 1,
    }


class _FakeState:
    """Minimal object exposing the fields the API serializer touches."""

    def __init__(self, state_value: str = "running"):
        self.goal_id = "GOAL-ABCD1234"
        self.task = "Fix token validation"
        self.repository = "repo"
        self.state_value = state_value
        self.decisions = []
        self.escalations = []
        self.checkpoints = []
        self.plan_versions = []
        self.events = []
        self.version = 1
        self.budget = None
        self.goal = None
        self.scope = None
        self._state = type("S", (), {"value": state_value})()

    @property
    def state(self):
        return self._state

    def status_summary(self):
        return _fake_status()


class _FakeService:
    """Mock controller service with the API-facing methods."""

    async def create_goal(self, **kwargs):
        return _FakeState()

    async def start(self, goal_id):
        return _FakeState()

    async def dry_run(self, **kwargs):
        class Report:
            def summary(self):
                return {
                    "task": "Fix tokens",
                    "repository": "repo",
                    "criteria_count": 1,
                    "estimated_scope": {"criteria_count": 1, "repository": "repo"},
                    "estimated_budget": {"max_iterations": 5},
                    "likely_workflow": ["PLAN", "IMPLEMENT", "TEST"],
                    "warnings": [],
                    "feasibility": "ok",
                }

        return Report()

    async def get_status(self, goal_id):
        if goal_id == "GOAL-MISSING":
            raise KeyError("GOAL-MISSING")
        return _FakeState()

    async def get_progress(self, goal_id):
        if goal_id == "GOAL-MISSING":
            raise KeyError("GOAL-MISSING")
        from app.models.autonomy import GoalProgress
        return GoalProgress(criteria_total=2, criteria_satisfied=1)

    async def get_decisions(self, goal_id):
        if goal_id == "GOAL-MISSING":
            raise KeyError("GOAL-MISSING")
        return []

    async def list_goals(self, limit=50, state=None):
        all_goals = [
            {
                "goal_id": "GOAL-ABCD1234",
                "task": "Fix token validation",
                "repository": "repo",
                "state": "running",
                "open_escalations": [],
                "updated_at": None,
            },
            {
                "goal_id": "GOAL-WAITING42",
                "task": "Ambiguous scope",
                "repository": "repo",
                "state": "waiting_for_human",
                "open_escalations": [
                    {
                        "escalation_id": "ESC-1",
                        "reason": "ambiguous_scope",
                        "what_happened": "Scope unclear",
                        "attempted": "Attempted plan",
                        "needed_input": "Clarify scope",
                        "status": "open",
                    }
                ],
                "updated_at": "2026-08-01T00:00:00+00:00",
            },
        ]
        if state is not None:
            all_goals = [g for g in all_goals if g["state"] == state]
        return all_goals[:limit]

    async def pause(self, goal_id):
        return _FakeState("paused")

    async def resume(self, goal_id):
        return _FakeState("running")

    async def cancel(self, goal_id):
        return _FakeState("cancelled")

    async def provide_input(self, goal_id, clarification):
        return _FakeState("resuming")


def _patch_service():
    return patch("app.api.v1.autonomy._get_service", return_value=_FakeService())


class TestAutonomyAPI:
    def test_run_creates_goal_and_starts(self) -> None:
        with _patch_service():
            resp = client.post("/api/v1/autonomy/run",
                               json={"task": "Fix token validation", "repository": "repo"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["goal_id"] == "GOAL-ABCD1234"
        assert body["data"]["state"] == "running"

    def test_dry_run_no_mutations(self) -> None:
        with _patch_service():
            resp = client.get("/api/v1/autonomy/dry-run",
                              params={"task": "Fix tokens", "repository": "repo"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["feasibility"] == "ok"
        assert "PLAN" in body["data"]["likely_workflow"]

    def test_get_status(self) -> None:
        with _patch_service():
            resp = client.get("/api/v1/autonomy/GOAL-ABCD1234")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["goal"]["progress"]["criteria_satisfied"] == 1

    def test_get_status_not_found(self) -> None:
        with _patch_service():
            resp = client.get("/api/v1/autonomy/GOAL-MISSING")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert body["error"] == "NotFound"

    def test_get_progress(self) -> None:
        with _patch_service():
            resp = client.get("/api/v1/autonomy/GOAL-ABCD1234/progress")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["criteria_total"] == 2

    def test_get_decisions(self) -> None:
        with _patch_service():
            resp = client.get("/api/v1/autonomy/GOAL-ABCD1234/decisions")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert isinstance(body["data"], list)

    def test_list_goals(self) -> None:
        with _patch_service():
            resp = client.get("/api/v1/autonomy")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert len(body["data"]["goals"]) == 2
        assert body["data"]["goals"][0]["goal_id"] == "GOAL-ABCD1234"

    def test_list_goals_escalation_queue(self) -> None:
        with _patch_service():
            resp = client.get("/api/v1/autonomy", params={"limit": 10})
        assert resp.status_code == 200
        body = resp.json()
        queue = body["data"]["escalation_queue"]
        assert len(queue) == 1
        assert queue[0]["goal_id"] == "GOAL-WAITING42"
        assert queue[0]["open_escalations"][0]["needed_input"] == "Clarify scope"

    def test_list_goals_state_filter(self) -> None:
        """The ?state= query param must reach the service and filter."""
        with _patch_service():
            resp = client.get("/api/v1/autonomy", params={"state": "waiting_for_human"})
        assert resp.status_code == 200
        body = resp.json()
        goals = body["data"]["goals"]
        assert len(goals) == 1
        assert goals[0]["goal_id"] == "GOAL-WAITING42"

    def test_list_goals_state_filter_empty(self) -> None:
        """A state with no matching goals returns an empty list (not an error)."""
        with _patch_service():
            resp = client.get("/api/v1/autonomy", params={"state": "completed"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["goals"] == []

    def test_run_criteria_and_budget_passthrough(self) -> None:
        """Run payload criteria + budget must reach create_goal unchanged."""
        captured = {}

        class RecordingService(_FakeService):
            async def create_goal(self, **kwargs):
                captured.update(kwargs)
                return _FakeState()

        with patch("app.api.v1.autonomy._get_service",
                   return_value=RecordingService()):
            resp = client.post("/api/v1/autonomy/run", json={
                "task": "Fix tokens",
                "criteria": ["Expired tokens rejected", "No regression"],
                "constraints": ["Do not touch auth/crypto"],
                "budget": {"max_iterations": 4, "max_replans": 1},
            })
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert captured["task"] == "Fix tokens"
        assert captured["criteria_texts"] == [
            "Expired tokens rejected", "No regression"
        ]
        assert captured["constraints"] == ["Do not touch auth/crypto"]
        assert captured["budget"].max_iterations == 4
        assert captured["budget"].max_replans == 1

    def test_pause(self) -> None:
        with _patch_service():
            resp = client.post("/api/v1/autonomy/GOAL-ABCD1234/pause")
        assert resp.status_code == 200
        assert resp.json()["data"]["state"] == "paused"

    def test_resume(self) -> None:
        with _patch_service():
            resp = client.post("/api/v1/autonomy/GOAL-ABCD1234/resume")
        assert resp.status_code == 200
        assert resp.json()["data"]["state"] == "running"

    def test_cancel(self) -> None:
        with _patch_service():
            resp = client.post("/api/v1/autonomy/GOAL-ABCD1234/cancel")
        assert resp.status_code == 200
        assert resp.json()["data"]["state"] == "cancelled"

    def test_provide_input(self) -> None:
        with _patch_service():
            resp = client.post("/api/v1/autonomy/GOAL-ABCD1234/input",
                               json={"clarification": "Accept default criteria"})
        assert resp.status_code == 200
        assert resp.json()["data"]["state"] == "resuming"

    def test_run_missing_task_handled(self) -> None:
        with _patch_service():
            resp = client.post("/api/v1/autonomy/run", json={})
        assert resp.status_code == 200
        # Service mock still succeeds; real service would default criteria.
        assert resp.json()["success"] is True

    def test_budget_payload_parsing_bounded(self) -> None:
        from app.api.v1.autonomy import _budget_from

        budget = _budget_from({"max_iterations": "3", "max_replans": "1",
                               "not_a_field": "999"})
        assert budget is not None
        assert budget.max_iterations == 3
        assert budget.max_replans == 1

    def test_policy_payload_parsing_bounded(self) -> None:
        from app.api.v1.autonomy import _policy_from

        policy = _policy_from({"allow_replan": True, "allow_scope_expansion": False,
                               "max_scope_expansions": 3})
        assert policy is not None
        assert policy.allow_replan is True
        assert policy.allow_scope_expansion is False
        assert policy.max_scope_expansions == 3

    def test_policy_string_bool_normalized(self) -> None:
        """JSON strings like "false" must not become True (bool('false'))."""
        from app.api.v1.autonomy import _policy_from

        policy = _policy_from({"allow_scope_expansion": "false", "allow_replan": "true"})
        assert policy is not None
        assert policy.allow_scope_expansion is False
        assert policy.allow_replan is True
