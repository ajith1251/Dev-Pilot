"""
Tests for the durability report API (Phase 19).

GET /api/v1/durability/report serves the JSON produced by
``scripts/durability_report.py --out`` so the web dashboard can render the
run_api/goal_api summary. The endpoint only READS the file (never runs the
live LLM paths), so these tests are fully deterministic — no provider, no
PostgreSQL.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.api.v1 import durability as durability_api
from app.config import settings
from app.main import app

# A realistic payload in the exact shape durability_report.py emits.
SAMPLE_REPORT = {
    "mode": "live",
    "run_api": {
        "run_id": "RUN-SAMPLE-001",
        "run_status": "approved",
        "handoffs": 5,
        "decisions": 4,
        "consensus_via_api": 3,
        "consensus_recovered": 3,
        "runs_in_table": 7,
    },
    "goal_api": {
        "goal_id": "GOAL-SAMPLE-001",
        "goal_state": "completed",
        "goal_runs": ["RUN-SAMPLE-002", "RUN-SAMPLE-003"],
        "goal_run_statuses": {
            "RUN-SAMPLE-002": "failed",
            "RUN-SAMPLE-003": "approved",
        },
        "goal_latest_run_status": "approved",
        "goal_handoffs": 6,
        "goal_decisions": 5,
        "goal_consensus": 4,
        "goal_recovered": "completed",
    },
    "gates": [],
    "passed": True,
}


@pytest.fixture()
def report_path(tmp_path, monkeypatch):
    """Point the endpoint at a tmp report file and return its path."""
    path = tmp_path / "durability_report.json"
    monkeypatch.setattr(settings, "DURABILITY_REPORT_PATH", str(path))
    return path


def _client() -> TestClient:
    return TestClient(app)


def test_report_404_when_missing(report_path):
    """No report file yet → 404 with guidance (dashboard shows empty state)."""
    res = _client().get("/api/v1/durability/report")
    assert res.status_code == 404
    body = res.json()
    assert "detail" in body
    assert "durability_report.py" in body["detail"]


def test_report_returns_payload_when_present(report_path):
    """A generated report is served verbatim in the success envelope."""
    report_path.write_text(json.dumps(SAMPLE_REPORT), encoding="utf-8")
    res = _client().get("/api/v1/durability/report")
    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    data = body["data"]
    assert data["mode"] == "live"
    assert data["run_api"]["run_status"] == "approved"
    assert data["goal_api"]["goal_latest_run_status"] == "approved"
    assert data["passed"] is True


def test_report_serves_skipped_mode(report_path):
    """Skip-mode reports (no provider/DB) are served too — dashboard shows reason."""
    skipped = {"mode": "skipped",
               "reason": "no live LLM provider configured"}
    report_path.write_text(json.dumps(skipped), encoding="utf-8")
    res = _client().get("/api/v1/durability/report")
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["mode"] == "skipped"
    assert "provider" in data["reason"]


def test_report_500_on_unreadable_file(report_path):
    """Corrupt JSON → 500 (never silently returns a broken payload)."""
    report_path.write_text("{ not valid json !!!", encoding="utf-8")
    res = _client().get("/api/v1/durability/report")
    assert res.status_code == 500
    assert "unreadable" in res.json()["detail"]


def test_report_500_on_non_object(report_path):
    """A JSON array at the report path is not a valid report document."""
    report_path.write_text("[1, 2, 3]", encoding="utf-8")
    res = _client().get("/api/v1/durability/report")
    assert res.status_code == 500
    assert "not a JSON object" in res.json()["detail"]


def test_default_report_path_resolution():
    """Without the setting, the default backend/durability_report.json is used.

    Regression: parents[3] of app/api/v1/durability.py must resolve to the
    backend root — a parent.parent.parent bug silently landed the file one
    level too deep (backend/app/...).
    """
    original = settings.DURABILITY_REPORT_PATH
    try:
        settings.DURABILITY_REPORT_PATH = None
        path = durability_api.resolve_report_path()
        assert path.name == "durability_report.json"
        assert path.parent.name == "backend"
        assert path.parent.is_dir()
    finally:
        settings.DURABILITY_REPORT_PATH = original
