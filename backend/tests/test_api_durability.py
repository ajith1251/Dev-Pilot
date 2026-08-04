"""
Raw-HTTP API path durability — repeatable pytest tests.

Converts the checks from ``scripts/verify_api_durability.py --live`` (runs
table row, handoffs metrics, consensus API, restart recovery, terminal
verdict / goal-state gates) into pytest test classes so the raw HTTP path is
covered deterministically in CI:

* ``TestApiDurabilityDeterministic`` — drives ``POST /api/v1/autonomy/run``
  through the real FastAPI app (ASGI) with deterministic stage drivers
  (no LLM required). Needs only a test-named PostgreSQL
  (``TEST_DATABASE_URL``) → runs in the ``postgres`` CI job on every push.
* ``TestLiveApiDurability`` — the full ``--live`` checks: one real
  ``execute_run`` (``POST /api/v1/runs``) **and** one real autonomous goal
  loop (``POST /api/v1/autonomy/run``) against a production LLM provider.
  Needs BOTH a test-named PostgreSQL AND a live provider
  (``DEVPILOT_LLM_PROVIDER`` + API key) → skips cleanly in every CI job
  without provider secrets; runs for real in the ``live-llm-e2e`` job.

Both classes reuse the exact helpers from ``scripts/verify_api_durability.py``
(pick_database_url / ensure_schema / build_wired_stack / run_live_http_* /
TERMINAL_STATUSES / TERMINAL_GOAL_STATES) so the pytest coverage is the same
code the script exercised, never a parallel reimplementation.

Skip policy (CI-safe, deterministic):

* No test-named PostgreSQL available  -> ``pytest.skip``
* No live LLM provider configured     -> ``pytest.skip`` (live class only)

The expensive real-LLM work runs ONCE per module (module-scoped fixture),
then each test method asserts on the collected artifacts.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Dict

import httpx
import pytest

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = _BACKEND_DIR / "scripts"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# Reuse the script's exact helpers (imports app.main at module scope — same
# as test_autonomy_api / test_api_contract already do).
import verify_api_durability as vd  # noqa: E402

FIXTURE_AUTH_APP = _BACKEND_DIR / "tests" / "fixtures" / "fixture_auth_app"

_TASK = "Fix password reset token expiration so expired tokens are rejected"
_CRITERIA = [
    "Expired reset tokens must be rejected",
    "Valid reset tokens must be accepted",
]
_BUDGET = {"max_iterations": 5, "max_replans": 2, "max_repairs": 3}


# Engine disposal lives in scripts/verify_api_durability.py (single source
# of truth) — the pytest classes and the JSON wrapper share it.
_dispose_stack_engines = vd._dispose_stack_engines


def _test_db_url_or_skip() -> str:
    """Return a test-named DB URL or skip the test cleanly."""
    db_url = vd.pick_database_url()
    if not db_url:
        pytest.skip("No test-named PostgreSQL available (set TEST_DATABASE_URL)")
    if not vd.ensure_schema(db_url):
        pytest.skip("alembic upgrade head failed — skipping raw-HTTP durability tests")
    return db_url


# ── Deterministic raw-HTTP path (needs live PG only — runs in CI postgres job)


@pytest.mark.integration
class TestApiDurabilityDeterministic:
    """Raw HTTP goal path against live PG with deterministic drivers.

    Mirrors the deterministic mode of ``scripts/verify_api_durability.py``:
    ``POST /api/v1/autonomy/run`` through the real FastAPI app (ASGI) with
    stage drivers replacing the LLM, then verifies runs table rows, handoff
    metrics and restart recovery. No live provider needed → deterministic
    coverage of the raw HTTP path in CI on every push.
    """

    @pytest.fixture(scope="module")
    def artifacts(self) -> Dict[str, Any]:
        db_url = _test_db_url_or_skip()
        import app.api.v1.autonomy as autonomy_api

        saved_service = autonomy_api._service

        async def _drive() -> Dict[str, Any]:
            ctrl, orch, collab, session_factory, run_store, _reasoning = (
                vd.build_wired_stack(db_url)  # deterministic drivers
            )
            autonomy_api._service = ctrl

            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=vd.fastapi_app),
                    base_url="http://testserver",
                    timeout=300.0,
                ) as client:
                    resp = await client.post("/api/v1/autonomy/run", json={
                        "task": _TASK,
                        "criteria": _CRITERIA,
                        "budget": _BUDGET,
                    })
                    body = resp.json()
                    assert resp.status_code == 200, f"HTTP {resp.status_code}: {body}"
                    assert body.get("success"), body.get("message")
                    goal_id = body["data"]["goal_id"]
                    goal_state = body["data"]["state"]

                    list_resp = await client.get("/api/v1/autonomy", params={
                        "state": "completed", "limit": 50,
                    })
                    list_body = list_resp.json()
                    assert list_body.get("success"), list_body.get("message")
                    filtered_ids = [g["goal_id"] for g in list_body["data"]["goals"]]

                rows = await run_store.list(limit=20)
                run_ids = [r.run_id for r in rows]

                total_handoffs = 0
                for run_id in run_ids:
                    try:
                        metrics = await collab.get_collaboration_metrics(run_id)
                    except Exception:
                        continue
                    total_handoffs += metrics["handoffs_total"]

                from app.services.autonomy_service import AutonomousExecutionController

                fresh = AutonomousExecutionController(
                    orchestration=orch,
                    collaboration=collab,
                    session_factory=session_factory,
                    run_store=run_store,
                )
                recovered = await fresh.recover(goal_id)

                return {
                    "goal_id": goal_id,
                    "goal_state": goal_state,
                    "completed_filter_contains_goal": goal_id in filtered_ids,
                    "run_ids": run_ids,
                    "total_handoffs": total_handoffs,
                    "recovered_state": recovered.state.value,
                }
            finally:
                await _dispose_stack_engines(session_factory, collab)

        try:
            return asyncio.run(_drive())
        finally:
            autonomy_api._service = saved_service

    def test_goal_completes_through_raw_http(self, artifacts: Dict[str, Any]) -> None:
        """Deterministic goal must reach a terminal state and appear under the
        completed filter (fail_then_pass drivers → REPAIR → COMPLETED)."""
        assert artifacts["goal_state"] in vd.TERMINAL_GOAL_STATES
        assert artifacts["completed_filter_contains_goal"] is True

    def test_runs_persisted_in_runs_table(self, artifacts: Dict[str, Any]) -> None:
        """The raw HTTP goal path must write runs through PostgresRunStore."""
        assert len(artifacts["run_ids"]) >= 1

    def test_handoffs_metrics_recorded(self, artifacts: Dict[str, Any]) -> None:
        """Collaboration handoffs must be recorded for the persisted runs."""
        assert artifacts["total_handoffs"] >= 1

    def test_restart_recovery_rehydrates_goal(self, artifacts: Dict[str, Any]) -> None:
        """A fresh controller must rehydrate the goal from the DB."""
        assert artifacts["recovered_state"] == artifacts["goal_state"]


# ── JSON wrapper (scripts/durability_report.py) — deterministic skip path


class TestDurabilityReportJson:
    """The live-class JSON wrapper: `scripts/durability_report.py`.

    Deterministically validates the skip path (no live provider) so the
    wrapper is CI-safe without API keys — the JSON report path itself runs
    the exact same `vd.run_live_http_*` helpers the live class already
    exercises (no parallel reimplementation to unit-test separately).
    """

    def test_wrapper_skips_cleanly_without_provider(self) -> None:
        """No provider -> `{"mode": "skipped"}` JSON and exit 0."""
        import os
        import subprocess

        env = dict(os.environ)
        # Force-empty the provider: env vars override backend/.env, so this
        # is deterministic even when the local .env has a real key configured.
        env["DEVPILOT_LLM_PROVIDER"] = ""
        for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
            env.pop(key, None)
        script = _SCRIPTS_DIR / "durability_report.py"
        proc = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True, text=True, env=env, timeout=120,
        )
        assert proc.returncode == 0, proc.stderr[-500:]
        import json as _json
        report = _json.loads(proc.stdout)
        assert report["mode"] == "skipped"
        assert report["reason"]


# ── Live raw-HTTP path (needs live PG + live provider — skips cleanly otherwise)


@pytest.mark.integration
@pytest.mark.live
class TestLiveApiDurability:
    """The verify_api_durability.py --live checks as repeatable pytest tests.

    One real ``execute_run`` (run API) + one real autonomous goal loop (goal
    API) against a production LLM provider, then asserts the same gates the
    script exits 1 on: runs table row, handoffs metrics, consensus API,
    restart recovery, terminal verdict and terminal goal state. Skips
    cleanly without a live provider so CI stays green with no API keys.
    """

    @pytest.fixture(scope="module")
    def artifacts(self) -> Dict[str, Any]:
        # Cheap gate first (no side effects) so provider-less runs skip
        # without a wasted `alembic upgrade head` subprocess.
        if not vd.check_live_mode():
            pytest.skip(
                "No live LLM provider configured (DEVPILOT_LLM_PROVIDER + "
                "matching API key) — skipping live durability tests")
        db_url = _test_db_url_or_skip()

        import app.api.v1.autonomy as autonomy_api
        import app.api.v1.orchestration as orch_api
        import app.api.v1.reasoning as reasoning_api

        saved = (
            getattr(orch_api, "workflow", None),
            getattr(reasoning_api, "_service", None),
            getattr(autonomy_api, "_service", None),
        )

        async def _drive() -> Dict[str, Any]:
            ctrl, orch, collab, session_factory, run_store, reasoning = (
                vd.build_wired_stack(db_url, live=True)
            )
            try:
                run_result = await vd.run_live_http_execute(
                    orch, collab, run_store, reasoning, session_factory,
                    str(FIXTURE_AUTH_APP),
                )
                goal_result = await vd.run_live_http_goal(
                    ctrl, orch, collab, run_store, reasoning, session_factory,
                    str(FIXTURE_AUTH_APP),
                )
                return {"run": run_result, "goal": goal_result}
            finally:
                await _dispose_stack_engines(session_factory, collab)

        try:
            return asyncio.run(_drive())
        finally:
            orch_api.workflow, reasoning_api._service, autonomy_api._service = saved

    # ── run API (POST /api/v1/runs) ─────────────────────────────────

    def test_run_row_persisted_in_runs_table(self, artifacts: Dict[str, Any]) -> None:
        """The live run must appear in the `runs` table via PostgresRunStore."""
        run = artifacts["run"]
        assert run["run_id"]
        assert run["runs_in_table"] >= 1

    def test_run_handoffs_and_decisions_recorded(self, artifacts: Dict[str, Any]) -> None:
        """Collaboration metrics (handoffs + decisions) must be > 0."""
        run = artifacts["run"]
        assert run["handoffs"] > 0
        assert run["decisions"] > 0

    def test_run_consensus_available_via_api(self, artifacts: Dict[str, Any]) -> None:
        """GET /api/v1/runs/{id}/consensus must return records."""
        assert artifacts["run"]["consensus_via_api"] > 0

    def test_run_consensus_survives_restart(self, artifacts: Dict[str, Any]) -> None:
        """A fresh reasoning engine must recover the same consensus count."""
        run = artifacts["run"]
        assert run["consensus_recovered"] > 0
        assert run["consensus_recovered"] == run["consensus_via_api"]

    def test_run_reaches_terminal_verdict(self, artifacts: Dict[str, Any]) -> None:
        """The live run must reach a verdict (approved/rejected/needs_human_review)."""
        assert artifacts["run"]["run_status"] in vd.TERMINAL_STATUSES

    # ── goal API (POST /api/v1/autonomy/run) ────────────────────────

    def test_goal_persists_runs_and_verdicts(self, artifacts: Dict[str, Any]) -> None:
        """The goal's runs must be persisted and its NEWEST run terminal.

        Item-13 bounded retry: a superseded first attempt (transient coding
        variance -> 'No patch produced') may legitimately stay `failed` in
        the audit trail while the retried run reaches a verdict — so the
        gate is the goal's newest run, not every attempt. A genuinely broken
        pipeline still fails because its final attempt never reaches a
        verdict.
        """
        goal = artifacts["goal"]
        assert goal["goal_runs"], "goal API must persist at least one run"
        assert goal["goal_latest_run_status"] in vd.TERMINAL_STATUSES

    def test_goal_reaches_terminal_state(self, artifacts: Dict[str, Any]) -> None:
        """The goal must reach a terminal state (not failed/cancelled)."""
        assert artifacts["goal"]["goal_state"] in vd.TERMINAL_GOAL_STATES

    def test_goal_restart_recovery_rehydrates(self, artifacts: Dict[str, Any]) -> None:
        """A fresh controller must recover the goal to its persisted state."""
        goal = artifacts["goal"]
        assert goal["goal_recovered"] in vd.TERMINAL_GOAL_STATES
        assert goal["goal_recovered"] == goal["goal_state"]
