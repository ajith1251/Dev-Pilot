"""
Phase 16 Demonstration - Autonomous Execution, Goal Tracking & Dynamic Replanning.

Drives ONE autonomous goal through the AutonomousExecutionController, which
runs the bounded loop (decide -> plan/implement/test/review/gate -> evaluate)
on top of the real orchestrator:

    Autonomous Controller  = decides WHAT happens next
    Orchestrator           = executes engineering stages
    Agents                 = perform specialized work
    Quality Gate           = determines final engineering approval

After the run it prints the goal's decision timeline, plan-version history,
repair/replan budget usage, escalations, and the Phase 15 collaboration
summary (structured handoffs, decisions, conflicts) for the run(s) the
controller executed - mirroring the Phase 15 live-PostgreSQL validation.

Mode:
  * deterministic  (default) - no LLM API required. The LLM-dependent stage
    bodies are replaced by drivers (same pattern as demo_phase15) that emit
    the run state real agents would; the first test run FAILS then PASSES so
    the autonomous loop's REPAIR path, budget usage, and plan/decision
    timeline are demonstrated end-to-end against a live PostgreSQL database.
  * live           (--live)   - runs a REAL execute_run with the configured
    LLM provider. Requires DEVPILOT_LLM_PROVIDER=openai (or anthropic/gemini)
    AND OPENAI_API_KEY / ANTHROPIC_API_KEY / GEMINI_API_KEY. A disposable copy
    of the fixture repo
    is used so the run never mutates the source tree.

Database: uses TEST_DATABASE_URL when the DB name contains "test" (schema
ensured via `alembic upgrade head`). Refuses to mutate a non-test database -
falls back to in-memory persistence.

Run from the backend directory:
    python scripts/demo_phase16.py
    python scripts/demo_phase16.py --json
    python scripts/demo_phase16.py --live
"""

from __future__ import annotations

import sys

# Windows consoles default to the cp1252 codec, which cannot encode the
# unicode arrows the orchestrator logs in handoff messages ("planner →
# coding"). Reconfigure the streams so the demo output stays clean on any
# platform instead of emitting a UnicodeEncodeError traceback per log line.
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
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.orchestration import RunSource, RunSourceType, RunStatus, StageType
from app.services.autonomy_service import AutonomousExecutionController
from app.services.collaboration_service import CollaborationService
from app.services.orchestration_service import OrchestrationService


def sep(title: str) -> None:
    print()
    print("=" * 72)
    print("  " + title)
    print("=" * 72)
    print()


# ── Database selection (test-DB safe) ──────────────────────────────

def pick_database_url() -> Optional[str]:
    """Return a test-named DB URL, or None for in-memory persistence.

    Mirrors the project rule: destructive/mutating operations only ever touch
    a designated test database - never a dev/production one.
    """
    from app.config import settings

    url = settings.TEST_DATABASE_URL or settings.DATABASE_URL
    if url and "test" in url.split("/")[-1].lower():
        return url
    if url:
        print("  [warn] No test-named database available - refusing to mutate "
              "a non-test DB. Falling back to in-memory persistence.")
        return None
    print("  [warn] No DATABASE_URL configured - using in-memory persistence.")
    return None


def ensure_schema(db_url: str) -> None:
    """Run `alembic upgrade head` against the demo test database (idempotent)."""
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
        timeout=120,
        env=env,
    )
    if result.returncode != 0:
        print(f"  [warn] alembic upgrade failed (continuing in-memory): {result.stderr[-300:]}")
    else:
        print(f"  [ok] alembic upgrade head against {db_url.split('@')[-1]}")


def build_session_factory(db_url: str):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(db_url)
    return async_sessionmaker(engine, expire_on_commit=False)


def build_memory_service(db_url: str):
    """RepositoryMemoryService bound to the demo test DB.

    Keeps ALL writes - including verified-memory promotion at goal completion
    - inside the designated test database.
    """
    from app.services.repository_memory_service import RepositoryMemoryService

    svc = RepositoryMemoryService()
    svc._session_factory = build_session_factory(db_url)
    return svc


