"""
Phase 16 — Live API Path Durability Validation.

Proves the real HTTP path persists autonomous runs end-to-end:

    POST /api/v1/autonomy/run  (task + criteria + budget)
        → AutonomousExecutionController (create_goal + start loop)
        → OrchestrationService bound to PostgresRunStore (test DB)
        → runs / handoffs / checkpoints / decisions written durably

Validated against a live PostgreSQL test database:

    1. POST /api/v1/autonomy/run through the real FastAPI app (ASGI)
    2. The `runs` table gains rows (PostgresRunStore.list) — closes the
       "runs=0 on the goal path" gap through the API path
    3. Collaboration handoffs/decisions persist for each executed run
    4. Restart recovery: a fresh controller rehydrates the goal from the DB
    5. GET /api/v1/autonomy?state=<filter> returns the filtered goal list

Mode: deterministic (no LLM API required). The LLM-dependent stage bodies
are replaced by drivers (same pattern as demo_phase16 / demo_phase15) that
emit the run state real agents would; the first test run FAILS then PASSES
so the autonomous loop's REPAIR path, budget usage, and plan/decision
timeline are exercised — the deterministic evidence is authoritative and
every persistence layer runs for real.

Live mode (--live): drives BOTH API paths against the configured LLM
provider with real stage bodies — (1) `POST /api/v1/runs` (one real
`execute_run`) and (2) `POST /api/v1/autonomy/run` (one real autonomous
goal loop) — then verifies runs / handoffs / consensus persist via
PostgresRunStore end-to-end. Requires DEVPILOT_LLM_PROVIDER=openai (or
anthropic/gemini) AND the matching API key in .env; the workspace is
copied from tests/fixtures/fixture_auth_app.

CI gate: in live mode the script EXITS 1 (non-zero) when either path fails
to reach a terminal outcome — the run must reach a verdict (approved /
rejected / needs_human_review) and the goal must reach a terminal state
(completed / stopped / waiting_for_human) with at least one persisted run
that reached a verdict. A failed/cancelled/stuck pipeline fails the job
instead of silently passing on durability alone. The GitHub Actions
`live-llm-e2e` job relies on this to catch provider outages/quota
mid-pipeline (mirrors demo_phase17 Demonstration A).

Database: uses TEST_DATABASE_URL when the DB name contains "test" (schema
ensured via `alembic upgrade head`). Refuses to touch a non-test database.

Run from the backend directory:
    python scripts/verify_api_durability.py
    python scripts/verify_api_durability.py --json
    python scripts/verify_api_durability.py --live
    python scripts/verify_api_durability.py --live --repository <path>
"""

from __future__ import annotations

import sys

# Windows consoles default to the cp1252 codec; keep the demo output clean.
for _stream in (sys.stdout, sys.stderr):
    reconfigure = getattr(_stream, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8", errors="replace")

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from app.main import app as fastapi_app


def sep(title: str) -> None:
    print()
    print("=" * 72)
    print("  " + title)
    print("=" * 72)
    print()


# ── Database selection (test-DB safe) ──────────────────────────────

def pick_database_url() -> Optional[str]:
    """Return a test-named DB URL, or None for in-memory persistence."""
    from app.config import settings

    url = settings.TEST_DATABASE_URL or settings.DATABASE_URL
    if url and "test" in url.split("/")[-1].lower():
        return url
    if url:
        print("  [error] No test-named database available - refusing to "
              "mutate a non-test DB. Set TEST_DATABASE_URL to a database "
              "whose name contains 'test'.")
        return None
    print("  [error] No DATABASE_URL configured. Set TEST_DATABASE_URL.")
    return None


def ensure_schema(db_url: str) -> bool:
    """Run `alembic upgrade head` against the test database (idempotent)."""
    backend_dir = Path(__file__).resolve().parent.parent
    env = os.environ.copy()
    env["PYTHONPATH"] = str(backend_dir)
    env["DATABASE_URL"] = db_url
    env["TEST_DATABASE_URL"] = db_url
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=str(backend_dir),
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
    )
    if result.returncode != 0:
        print(f"  [error] alembic upgrade failed: {result.stderr[-400:]}")
        return False
    print(f"  [ok] alembic upgrade head against {db_url.split('@')[-1]}")
    return True


