"""
Phase 15 — Tests for the collaboration API endpoints.

Uses a mocked CollaborationService so no DB is required. Verifies
response envelope, pagination, handoff detail, decisions, and the
collaboration summary endpoint.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.models.collaboration import (
    AgentHandoff,
    ConflictResolution,
    DecisionType,
    EvidenceConflict,
    EvidenceRef,
    EvidenceType,
    HandoffStatus,
    RunDecision,
)

client = TestClient(app)


def _make_handoff(i=1):
    return AgentHandoff(
        run_id="RUN-1",
        from_agent="planner",
        to_agent="coding",
        stage="planning",
        summary=f"Plan {i} ready",
        affected_symbols=["auth_service.py::AuthService"],
        evidence_refs=[EvidenceRef(type=EvidenceType.PLAN, reference="step-1")],
        status=HandoffStatus.VALIDATED,
    )


def _make_decision(i=1):
    return RunDecision(
        run_id="RUN-1",
        decision_type=DecisionType.IMPLEMENTATION,
        statement=f"Decision {i}",
        made_by="coding",
    )


def _make_conflict():
    return EvidenceConflict(
        run_id="RUN-1",
        description="Coding claimed pass but tests failed",
        claim_evidence=EvidenceRef(type=EvidenceType.AGENT_CLAIM, reference="HO-1"),
        deterministic_evidence=EvidenceRef(type=EvidenceType.TEST_RESULT, reference="failed"),
        resolution=ConflictResolution.DETERMINISTIC_WINS,
    )


def _mock_service():
    svc = MagicMock()
    svc.list_handoffs = AsyncMock(return_value=[_make_handoff()])
    svc.get_handoff = AsyncMock(return_value=_make_handoff())
    svc.list_decisions = AsyncMock(return_value=[_make_decision()])
    svc.list_conflicts = AsyncMock(return_value=[_make_conflict()])
    svc.get_collaboration_metrics = AsyncMock(return_value={
        "run_id": "RUN-1",
        "handoffs_total": 1,
        "handoffs_by_to_agent": {"coding": 1},
        "handoffs_validated": 1,
        "decisions": 1,
        "conflicts_detected": 1,
        "conflicts_resolved": 1,
        "evidence_items": 1,
    })
    return svc


class TestHandoffEndpoints:
    @patch("app.api.v1.collaboration._get_service")
    def test_list_handoffs(self, mock_get):
        mock_get.return_value = _mock_service()
        res = client.get("/api/v1/runs/RUN-1/handoffs")
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["data"][0]["handoff_id"].startswith("HO-")
        assert data["data"][0]["from_agent"] == "planner"
        assert data["data"][0]["to_agent"] == "coding"

    @patch("app.api.v1.collaboration._get_service")
    def test_list_handoffs_filtered(self, mock_get):
        svc = _mock_service()
        mock_get.return_value = svc
        client.get("/api/v1/runs/RUN-1/handoffs?to_agent=repair")
        _, kwargs = svc.list_handoffs.call_args
        assert kwargs["to_agent"] == "repair"

    @patch("app.api.v1.collaboration._get_service")
    def test_get_handoff(self, mock_get):
        mock_get.return_value = _mock_service()
        res = client.get("/api/v1/runs/RUN-1/handoffs/HO-TEST")
        assert res.status_code == 200
        assert res.json()["success"] is True

    @patch("app.api.v1.collaboration._get_service")
    def test_get_handoff_not_found(self, mock_get):
        svc = _mock_service()
        svc.get_handoff = AsyncMock(return_value=None)
        mock_get.return_value = svc
        res = client.get("/api/v1/runs/RUN-1/handoffs/HO-NOPE")
        assert res.json()["success"] is False
        assert res.json()["error"] == "NotFound"


class TestDecisionEndpoints:
    @patch("app.api.v1.collaboration._get_service")
    def test_list_decisions(self, mock_get):
        mock_get.return_value = _mock_service()
        res = client.get("/api/v1/runs/RUN-1/decisions")
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["data"][0]["decision_type"] == "implementation"
        assert data["data"][0]["made_by"] == "coding"


class TestCollaborationSummary:
    @patch("app.api.v1.collaboration._get_service")
    def test_collaboration_summary(self, mock_get):
        mock_get.return_value = _mock_service()
        res = client.get("/api/v1/runs/RUN-1/collaboration")
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        body = data["data"]
        assert body["handoffs_total"] == 1
        assert body["conflicts_detected"] == 1
        assert len(body["handoffs"]) == 1
        assert len(body["decisions"]) == 1
        assert len(body["conflicts"]) == 1
        assert body["conflicts"][0]["resolution"] == "deterministic_wins"