def check_live_mode() -> bool:
    """Validate that a real LLM run is actually possible (any registered
    provider except the deterministic 'fake', with its config attr set)."""
    from app.config import settings
    from app.core.exceptions import LLMProviderNotFound
    from app.llm.factory import factory as llm_factory
    from app.llm.provider_registry import get_spec

    provider = (settings.LLM_PROVIDER or "").lower()
    spec = get_spec(provider)
    if spec is None:
        print(f"  [error] --live requires a registered LLM provider. "
              f"DEVPILOT_LLM_PROVIDER='{provider}' is not registered "
              f"(available: {sorted(llm_factory._providers)}).")
        return False
    if provider == "fake":
        print("  [error] --live requires a real LLM provider; 'fake' is the "
              "deterministic in-memory fallback used by tests.")
        return False
    try:
        llm_factory.get_provider(provider)
    except LLMProviderNotFound:
        print(f"  [error] --live requires a registered LLM provider. "
              f"DEVPILOT_LLM_PROVIDER='{provider}' is not registered "
              f"(available: {sorted(llm_factory._providers)}).")
        return False
    if not spec.always_available and spec.availability_attr:
        if not getattr(settings, spec.availability_attr, None):
            print(f"  [error] --live requires {spec.availability_attr} in .env.")
            return False
    return True


# ── Deterministic model builders ───────────────────────────────────

def make_plan():
    from app.models.issues import ImplementationPlan, ImplementationStep

    return ImplementationPlan(
        summary="Add token expiration validation to AuthService",
        objective="Reject expired reset tokens",
        steps=[
            ImplementationStep(
                id="STEP-001",
                title="Validate token expiration",
                description="Check expiry before token lookup in AuthService",
                affected_areas=["auth"],
            )
        ],
        test_strategy="Run auth unit tests",
    )


def make_patch_set():
    from app.models.coding import FileChange, FileOperation, PatchSet

    return PatchSet(
        patch_id="PATCH-DEMO",
        changes=[
            FileChange(
                change_id="CHANGE-001",
                operation=FileOperation.MODIFY,
                path="auth/service.py",
                original_hash="abc123",
                new_content=(
                    "class AuthService:\n"
                    "    def login(self, token):\n"
                    "        if token.expired:\n"
                    "            raise TokenExpiredError()\n"
                    "        return self._validate(token)\n"
                ),
            )
        ],
    )


def make_test_result(passed: bool):
    from app.models.testing import (
        ExecutionStatus,
        FailureCategory,
        TestFailure,
        TestRunResult,
    )

    if passed:
        return TestRunResult(
            run_id="demo",
            workspace_id="demo-ws",
            status=ExecutionStatus.PASSED,
            commands_total=1,
            commands_passed=1,
            commands_failed=0,
            commands_skipped=0,
            tests_total=5,
            tests_passed=5,
            tests_failed=0,
            tests_skipped=0,
            failures=[],
            process_results=[],
            duration_seconds=0.5,
            summary="5 passed in 0.5s",
        )
    return TestRunResult(
        run_id="demo",
        workspace_id="demo-ws",
        status=ExecutionStatus.FAILED,
        commands_total=1,
        commands_passed=0,
        commands_failed=1,
        commands_skipped=0,
        tests_total=5,
        tests_passed=3,
        tests_failed=2,
        tests_skipped=0,
        failures=[
            TestFailure(
                failure_id="tf-demo",
                test_name="test_expired_token_rejected",
                file_path="auth/tests/test_auth.py",
                line_number=12,
                message="TokenExpiredError not raised",
                framework="pytest",
                failure_type=FailureCategory.ASSERTION_FAILURE,
            )
        ],
        process_results=[],
        duration_seconds=0.5,
        summary="2 failed, 3 passed",
    )


def make_review_report():
    from app.models.review import ReviewReport

    return ReviewReport(review_id="rv-demo-001")


def make_approved_gate():
    from app.models.review import QualityGateDecision, QualityGateResult

    return QualityGateResult(
        review_id="rv-demo-001",
        decision=QualityGateDecision.APPROVED,
        score=92.5,
        requirements_satisfied=2,
        requirements_partial=0,
        requirements_unsatisfied=0,
        verification_status="passed",
        security_status="passed",
        reason_codes=["review_passed"],
    )


def make_repair_success(attempts: int = 1):
    from app.models.repair import RepairResult, RepairSession, RepairSessionStatus

    return RepairResult(
        session=RepairSession(
            session_id="rs-demo", workspace_id="ws-demo", status=RepairSessionStatus.SUCCESS
        ),
        status=RepairSessionStatus.SUCCESS,
        stop_reason="Fixed" if attempts else "Repair deferred",
        attempts=attempts,
        remaining_failures=[],
        summary="Repaired expired-token check" if attempts else "Repair pending next iteration",
        duration_seconds=1.0,
    )