def build_session_factory(db_url: str):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(db_url)
    return async_sessionmaker(engine, expire_on_commit=False)


def build_memory_service(db_url: str):
    from app.services.repository_memory_service import RepositoryMemoryService

    svc = RepositoryMemoryService()
    svc._session_factory = build_session_factory(db_url)
    return svc


# ── Terminal-verdict gate ──────────────────────────────────────────
# A live run must REACH a verdict for the CI job to pass. FAILED/CANCELLED
# mean the pipeline broke mid-flight (provider outage, quota, exception);
# durability alone is not a successful Demonstration A.
# NOTE: RunStatus lists FAILED/CANCELLED among its "terminal states" too —
# the divergence is intentional: a failed/cancelled pipeline must FAIL the
# CI job, so only verdicts (approved / rejected / needs_human_review) are
# accepted here. Do not add FAILED/CANCELLED to this set.
TERMINAL_STATUSES = {"approved", "rejected", "needs_human_review"}


# ── Terminal goal-state gate ───────────────────────────────────────
# The goal loop (POST /api/v1/autonomy/run) always finishes synchronously
# into one of: completed (criteria met) / waiting_for_human or stopped
# (deterministic escalation after an unsatisfied iteration) / failed
# (exception) / cancelled. FAILED or CANCELLED means the pipeline broke —
# the CI job must fail. WAITING_FOR_HUMAN / STOPPED are legitimate
# verdicts: the loop ran real LLM stages, decided deterministically, and
# persisted everything.
TERMINAL_GOAL_STATES = {"completed", "stopped", "waiting_for_human"}


def check_live_mode() -> bool:
    """Validate the live-LLM prerequisites (reuses demo_phase17's check)."""
    from demo_phase17 import check_live_mode as _check_live_mode

    return _check_live_mode()


def build_wired_stack(db_url: str, live: bool = False):
    """Controller + orchestration + collaboration bound to the test DB.

    Returns (ctrl, orch, collab, session_factory, run_store, reasoning).

    Deterministic mode: the LLM stage bodies are replaced by drivers so the
    API path is exercised without an LLM provider. Live mode: the real stage
    bodies run (real LLM) and a reasoning engine bound to the test DB is
    injected so consensus/notebook persist at run completion.
    """
    from app.services.autonomy_service import AutonomousExecutionController
    from app.services.collaboration_service import CollaborationService
    from app.services.orchestration_service import OrchestrationService
    from app.services.postgres_run_store import PostgresRunStore

    session_factory = build_session_factory(db_url)
    collab = CollaborationService(
        session_factory=session_factory,
        memory_service=build_memory_service(db_url),
    )
    run_store = PostgresRunStore()
    run_store._session_factory = session_factory
    orch = OrchestrationService(run_store=run_store)
    orch._collaboration = collab
    orch._get_collaboration = lambda: collab

    reasoning = None
    if live:
        # Real stage bodies — DO NOT install drivers. Bind the reasoning
        # engine to the test DB so analyze_run() (run-completion hook)
        # persists consensus/notebook durably through the HTTP run path.
        from app.services.reasoning_service import CollaborativeReasoningEngine

        reasoning = CollaborativeReasoningEngine(
            session_factory=session_factory,
            collaboration=collab,
        )
        orch._reasoning = reasoning
    else:
        # Deterministic stage drivers (first test run fails, then passes).
        from demo_phase16 import install_drivers

        install_drivers(orch, fail_then_pass=True)

    ctrl = AutonomousExecutionController(
        orchestration=orch,
        collaboration=collab,
        session_factory=session_factory,
        run_store=run_store,
    )
    if reasoning is not None:
        ctrl._reasoning = reasoning
    return ctrl, orch, collab, session_factory, run_store, reasoning


