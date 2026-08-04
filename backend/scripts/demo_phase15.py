"""
Phase 15 Demonstration — Multi-Agent Collaboration & Shared Run Intelligence.

Shows the full collaboration lifecycle with structured handoffs, decision
records, conflict detection, durable PostgreSQL persistence, restart recovery,
and a repair-loop scenario:

    Run A (happy path):
        Planner -> Coding -> Testing -> Reviewer -> Quality Gate

    Run B (repair loop):
        Planner -> Coding -> Testing(FAIL) -> Repair -> Testing(PASS)
        -> Reviewer -> Quality Gate

Mode:
  * deterministic  (default) — no LLM API required. The LLM-dependent stage
    bodies are replaced by drivers that emit the same run state real agents
    would, so the orchestrator's real handoff / decision / conflict /
    promotion wiring runs against a live PostgreSQL database.
  * live           (--live)   — runs a REAL execute_run using the configured
    LLM provider. Requires DEVPILOT_LLM_PROVIDER=openai (or anthropic/gemini)
    AND OPENAI_API_KEY / ANTHROPIC_API_KEY / GEMINI_API_KEY. A disposable copy
    of the fixture repo
    is used so the run never mutates the source tree.

Note: in both modes the acquisition / analysis / task-analysis / retrieval
stages are pre-populated (same pattern as the orchestrator test suite) so the
demo focuses on the collaboration layer — planner -> coding -> testing ->
repair -> reviewer -> quality gate. Memory promotion is bound to the same
test database as the collaboration records.

Database: uses TEST_DATABASE_URL when the DB name contains "test" (schema
ensured via `alembic upgrade head`). Refuses to mutate a non-test database —
falls back to in-memory persistence.

Run from the backend directory:
    python scripts/demo_phase15.py
    python scripts/demo_phase15.py --json
    python scripts/demo_phase15.py --live
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.orchestration import RunSource, RunSourceType, RunStatus, StageType
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
    a designated test database — never a dev/production one.
    """
    from app.config import settings

    url = settings.TEST_DATABASE_URL or settings.DATABASE_URL
    if url and "test" in url.split("/")[-1].lower():
        return url
    if url:
        print("  [warn] No test-named database available — refusing to mutate "
              "a non-test DB. Falling back to in-memory persistence.")
        return None
    print("  [warn] No DATABASE_URL configured — using in-memory persistence.")
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

    The default service reads settings.DATABASE_URL (the dev DB); binding its
    session factory to the demo's test DB keeps ALL writes — including memory
    promotion — inside the designated test database.
    """
    from app.services.repository_memory_service import RepositoryMemoryService

    svc = RepositoryMemoryService()
    svc._session_factory = build_session_factory(db_url)
    return svc


def check_live_mode() -> bool:
    """Validate that a real LLM run is actually possible."""
    from app.config import settings
    from app.core.exceptions import LLMProviderNotFound
    from app.llm.factory import factory as llm_factory

    provider = (settings.LLM_PROVIDER or "").lower()
    real = ("openai", "anthropic", "gemini")
    if provider not in real:
        print(f"  [error] --live requires a real LLM provider; "
              f"DEVPILOT_LLM_PROVIDER='{provider}' is not "
              f"{'/'.join(real)} "
              f"(available: {sorted(llm_factory._providers)}).")
        print("          Set DEVPILOT_LLM_PROVIDER=openai (or anthropic/gemini) "
              "and add OPENAI_API_KEY / ANTHROPIC_API_KEY / GEMINI_API_KEY to .env.")
        return False
    try:
        llm_factory.get_provider(provider)
    except LLMProviderNotFound:
        print(f"  [error] --live requires a registered LLM provider. "
              f"DEVPILOT_LLM_PROVIDER='{provider}' is not registered "
              f"(available: {sorted(llm_factory._providers)}).")
        return False
    key = {
        "openai": settings.OPENAI_API_KEY,
        "anthropic": settings.ANTHROPIC_API_KEY,
        "gemini": settings.GEMINI_API_KEY,
    }.get(provider)
    if not key:
        print(f"  [error] --live requires {provider.upper()}_API_KEY in .env.")
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


def make_requirements():
    from app.models.issues import Requirement, StructuredRequirements

    return StructuredRequirements(
        objective="Fix password reset token expiration",
        requirements=[
            Requirement(description="Expired reset tokens must be rejected"),
            Requirement(description="Valid reset tokens must be accepted"),
        ],
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


def make_repair_success():
    from app.models.repair import RepairResult, RepairSession, RepairSessionStatus

    return RepairResult(
        session=RepairSession(
            session_id="rs-demo", workspace_id="ws-demo", status=RepairSessionStatus.SUCCESS
        ),
        status=RepairSessionStatus.SUCCESS,
        stop_reason="Fixed",
        attempts=1,
        remaining_failures=[],
        summary="Repaired expired-token check",
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
# handoff / decision / conflict / promotion wiring runs for real.

def install_drivers(orch: OrchestrationService, fail_then_pass: bool) -> None:
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
        run.repair_result = make_repair_success()
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

    # Instance-level shadowing — execute_run calls self._stage_*()
    orch._stage_planning = _planning
    orch._stage_coding = _coding
    orch._stage_patch_validation = _pv
    orch._stage_patch_application = _pa
    orch._stage_testing = _testing
    orch._stage_repair = _repair
    orch._stage_review = _review
    orch._stage_quality_gate = _qg


async def create_run(orch: OrchestrationService, repo: str, title: str,
                      live: bool = False):
    """Create a run pre-populated so the state machine can start at planning.

    Note: execute_run may only transition initializing -> acquiring_repository.
    Pre-populating requirements + repository_profile + retrieved_context skips
    the acquisition/analysis/task-analysis/retrieval stages (same pattern the
    orchestrator test suite uses), letting the demo focus on the collaboration
    layer (planner -> coding -> testing -> repair -> reviewer -> gate).
    """
    source = RunSource(
        source_type=RunSourceType.USER_TASK,
        title=title,
        repository_path=repo,
    )
    run = await orch.create_run(source)
    run.repository_path = repo
    run.repository_profile = make_repository_profile(repo)
    run.requirements = make_requirements()
    from app.models.rag import RetrievedContext, RetrievalQuery

    if not live:
        # Deterministic mode: keep the stub (retrieval stage is skipped).
        # In live mode leave retrieved_context unset so the REAL retrieval
        # stage runs after planning: a zero-item stub makes the coding
        # agent's LLM return INSUFFICIENT_CONTEXT (it must not hallucinate
        # file contents), surfacing as 'No patch produced'.
        run.retrieved_context = RetrievedContext(query=RetrievalQuery(text=title))
    # Advance past the pre-populated stages so the first REAL stage
    # (planning) has a valid transition (ANALYZING_TASK -> PLANNING).
    # The strict RunStateMachine only allows initializing ->
    # acquiring_repository, so without this a --live run fails immediately.
    run.current_stage = StageType.ANALYZING_TASK
    await orch._store.update(run)
    return run


def _handoff_line(h) -> str:
    return f"         {h.from_agent} -> {h.to_agent}  [{h.status.value}]  {h.summary[:60]}"


async def run_pipeline(
    orch: OrchestrationService,
    collab: CollaborationService,
    repo: str,
    title: str,
    label: str,
    live: bool = False,
) -> dict:
    print(f"\n  --- {label} ---")
    run = await create_run(orch, repo, title, live=live)
    print(f"  Run: {run.run_id}   ({title})")
    result = await orch.execute_run(run.run_id, workspace_root=repo)
    print(f"  Final status: {result.status.value}")

    handoffs = await collab.list_handoffs(run.run_id)
    decisions = await collab.list_decisions(run.run_id)
    conflicts = await collab.list_conflicts(run.run_id)

    print(f"  Handoffs ({len(handoffs)}):")
    for h in handoffs:
        print(_handoff_line(h))
    print(f"  Decisions ({len(decisions)}):")
    for d in decisions:
        print(f"         [{d.decision_type.value}] {d.statement[:70]}  (by {d.made_by})")
    if conflicts:
        print(f"  Conflicts ({len(conflicts)}):")
        for c in conflicts:
            print(f"         {c.description[:80]}  [{c.resolution.value}]")
    else:
        print("  Conflicts: 0")

    return {
        "run_id": run.run_id,
        "status": result.status.value,
        "handoffs": handoffs,
        "decisions": decisions,
        "conflicts": conflicts,
    }


async def main(json_output: bool = False, live: bool = False) -> None:
    fixture = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "fixture_auth_app"

    sep("PHASE 15 DEMO — MULTI-AGENT COLLABORATION & SHARED RUN INTELLIGENCE")
    print(f"  Repository: {fixture.name}")
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
        collab = CollaborationService()

    # Run records stay ephemeral (OrchestrationService defaults to an
    # in-memory run store) — the durable demo subject is the collaboration
    # evidence (handoffs/decisions/conflicts) written to the test DB. This
    # keeps ALL persistent writes inside the designated test database.
    orch = OrchestrationService()
    orch._collaboration = collab
    orch._get_collaboration = lambda: collab

    # Live mode uses a disposable copy so the run never mutates the fixture.
    repo = str(fixture)
    tmp = None
    if live:
        tmp = tempfile.mkdtemp(prefix="devpilot-demo-")
        repo = str(Path(tmp) / fixture.name)
        shutil.copytree(str(fixture), repo, dirs_exist_ok=True)
        print(f"  Live workspace: {repo}")

    if not live:
        install_drivers(orch, fail_then_pass=False)

    # ── Run A: happy path ─────────────────────────────────────────
    sep("[RUN A] HAPPY PATH — Planner -> Coding -> Testing -> Reviewer -> Gate")
    a = await run_pipeline(orch, collab, repo, "Fix password reset token expiration",
                           "Run A (all tests pass)", live=live)
    run_a_id = a["run_id"]

    # ── Run B: repair loop ────────────────────────────────────────
    sep("[RUN B] REPAIR LOOP — Tests FAIL, repair, retest PASSES")
    install_drivers(orch, fail_then_pass=True)
    b = await run_pipeline(orch, collab, repo, "Fix expired token check (repair needed)",
                           "Run B (first test run fails)", live=live)
    run_b_id = b["run_id"]

    # ── Restart recovery (fresh service rehydrates from DB) ───────
    sep("[RESTART RECOVERY]")
    print("  Creating a fresh CollaborationService (simulating a backend restart)...")
    if db_url:
        fresh = CollaborationService(session_factory=build_session_factory(db_url))
    else:
        fresh = CollaborationService()
    await fresh.recover(run_a_id)
    recovered = await fresh.list_handoffs(run_a_id)
    print(f"  Run A recovered handoffs: {len(recovered)}")
    for h in recovered[:4]:
        print(_handoff_line(h))
    recovered_b = await fresh.list_handoffs(run_b_id)
    print(f"  Run B recovered handoffs: {len(recovered_b)}")

    # ── Conflict detection demonstration (spec Demonstration E) ───
    sep("[CONFLICT DETECTION]")
    print("  Agent claims 'Tests passed: all green' while test evidence says FAILED...")
    bogus = await collab.create_handoff(
        run_id=run_b_id,
        from_agent="coding",
        to_agent="testing",
        stage="testing",
        summary="Tests passed: all green",
        decisions=["Implementation complete"],
    )
    detected = []
    if bogus:
        detected = await collab.detect_conflicts(run_id=run_b_id, handoff=bogus, test_passed=False)
    if detected:
        for c in detected:
            print(f"  -> Conflict: {c.description[:90]}")
            print(f"     Resolution: {c.resolution.value} (deterministic evidence wins)")
        print(f"     Handoff downgraded to: {bogus.status.value if bogus else 'n/a'}")
    else:
        print("  (no conflict scenario applicable — skip)")

    # ── Summary ───────────────────────────────────────────────────
    sep("COLLABORATION SUMMARY")
    summaries = {}
    for name, data in (("Run A", a), ("Run B", b)):
        metrics = await collab.get_collaboration_metrics(data["run_id"])
        summaries[name] = metrics
        print(f"  {name}: {data['run_id']}   Status: {data['status']}")
        print(f"    Handoffs:           {metrics['handoffs_total']}")
        print(f"    Handoffs validated: {metrics['handoffs_validated']}")
        print(f"    Decisions:          {metrics['decisions']}")
        print(f"    Conflicts detected: {metrics['conflicts_detected']}")
        print(f"    Conflicts resolved: {metrics['conflicts_resolved']}")
        print(f"    Evidence items:     {metrics['evidence_items']}")
        print()

    print("  Timeline (Run A):")
    for from_agent in ("planner", "coding", "testing", "repair", "reviewer"):
        print(f"    {from_agent.capitalize()}")
        for h in a["handoffs"]:
            if h.from_agent == from_agent:
                print(f"      -> {h.to_agent}")
    print()
    print("  Key invariant: agents share structured engineering evidence —")
    print("  never chain-of-thought. Deterministic evidence outranks LLM claims.")

    # Clean up the disposable live-mode workspace.
    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)
        print("\n  Live workspace cleaned up.")

    if json_output:
        print()
        print("JSON:")
        print(json.dumps({
            "run_a": {
                "run_id": a["run_id"],
                "status": a["status"],
                "metrics": summaries["Run A"],
                "handoffs": [
                    {"from": h.from_agent, "to": h.to_agent, "summary": h.summary,
                     "status": h.status.value}
                    for h in a["handoffs"]
                ],
                "decisions": [
                    {"type": d.decision_type.value, "statement": d.statement, "made_by": d.made_by}
                    for d in a["decisions"]
                ],
                "conflicts": [
                    {"description": c.description, "resolution": c.resolution.value}
                    for c in a["conflicts"]
                ],
            },
            "run_b": {
                "run_id": b["run_id"],
                "status": b["status"],
                "metrics": summaries["Run B"],
                "handoffs": [
                    {"from": h.from_agent, "to": h.to_agent, "summary": h.summary,
                     "status": h.status.value}
                    for h in b["handoffs"]
                ],
                "decisions": [
                    {"type": d.decision_type.value, "statement": d.statement, "made_by": d.made_by}
                    for d in b["decisions"]
                ],
                "conflicts": [
                    {"description": c.description, "resolution": c.resolution.value}
                    for c in b["conflicts"]
                ],
            },
            "recovered_run_a_handoffs": [
                {"from": h.from_agent, "to": h.to_agent, "status": h.status.value}
                for h in recovered
            ],
        }, indent=2))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Phase 15 collaboration demo")
    parser.add_argument("--json", action="store_true", help="Emit JSON summary")
    parser.add_argument("--live", action="store_true",
                        help="Run a REAL execute_run with the configured LLM provider")
    args = parser.parse_args()
    asyncio.run(main(json_output=args.json, live=args.live))