def make_repository_profile(repo: str):
    """Real deterministic repository analysis; falls back to a stub on error."""
    try:
        from app.workflows.repository_analysis import RepositoryAnalysisWorkflow

        return RepositoryAnalysisWorkflow().run(repo)
    except Exception as exc:
        print(f"  [warn] Repository analysis unavailable ({exc}); using stub profile.")
        return type("RP", (), {})()


# ── Deterministic stage drivers (emit real run state) ──────────────
# Only the LLM-dependent stage bodies are replaced; the orchestrator's
# handoff / decision / conflict / promotion wiring runs for real. The first
# test run FAILS then PASSES so the autonomous REPAIR path is demonstrated.

def install_drivers(orch: OrchestrationService, fail_then_pass: bool = True) -> None:
    calls = {"n": 0}

    async def _planning(run, *a, **kw):
        run.plan = make_plan()
        run.current_stage = StageType.PLANNING
        return True

    async def _coding(run, *a, **kw):
        run.patch_set = make_patch_set()
        run.current_stage = StageType.CODING
        return True

    async def _pv(run, *a, **kw):
        run.current_stage = StageType.VALIDATING_PATCH
        return True

    async def _pa(run, *a, **kw):
        run.patch_result = type("PR", (), {
            "changed_symbols": ["auth/service.py::AuthService.login"],
        })()
        run.current_stage = StageType.APPLYING_PATCH
        return True

    async def _testing(run, *a, **kw):
        calls["n"] += 1
        run.current_stage = StageType.TESTING
        if fail_then_pass and calls["n"] == 1:
            run.test_result = make_test_result(passed=False)
            return False
        run.test_result = make_test_result(passed=True)
        return True

    async def _repair(run, *a, **kw):
        # attempts=0 -> execute_run skips the internal retest, so the first
        # iteration's evidence keeps test_status='failed'. The autonomous
        # controller then decides REPAIR for the next iteration (demonstrating
        # the autonomy-level repair path + repair budget usage).
        run.repair_result = make_repair_success(attempts=0)
        run.current_stage = StageType.REPAIRING
        return True

    async def _review(run, *a, **kw):
        run.review_report = make_review_report()
        run.current_stage = StageType.REVIEWING
        return True

    async def _qg(run, *a, **kw):
        run.quality_gate_result = make_approved_gate()
        run.status = RunStatus.APPROVED
        run.current_stage = StageType.QUALITY_GATE
        return True

    # Instance-level shadowing - execute_run calls self._stage_*()
    orch._stage_planning = _planning
    orch._stage_coding = _coding
    orch._stage_patch_validation = _pv
    orch._stage_patch_application = _pa
    orch._stage_testing = _testing
    orch._stage_repair = _repair
    orch._stage_review = _review
    orch._stage_quality_gate = _qg


# ── Printing helpers ───────────────────────────────────────────────

def print_decision(d) -> None:
    print(f"    [{d.iteration}] {d.action.value.upper():9s} {d.reason_code}")
    print(f"         {d.rationale[:130]}")


def print_plan_version(p) -> None:
    print(f"    v{p.version} [{p.status}] {p.step_count} step(s): {p.plan_summary[:80]}")
    if p.superseded_reason:
        print(f"         superseded: {p.superseded_reason[:100]}")


def _handoff_line(h) -> str:
    return f"         {h.from_agent} -> {h.to_agent}  [{h.status.value}]  {h.summary[:60]}"


# ── The autonomous run ─────────────────────────────────────────────