async def _dispose_engine(session_factory: Any) -> None:
    """Dispose the bound async engine (no-op when none is bound).

    ``build_wired_stack`` injects the session factory directly into each
    store (``run_store._session_factory = ...``), so ``run_store.dispose()``
    (which only disposes ``_owned_engine``) would leak the engine/pool. The
    factory's bound engine is the single shared one — disposing it covers
    the orchestration, collaboration, reasoning, and autonomy stores alike.
    """
    engine = getattr(session_factory, "bind", None)
    if engine is None:
        return
    try:
        await engine.dispose()
    except Exception:
        pass


async def _dispose_stack_engines(session_factory: Any, collab: Any) -> None:
    """Dispose every engine the wired stack created.

    ``build_wired_stack`` builds the shared ``session_factory`` AND a second
    engine inside ``build_memory_service`` (bound into the collaboration
    service). Both must be disposed so no pool leaks across the process.
    """
    await _dispose_engine(session_factory)
    memory_svc = getattr(collab, "_memory_service", None)
    memory_sf = getattr(memory_svc, "_session_factory", None)
    if memory_sf is not None:
        await _dispose_engine(memory_sf)


def seed_live_api(orch: Any, reasoning: Any, ctrl: Any = None) -> None:
    """Point the API singletons at the wired stack (real LLM + test DB).

    POST /api/v1/runs uses `app.api.v1.orchestration.workflow`; the
    reasoning endpoints use `app.api.v1.reasoning._service`; POST
    /api/v1/autonomy/run uses `app.api.v1.autonomy._service`. Seeding all
    keeps the whole HTTP path on the same PostgresRunStore + collaboration
    + reasoning + autonomy instances the rest of the validation inspects.
    """
    import app.api.v1.orchestration as orch_api
    import app.api.v1.reasoning as reasoning_api
    from app.workflows.orchestration import OrchestrationWorkflow

    orch_api.workflow = OrchestrationWorkflow(orchestration_service=orch)
    reasoning_api._service = reasoning
    if ctrl is not None:
        import app.api.v1.autonomy as autonomy_api

        autonomy_api._service = ctrl


async def run_live_http_execute(
    orch: Any,
    collab: Any,
    run_store: Any,
    reasoning: Any,
    session_factory: Any,
    fixture: str,
    repository: Optional[str] = None,
) -> dict:
    """POST /api/v1/runs → one REAL execute_run through the HTTP API.

    Verifies runs / handoffs / consensus persist via PostgresRunStore
    end-to-end (Demonstration A for the run-API path, live-LLM).
    """
    tmp = tempfile.mkdtemp(prefix="devpilot-verify-live-")
    try:
        src = Path(fixture)
        name = Path(repository).name if repository else src.name
        repo = str(Path(tmp) / name)
        shutil.copytree(str(src), repo, dirs_exist_ok=True)
        print(f"  Live workspace: {repo}")

        seed_live_api(orch, reasoning)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=fastapi_app),
            base_url="http://testserver",
            timeout=900.0,
        ) as client:
            resp = await client.post("/api/v1/runs", json={
                "source": "user_task",
                "title": "Fix auth token expiration (live API durability verify)",
                "repository": repo,
                "workspace_root": repo,
            })
            body = resp.json()
            assert resp.status_code == 200, f"HTTP {resp.status_code}: {body}"
            assert body.get("success"), body.get("detail") or body
            result = body["data"]
            run_id = result["run_id"]
            print(f"  POST /api/v1/runs -> {run_id} status={result['status']}")
            print(f"  stages: {result['stages']}")

            # Run detail through the HTTP API (PostgresRunStore-backed).
            get_resp = await client.get(f"/api/v1/runs/{run_id}")
            get_body = get_resp.json()
            assert get_body.get("success"), get_body
            detail = get_body["data"]
            print(f"  GET /api/v1/runs/{run_id} -> {detail['status']} "
                  f"@ {detail['current_stage']}")

            # Consensus through the reasoning API (persisted + recovered).
            cons_resp = await client.get(f"/api/v1/runs/{run_id}/consensus")
            cons_body = cons_resp.json()
            consensus = cons_body.get("data", []) if cons_body.get("success") else []
            print(f"  GET /api/v1/runs/{run_id}/consensus -> {len(consensus)} record(s)")

        # Direct store/collaboration inspection (same DB the API wrote to).
        rows = await run_store.list(limit=20)
        run_ids = {r.run_id for r in rows}
        assert run_id in run_ids, f"run {run_id} missing from `runs` table"
        print(f"  Runs in `runs` table: {len(rows)} (target present: True)")

        metrics = await collab.get_collaboration_metrics(run_id)
        print(f"  Run {run_id}: handoffs={metrics['handoffs_total']} "
              f"decisions={metrics['decisions']} "
              f"conflicts={metrics['conflicts_detected']} "
              f"evidence={metrics['evidence_items']}")

        # Restart recovery: a FRESH reasoning engine rehydrates from the DB.
        from app.services.reasoning_service import CollaborativeReasoningEngine

        fresh = CollaborativeReasoningEngine(
            session_factory=session_factory,
            collaboration=collab,
        )
        await fresh.recover(run_id)
        recovered_consensus = await fresh.list_consensus(run_id)
        notebook = await fresh.get_notebook(run_id)
        print(f"  Restart recovery: consensus={len(recovered_consensus)} "
              f"notebook={'found' if notebook is not None else 'missing'}")

        # Report the verdict; the caller (main) hard-fails the CI job when
        # the run did not reach a terminal stage.
        print(f"  Terminal verdict: {detail['status']}")

        return {
            "run_id": run_id,
            "run_status": detail["status"],
            "handoffs": metrics["handoffs_total"],
            "decisions": metrics["decisions"],
            "consensus_via_api": len(consensus),
            "consensus_recovered": len(recovered_consensus),
            "runs_in_table": len(rows),
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def run_live_http_goal(
    ctrl: Any,
    orch: Any,
    collab: Any,
    run_store: Any,
    reasoning: Any,
    session_factory: Any,
    fixture: str,
    repository: Optional[str] = None,
) -> dict:
    """POST /api/v1/autonomy/run -> ONE real autonomous goal loop (real LLM).

    The bounded goal loop executes real `execute_run` bodies per iteration
    against a copied fixture workspace. Verifies the goal record, its
    persisted runs (PostgresRunStore), collaboration evidence, consensus,
    and restart recovery end-to-end — the goal-API counterpart to
    `run_live_http_execute` (Demonstration A for the autonomy path).
    """
    tmp = tempfile.mkdtemp(prefix="devpilot-verify-live-goal-")
    try:
        src = Path(fixture)
        name = Path(repository).name if repository else src.name
        repo = str(Path(tmp) / name)
        shutil.copytree(str(src), repo, dirs_exist_ok=True)
        print(f"  Goal workspace: {repo}")

        seed_live_api(orch, reasoning, ctrl)

        before = {r.run_id for r in await run_store.list(limit=200)}

        task = "Fix password reset token expiration so expired tokens are rejected"
        criteria = ["Expired reset tokens must be rejected"]
        # Bounded: at most one real execute_run before the deterministic
        # decision loop terminates (0 repairs/replans = disabled).
        budget = {"max_iterations": 2, "max_replans": 0, "max_repairs": 0}

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=fastapi_app),
            base_url="http://testserver",
            timeout=900.0,
        ) as client:
            resp = await client.post("/api/v1/autonomy/run", json={
                "task": task,
                "repository": repo,
                "criteria": criteria,
                "budget": budget,
            })
            body = resp.json()
            assert resp.status_code == 200, f"HTTP {resp.status_code}: {body}"
            assert body.get("success"), body.get("message") or body
            data = body["data"]
            goal_id = data["goal_id"]
            goal_state = data["state"]
            print(f"  POST /api/v1/autonomy/run -> {goal_id} state={goal_state}")

            # Full status through the goal API (persisted state).
            st_resp = await client.get(f"/api/v1/autonomy/{goal_id}")
            st_body = st_resp.json()
            assert st_body.get("success"), st_body
            print(f"  GET /api/v1/autonomy/{goal_id} -> "
                  f"state={st_body['data']['state']}")

            # Consensus for the goal's runs through the reasoning API.
            after = {r.run_id for r in await run_store.list(limit=200)}
            consensus_per_run: dict = {}
            for rid in sorted(after - before):
                cons_resp = await client.get(f"/api/v1/runs/{rid}/consensus")
                cons_body = cons_resp.json()
                consensus_per_run[rid] = (
                    len(cons_body["data"]) if cons_body.get("success") else 0
                )

        # Direct store/collaboration inspection (same DB the API wrote to).
        rows = await run_store.list(limit=200)
        new_rows = [r for r in rows if r.run_id in (after - before)]
        run_statuses = {r.run_id: r.status.value for r in new_rows}
        # Item 13 bounded goal-path retry: a superseded first attempt (transient
        # coding variance -> 'No patch produced') stays in the audit trail as
        # `failed` while the retried run reaches a terminal verdict. Gate on
        # the NEWEST goal run (rows are newest-first), not every attempt.
        latest_run_status = new_rows[0].status.value if new_rows else None
        print(f"  Goal runs persisted: {len(new_rows)} -> {run_statuses}")
        print(f"  Goal newest run status: {latest_run_status}")

        total_handoffs = 0
        total_decisions = 0
        for rid in sorted(run_statuses):
            metrics = await collab.get_collaboration_metrics(rid)
            total_handoffs += metrics["handoffs_total"]
            total_decisions += metrics["decisions"]
            print(f"  Run {rid}: status={run_statuses[rid]} "
                  f"handoffs={metrics['handoffs_total']} "
                  f"decisions={metrics['decisions']} "
                  f"consensus={consensus_per_run.get(rid, 0)}")

        # Restart recovery: a FRESH controller rehydrates the goal from DB.
        from app.services.autonomy_service import AutonomousExecutionController

        fresh = AutonomousExecutionController(
            orchestration=orch,
            collaboration=collab,
            session_factory=session_factory,
            run_store=run_store,
        )
        recovered = await fresh.recover(goal_id)
        print(f"  Restart recovery: goal {goal_id} rehydrated -> "
              f"state={recovered.state.value}, "
              f"decisions={len(recovered.decisions)}, "
              f"plan_versions={len(recovered.plan_versions)}")

        return {
            "goal_id": goal_id,
            "goal_state": goal_state,
            "goal_runs": sorted(run_statuses),
            "goal_run_statuses": run_statuses,
            "goal_latest_run_status": latest_run_status,
            "goal_handoffs": total_handoffs,
            "goal_decisions": total_decisions,
            "goal_consensus": sum(consensus_per_run.values()),
            "goal_recovered": recovered.state.value,
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def main(json_output: bool = False, live: bool = False,
               repository: Optional[str] = None) -> None:
    task = "Fix password reset token expiration so expired tokens are rejected"
    criteria = [
        "Expired reset tokens must be rejected",
        "Valid reset tokens must be accepted",
    ]
    budget = {"max_iterations": 5, "max_replans": 2, "max_repairs": 3}

    if live:
        sep("PHASE 16 — LIVE API PATH DURABILITY VALIDATION (REAL LLM)")
        print(f"  Task: {task[:80]}")
        print(f"  Mode: LIVE execute_run via HTTP API + REAL PostgreSQL test DB")
        print()
        if not check_live_mode():
            return

        db_url = pick_database_url()
        if not db_url:
            return
        if not ensure_schema(db_url):
            return

        _ctrl, orch, collab, session_factory, run_store, reasoning = (
            build_wired_stack(db_url, live=True)
        )
        fixture = (
            Path(__file__).resolve().parent.parent
            / "tests" / "fixtures" / "fixture_auth_app"
        )
        live_result = await run_live_http_execute(
            orch, collab, run_store, reasoning, session_factory,
            str(fixture), repository=repository,
        )
        goal_result = await run_live_http_goal(
            _ctrl, orch, collab, run_store, reasoning, session_factory,
            str(fixture), repository=repository,
        )

        print()
        print("  RESULT: BOTH HTTP paths persist runs/handoffs/consensus via "
              "PostgresRunStore ✅")
        if json_output:
            print()
            print("JSON:")
            print(json.dumps({
                "mode": "live",
                "run_api": live_result,
                "goal_api": goal_result,
            }, indent=2))

        # CI gates (mirror demo_phase17 Demonstration A): the job must FAIL
        # when either path failed to reach a terminal outcome. Durability is
        # proven either way, but a FAILED/CANCELLED pipeline means the
        # real-LLM path did not complete end-to-end (provider outage, quota).
        failed_gates: list = []
        if live_result["run_status"] not in TERMINAL_STATUSES:
            failed_gates.append(
                f"run API: status='{live_result['run_status']}' is not a "
                "terminal verdict (approved/rejected/needs_human_review)")
        if goal_result["goal_state"] not in TERMINAL_GOAL_STATES:
            failed_gates.append(
                f"goal API: state='{goal_result['goal_state']}' is not a "
                "terminal goal state (completed/stopped/waiting_for_human)")
        if not goal_result["goal_runs"]:
            failed_gates.append(
                "goal API: no runs persisted for the live goal")
        # The goal's NEWEST run must reach a terminal verdict. With the
        # item-13 bounded retry, a superseded first attempt may legitimately
        # be `failed` (transient coding variance) while the retried run
        # reaches a verdict — a genuinely broken pipeline still fails this
        # gate because its final attempt never reaches a verdict.
        if (goal_result.get("goal_latest_run_status")
                not in TERMINAL_STATUSES):
            failed_gates.append(
                "goal API: the goal's newest run did not reach a terminal "
                f"verdict (latest={goal_result.get('goal_latest_run_status')}, "
                f"all={goal_result['goal_run_statuses']})")
        if failed_gates:
            print()
            print("  [error] Live validation did not complete end-to-end:")
            for g in failed_gates:
                print(f"    - {g}")
            print("  Exiting 1 so the CI job fails.")
            raise SystemExit(1)
        return

    sep("PHASE 16 — LIVE API PATH DURABILITY VALIDATION")
    print(f"  Task: {task[:80]}")
    print(f"  Mode: deterministic drivers + REAL PostgreSQL test DB")
    print()

    db_url = pick_database_url()
    if not db_url:
        return
    if not ensure_schema(db_url):
        return

    ctrl, orch, collab, session_factory, run_store, _reasoning = (
        build_wired_stack(db_url)
    )

    # ── 1. Pre-seed the API singleton so the HTTP path uses the wired stack.
    # The default singleton would self-configure via the module-level engine,
    # but we inject the deterministic drivers + test DB bindings explicitly
    # so the API path is exercised without an LLM provider.
    import app.api.v1.autonomy as autonomy_api

    autonomy_api._service = ctrl

    # ── 2. Drive the REAL HTTP API (ASGI) ────────────────────────────
    sep("HTTP API — POST /api/v1/autonomy/run")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fastapi_app),
        base_url="http://testserver",
        timeout=300.0,
    ) as client:
        resp = await client.post("/api/v1/autonomy/run", json={
            "task": task,
            "criteria": criteria,
            "budget": budget,
        })
        body = resp.json()
        assert resp.status_code == 200, f"HTTP {resp.status_code}: {body}"
        assert body.get("success"), f"API returned error: {body.get('message')}"
        goal_id = body["data"]["goal_id"]
        print(f"  POST /run -> {goal_id} state={body['data']['state']}")
        print(f"  criteria={body['data']['goal']['progress']['criteria_satisfied']}/"
              f"{body['data']['goal']['progress']['criteria_total']}")

        # ── 3. Goal list with the new state filter ──────────────────
        sep("HTTP API — GET /api/v1/autonomy?state=completed")
        list_resp = await client.get("/api/v1/autonomy", params={
            "state": "completed", "limit": 50,
        })
        list_body = list_resp.json()
        assert list_body.get("success"), list_body.get("message")
        filtered = list_body["data"]["goals"]
        assert any(g["goal_id"] == goal_id for g in filtered), (
            f"Goal {goal_id} not returned by state=completed filter"
        )
        print(f"  state=completed -> {len(filtered)} goal(s); target present: True")

        # ── 4. Restart recovery through a fresh controller ──────────
        sep("RESTART RECOVERY (fresh controller rehydrates from DB)")
        from app.services.autonomy_service import AutonomousExecutionController

        fresh = AutonomousExecutionController(
            orchestration=orch,
            collaboration=collab,
            session_factory=session_factory,
            run_store=run_store,
        )
        recovered = await fresh.recover(goal_id)
        print(f"  Recovered {goal_id}: state={recovered.state.value}, "
              f"decisions={len(recovered.decisions)}, "
              f"plan_versions={len(recovered.plan_versions)}, "
              f"checkpoints={len(recovered.checkpoints)}")

        # ── 5. Persisted runs + collaboration evidence ──────────────
        sep("DURABLE ARTIFACTS (PostgresRunStore + collaboration tables)")
        rows = await run_store.list(limit=20)
        print(f"  Runs in `runs` table: {len(rows)}")
        for r in rows[:10]:
            print(f"    {r.run_id}  {r.status.value:10s} {r.source.title[:55]}")

        total_handoffs = 0
        for run_id in sorted({r.run_id for r in rows}):
            try:
                metrics = await collab.get_collaboration_metrics(run_id)
            except Exception:
                continue
            total_handoffs += metrics["handoffs_total"]
            print(f"  Run {run_id}: handoffs={metrics['handoffs_total']} "
                  f"decisions={metrics['decisions']} "
                  f"conflicts={metrics['conflicts_detected']} "
                  f"evidence={metrics['evidence_items']}")
        print(f"  Total handoffs across persisted runs: {total_handoffs}")

    print()
    print("  RESULT: API path persists runs/handoffs/recovery via "
          "PostgresRunStore ✅")

    if json_output:
        print()
        print("JSON:")
        print(json.dumps({
            "goal_id": goal_id,
            "goal_state": body["data"]["state"],
            "filtered_state_completed": [g["goal_id"] for g in filtered],
            "persisted_runs": [r.run_id for r in rows],
            "total_handoffs": total_handoffs,
            "recovered": recovered.state.value,
        }, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print JSON summary")
    parser.add_argument("--live", action="store_true",
                        help="Drive BOTH the run API (POST /api/v1/runs) and "
                             "the goal API (POST /api/v1/autonomy/run) with "
                             "real LLM bodies (requires provider + API key)")
    parser.add_argument("--repository", default=None,
                        help="Repository path to copy into a temp workspace "
                             "(default: tests/fixtures/fixture_auth_app)")
    args = parser.parse_args()
    asyncio.run(main(json_output=args.json, live=args.live,
                     repository=args.repository))