async def run_autonomous_goal(
    orch: OrchestrationService,
    collab: CollaborationService,
    ctrl: AutonomousExecutionController,
    repo: str,
    task: str,
    criteria: Optional[list],
) -> dict:
    print(f"\n  Creating goal: '{task[:80]}'")
    state = await ctrl.create_goal(
        task=task,
        repository=repo,
        criteria_texts=criteria,
    )
    goal_id = state.goal_id
    print(f"  Goal ID: {goal_id}")
    print(f"  Acceptance criteria ({len(state.goal.acceptance_criteria)}):")
    for c in state.goal.acceptance_criteria:
        print(f"    - {c.description[:90]}  [{c.verification}]")

    sep("[AUTONOMOUS LOOP]")
    print("  Running the bounded loop (decide -> implement -> evaluate)...")
    print("  Deterministic evidence is authoritative; LLM claims can never")
    print("  override test results, patch evidence, or the quality gate.")
    print()
    final = await ctrl.start(goal_id)

    print(f"  >>> FINAL GOAL STATE: {final.state.value}")
    print(f"      Criteria: {final.goal.progress.criteria_satisfied}/"
          f"{final.goal.progress.criteria_total} satisfied")
    print()

    # ── Decision timeline ─────────────────────────────────────────
    print("  Decision timeline:")
    for d in final.decisions:
        print_decision(d)

    # ── Plan versions ─────────────────────────────────────────────
    if final.plan_versions:
        print()
        print(f"  Plan versions ({len(final.plan_versions)}):")
        for p in final.plan_versions:
            print_plan_version(p)

    # ── Budget usage ──────────────────────────────────────────────
    print()
    print("  Budget usage:")
    limits = final.budget.limits()
    usage = final.budget.usage()
    for key in ("iterations", "repairs", "replans", "agent_calls",
                "llm_calls", "test_runs", "files_changed"):
        limit = limits.get(f"max_{key}", 0)
        used = usage.get(key, 0)
        marker = " [EXHAUSTED]" if limit and used >= limit else ""
        print(f"    {key:14s} {used}/{limit}{marker}")

    # ── Escalations ───────────────────────────────────────────────
    if final.escalations:
        print()
        print(f"  Escalations ({len(final.escalations)}):")
        for e in final.escalations:
            print(f"    [{e.reason.value}] {e.what_happened[:90]}")
            print(f"         needed: {e.needed_input[:90]}")

    # ── Per-iteration evidence ────────────────────────────────────
    print()
    print(f"  Iteration evidence ({len(final.evidence_history)}):")
    for e in final.evidence_history:
        print(f"    iter {e.iteration}  run {e.run_id}  test={e.test_status} "
              f"gate={e.quality_gate_decision}  failures={e.tests_failed} "
              f"changed_files={len(e.changed_files)}")

    # ── Collaboration summary (Phase 15 wiring) ───────────────────
    sep("COLLABORATION SUMMARY (per executed run)")
    run_ids = [e.run_id for e in final.evidence_history if e.run_id]
    total_handoffs = 0
    for run_id in sorted(set(run_ids)):
        metrics = await collab.get_collaboration_metrics(run_id)
        total_handoffs += metrics["handoffs_total"]
        print(f"  Run {run_id}:")
        print(f"    Handoffs:           {metrics['handoffs_total']}")
        print(f"    Handoffs validated: {metrics['handoffs_validated']}")
        print(f"    Decisions:          {metrics['decisions']}")
        print(f"    Conflicts detected: {metrics['conflicts_detected']}")
        print(f"    Conflicts resolved: {metrics['conflicts_resolved']}")
        print(f"    Evidence items:     {metrics['evidence_items']}")
        handoffs = await collab.list_handoffs(run_id)
        for h in handoffs:
            print(_handoff_line(h))
        print()

    # ── Restart recovery (fresh service rehydrates from DB) ───────
    if ctrl._factory is not None:
        try:
            fresh_ctrl = AutonomousExecutionController(
                orchestration=orch,
                collaboration=collab,
                session_factory=ctrl._factory,
            )
            recovered = await fresh_ctrl.recover(goal_id)
            print(f"  Restart recovery: goal {goal_id} rehydrated -> state "
                  f"{recovered.state.value}, {len(recovered.decisions)} decision(s), "
                  f"{len(recovered.plan_versions)} plan version(s)")
        except KeyError:
            print(f"  [warn] Restart recovery skipped - goal {goal_id} not found "
                  "in the database (schema/DB unavailable).")
    else:
        print("  [warn] In-memory persistence - restart recovery skipped "
              "(no test-named database available).")

    return {
        "goal_id": goal_id,
        "state": final.state.value,
        "criteria_satisfied": final.goal.progress.criteria_satisfied,
        "criteria_total": final.goal.progress.criteria_total,
        "iterations_used": final.budget.iterations_used,
        "repairs_used": final.budget.repairs_used,
        "replans_used": final.budget.replans_used,
        "run_ids": run_ids,
        "handoffs_total": total_handoffs,
        "decisions": [
            {"iteration": d.iteration, "action": d.action.value,
             "reason_code": d.reason_code, "rationale": d.rationale[:200]}
            for d in final.decisions
        ],
        "plan_versions": [
            {"version": p.version, "status": p.status, "summary": p.plan_summary}
            for p in final.plan_versions
        ],
        "escalations": [
            {"reason": e.reason.value, "what_happened": e.what_happened[:200]}
            for e in final.escalations
        ],
    }


async def main(json_output: bool = False, live: bool = False,
               task: Optional[str] = None, repository: Optional[str] = None) -> None:
    fixture = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "fixture_auth_app"
    task = task or "Fix password reset token expiration so expired tokens are rejected"
    criteria = ["Expired reset tokens must be rejected", "Valid reset tokens must be accepted"]

    sep("PHASE 16 DEMO - AUTONOMOUS EXECUTION, GOAL TRACKING & DYNAMIC REPLANNING")
    print(f"  Repository: {Path(repository or fixture).name}")
    print(f"  Mode:       {'LIVE (real LLM)' if live else 'deterministic (no LLM API)'}")
    print()

    if live and not check_live_mode():
        return

    # ── Database ──────────────────────────────────────────────────
    db_url = pick_database_url()
    if db_url:
        ensure_schema(db_url)
        session_factory = build_session_factory(db_url)
        collab = CollaborationService(
            session_factory=session_factory,
            memory_service=build_memory_service(db_url),
        )
    else:
        session_factory = None
        collab = CollaborationService()

    # Run records are now DURABLE: the orchestrator is bound to a
    # PostgresRunStore using the test DB, so the `runs` table is populated
    # alongside the collaboration evidence + goal records (full audit trail).
    if session_factory is not None:
        from app.services.postgres_run_store import PostgresRunStore

        run_store = PostgresRunStore()
        run_store._session_factory = session_factory
        orch = OrchestrationService(run_store=run_store)
    else:
        orch = OrchestrationService()
    orch._collaboration = collab
    orch._get_collaboration = lambda: collab

    ctrl = AutonomousExecutionController(
        orchestration=orch,
        collaboration=collab,
        session_factory=session_factory,
        run_store=run_store if session_factory is not None else None,
    )

    # Live mode uses a disposable copy so the run never mutates the fixture.
    repo = repository or str(fixture)
    tmp = None
    if live:
        tmp = tempfile.mkdtemp(prefix="devpilot-demo-")
        repo = str(Path(tmp) / Path(repository or fixture).name)
        shutil.copytree(str(fixture), repo, dirs_exist_ok=True)
        print(f"  Live workspace: {repo}")

    if not live:
        # Deterministic mode: first test run fails, then passes, so the
        # autonomous loop exercises the REPAIR path and budget routing.
        install_drivers(orch, fail_then_pass=True)

    # ── One real autonomous run ───────────────────────────────────
    summary = await run_autonomous_goal(
        orch, collab, ctrl, repo, task, criteria,
    )

    # ── Persisted runs (durable audit trail) ─────────────────────
    if session_factory is not None:
        try:
            from app.services.postgres_run_store import PostgresRunStore
            probe = PostgresRunStore()
            probe._session_factory = session_factory
            rows = await probe.list(limit=20)
            print()
            print(f"  Persisted runs in test DB ({len(rows)}):")
            for r in rows:
                print(f"    {r.run_id}  {r.status.value:10s} {r.source.title[:60]}")
        except Exception as exc:
            print(f"  [warn] Could not list persisted runs: {exc}")

    # ── Final summary ─────────────────────────────────────────────
    sep("GOAL SUMMARY")
    print(f"  Goal:           {summary['goal_id']}")
    print(f"  Final state:    {summary['state']}")
    print(f"  Criteria:       {summary['criteria_satisfied']}/{summary['criteria_total']}")
    print(f"  Iterations:     {summary['iterations_used']}")
    print(f"  Repairs:        {summary['repairs_used']}")
    print(f"  Replans:        {summary['replans_used']}")
    print(f"  Handoffs:       {summary['handoffs_total']}")
    print()
    print("  Key invariant: deterministic evidence outranks LLM claims.")
    print("  The loop terminates when provably complete (COMPLETED), stuck")
    print("  (escalated for human input), or budget-exhausted (STOPPED).")

    # Clean up the disposable live-mode workspace.
    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)
        print("\n  Live workspace cleaned up.")

    if json_output:
        print()
        print("JSON:")
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Phase 16 autonomous execution demo")
    parser.add_argument("--json", action="store_true", help="Emit JSON summary")
    parser.add_argument("--live", action="store_true",
                        help="Run a REAL execute_run with the configured LLM provider")
    parser.add_argument("--task", help="Override the autonomous task")
    parser.add_argument("--repository", help="Override the target repository path")
    args = parser.parse_args()
    asyncio.run(main(json_output=args.json, live=args.live,
                     task=args.task, repository=args.repository))
