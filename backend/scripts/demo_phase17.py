"""
Phase 17 Demonstration — Collaborative Reasoning & Evidence Consensus.

Shows the full reasoning lifecycle above the Phase 15 collaboration layer:

    Run A (agreement):    planner + coding + testing + reviewer agree →
                          AGREED consensus, HIGH confidence, notebook.
    Run B (conflict):     coding claims success but tests FAIL →
                          CLAIM_VS_TEST contradiction (deterministic_wins),
                          CONFLICTED consensus.
    Run C (repair):       repair resolves the conflict → RESOLVED consensus.
    Reviewer consensus:   the reviewer context carries shared consensus notes.
    Autonomy replan:      consensus topics enrich the REPLAN rationale.
    Restart recovery:     fresh engine rehydrates the notebook from PostgreSQL.

Mode:
  * deterministic (default) — no LLM API required. The LLM-dependent stage
    bodies are replaced by drivers that emit the same run state real agents
    would, so the orchestrator's real handoff / decision / reasoning wiring
    runs against a live PostgreSQL database (migration 010).
  * live (--live) — runs a REAL execute_run using the configured LLM
    provider. Requires DEVPILOT_LLM_PROVIDER=openai (or anthropic/gemini)
    AND OPENAI_API_KEY / ANTHROPIC_API_KEY / GEMINI_API_KEY.

Security invariant (unchanged): only evidence, confidence, decisions and
consensus are exposed — never chain-of-thought.

Database: uses TEST_DATABASE_URL when the DB name contains "test" (schema
ensured via `alembic upgrade head`). Refuses to mutate a non-test database —
falls back to in-memory persistence.

Run from the backend directory:
    python scripts/demo_phase17.py
    python scripts/demo_phase17.py --json
    python scripts/demo_phase17.py --live
"""

from __future__ import annotations

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
from app.services.reasoning_service import CollaborativeReasoningEngine


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
    from app.services.repository_memory_service import RepositoryMemoryService

    svc = RepositoryMemoryService()
    svc._session_factory = build_session_factory(db_url)
    return svc


def check_live_mode() -> bool:
    from app.config import settings
    from app.core.exceptions import LLMProviderNotFound
    from app.llm.factory import factory as llm_factory

    provider = (settings.LLM_PROVIDER or "").lower()
    real = ("openai", "anthropic", "gemini", "openrouter")
    if provider not in real:
        print(f"  [error] --live requires a real LLM provider; "
              f"DEVPILOT_LLM_PROVIDER='{provider}' is not "
              f"{'/'.join(real)} "
              f"(available: {sorted(llm_factory._providers)}).")
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
        "openrouter": settings.OPENROUTER_API_KEY,
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


def make_patch_set():
    from app.models.coding import FileChange, FileOperation, PatchSet

    return PatchSet(
        patch_id="PATCH-DEMO17",
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
            run_id="demo17", workspace_id="demo17-ws",
            status=ExecutionStatus.PASSED,
            commands_total=1, commands_passed=1, commands_failed=0,
            commands_skipped=0, tests_total=5, tests_passed=5,
            tests_failed=0, tests_skipped=0, failures=[], process_results=[],
            duration_seconds=0.5, summary="5 passed in 0.5s",
        )
    return TestRunResult(
        run_id="demo17", workspace_id="demo17-ws",
        status=ExecutionStatus.FAILED,
        commands_total=1, commands_passed=0, commands_failed=1,
        commands_skipped=0, tests_total=5, tests_passed=3,
        tests_failed=2, tests_skipped=0,
        failures=[
            TestFailure(
                failure_id="tf-demo17",
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
        summary="2 failed in 0.5s",
    )


def make_repair_success(attempts: int = 1):
    from app.models.repair import (
        RepairAttempt,
        RepairResult,
        RepairSession,
        RepairSessionStatus,
    )

    return RepairResult(
        session=RepairSession(
            session_id="RS-DEMO17",
            test_command="pytest auth/tests",
            attempts=[RepairAttempt(attempt_number=1, changes=[], errors=[])],
        ),
        status=RepairSessionStatus.SUCCESS,
        attempts=attempts,
        stop_reason="Fixed expired-token check",
    )


def make_review_report():
    from app.models.review import ReviewReport

    return ReviewReport(
        review_id="REV-DEMO17",
        workspace_id="demo17-ws",
        requirement_coverage=[],
        findings=[],
        summary="Patch matches plan; no blocking findings",
        verdict="approved",
    )


def make_approved_gate():
    from app.models.review import QualityGateDecision, QualityGateResult

    return QualityGateResult(
        review_id="REV-DEMO17",
        decision=QualityGateDecision.APPROVED,
        blocking_findings=[],
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


async def make_repository_profile(repo: str):
    """Real deterministic repository analysis; falls back to a stub on error.

    Mirrors autonomy_service._run_iteration: awaits the workflow and extracts
    the RepositoryProfile, so the durable store can round-trip it.
    """
    try:
        from app.models.profile import RepositoryProfile
        from app.workflows.repository_analysis import RepositoryAnalysisWorkflow

        analysis_state = await RepositoryAnalysisWorkflow().run(repo)
        profile = getattr(analysis_state, "profile", None)
        if profile is not None:
            return profile
        return RepositoryProfile(name=Path(repo).name or "repository")
    except Exception as exc:
        print(f"  [warn] Repository analysis unavailable ({exc}); using stub profile.")
        from app.models.profile import RepositoryProfile

        return RepositoryProfile(name=Path(repo).name or "repository")


# ── Deterministic stage drivers ────────────────────────────────────
# Only the LLM-dependent stage bodies are replaced; the orchestrator's
# handoff / decision / conflict / reasoning wiring runs for real.


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

    orch._stage_planning = _planning
    orch._stage_coding = _coding
    orch._stage_patch_validation = _pv
    orch._stage_patch_application = _pa
    orch._stage_testing = _testing
    orch._stage_repair = _repair
    orch._stage_review = _review
    orch._stage_quality_gate = _qg


# ── Reasoning demonstrations ───────────────────────────────────────


async def demo_consensus(
    reasoning: CollaborativeReasoningEngine,
    orch: OrchestrationService,
    collab: CollaborationService,
    repo: str,
    title: str,
    fail_then_pass: bool,
    live: bool = False,
) -> dict:
    """Run one real run and analyze its shared evidence.

    Mirrors demo_phase15's create_run pattern: pre-populates
    repository_profile + requirements + retrieved_context so the strict
    state machine can start at planning (skipping acquisition/analysis).
    """
    source = RunSource(
        source_type=RunSourceType.USER_TASK,
        title=title,
        repository_path=repo,
    )
    run = await orch.create_run(source)
    run.repository_path = repo
    run.repository_profile = await make_repository_profile(repo)
    run.requirements = make_requirements()
    from app.models.rag import RetrievedContext, RetrievalQuery

    if not live:
        # Deterministic mode: keep the stub (retrieval stage is skipped).
        # In live mode leave retrieved_context unset so the REAL retrieval
        # stage runs after planning: the coding agent must see actual
        # repository code — a zero-item stub makes Gemini return
        # INSUFFICIENT_CONTEXT (the prompt forbids hallucinating file
        # contents), which surfaces as 'No patch produced'.
        run.retrieved_context = RetrievedContext(query=RetrievalQuery(text=title))
    # Advance past the pre-populated stages so the first REAL stage
    # (planning) has a valid transition (ANALYZING_TASK -> PLANNING).
    # The strict RunStateMachine only allows initializing ->
    # acquiring_repository, so without this a live run (real stage
    # methods that enforce transitions) fails immediately. This mirrors
    # autonomy_service._run_iteration (Session 4 fix). Deterministic mode
    # masked it because install_drivers shadows the stage methods and
    # skips transition validation.
    run.current_stage = StageType.ANALYZING_TASK
    # Persist the pre-populated state BEFORE execute_run: with a durable
    # store execute_run re-hydrates the run, so in-memory-only edits would
    # leave it stuck at `initializing` (same pattern as demo_phase15).
    await orch._store.update(run)
    if not live:
        install_drivers(orch, fail_then_pass=fail_then_pass)
    result = await orch.execute_run(run.run_id, workspace_root=repo)
    run = await orch._store.get(run.run_id)
    if run is None:
        raise RuntimeError(f"Run {run.run_id} not found after execute_run")

    outcome = await reasoning.analyze_run(run)
    return {
        "run_id": run.run_id,
        "run_status": run.status.value,
        "consensus": outcome.get("consensus", []),
        "contradictions": outcome.get("contradictions", []),
        "notebook": outcome.get("notebook"),
    }


def print_consensus_summary(title: str, demo: dict) -> None:
    sep(title)
    print(f"  Run:    {demo['run_id']}  ({demo['run_status']})")
    print(f"  Consensus records: {len(demo['consensus'])}")
    for c in demo["consensus"]:
        print(f"    [{c.topic}] {c.status.value.upper()}  confidence={c.confidence.tier.value}"
              f" ({round(c.confidence.value, 2)})")
        print(f"      decision: {c.final_decision[:120]}")
        if c.supporting_evidence:
            print(f"      supporting: {len(c.supporting_evidence)} ref(s)")
        if c.conflicting_evidence:
            print(f"      conflicting: {len(c.conflicting_evidence)} ref(s)")
    print(f"  Contradictions: {len(demo['contradictions'])}")
    for c in demo["contradictions"]:
        print(f"    [{c.kind.value}] {c.description[:120]}")
        print(f"      resolution: {c.resolution}")
        if c.deterministic_evidence:
            print(f"      deterministic: {c.deterministic_evidence.type.value} "
                  f"({c.deterministic_evidence.reference[:60]})")
    nb = demo["notebook"]
    if nb is not None:
        print(f"  Notebook: {nb.notebook_id}  task='{nb.task[:60]}'")
        print(f"    accepted={len(nb.accepted_decisions)} rejected={len(nb.rejected_decisions)} "
              f"conflicts={len(nb.conflicts)} resolved={len(nb.resolved_conflicts)} "
              f"consensus={len(nb.consensus)} timeline={len(nb.timeline)}")


async def demo_autonomy_consensus(
    orch: OrchestrationService,
    collab: CollaborationService,
    ctrl: AutonomousExecutionController,
    reasoning: CollaborativeReasoningEngine,
    repo: str,
) -> dict:
    """Autonomous loop where consensus topics enrich the REPLAN rationale."""
    print()
    print("  Creating autonomous goal with failing-first test evidence...")
    state = await ctrl.create_goal(
        task="Fix password reset token expiration so expired tokens are rejected",
        repository=repo,
        criteria_texts=[
            "Expired reset tokens must be rejected",
            "Valid reset tokens must be accepted",
        ],
    )
    goal_id = state.goal_id
    final = await ctrl.start(goal_id)
    print(f"  >>> FINAL GOAL STATE: {final.state.value}  "
          f"(criteria {final.goal.progress.criteria_satisfied}/"
          f"{final.goal.progress.criteria_total})")
    print()
    print("  Decision timeline (REPLAN rationale may carry consensus topics):")
    for d in final.decisions:
        print(f"    [{d.iteration}] {d.action.value.upper():9s} {d.reason_code}")
        if d.action.value == "replan":
            print(f"         rationale: {d.rationale[:160]}")
    print()
    print(f"  Consensus topics on final state: {final.consensus_topics[:5]}")
    return {
        "goal_id": goal_id,
        "state": final.state.value,
        "criteria_satisfied": final.goal.progress.criteria_satisfied,
        "criteria_total": final.goal.progress.criteria_total,
        "decisions": [
            {"iteration": d.iteration, "action": d.action.value,
             "reason_code": d.reason_code, "rationale": d.rationale[:200]}
            for d in final.decisions
        ],
        "consensus_topics": final.consensus_topics[:10],
    }


async def main(json_output: bool = False, live: bool = False,
               repository: Optional[str] = None) -> None:
    fixture = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "fixture_auth_app"

    sep("PHASE 17 DEMO - COLLABORATIVE REASONING & EVIDENCE CONSENSUS")
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
        from app.services.postgres_run_store import PostgresRunStore

        run_store = PostgresRunStore()
        run_store._session_factory = session_factory
        orch = OrchestrationService(run_store=run_store)
    else:
        session_factory = None
        collab = CollaborationService()
        orch = OrchestrationService()
    orch._collaboration = collab
    orch._get_collaboration = lambda: collab

    reasoning = CollaborativeReasoningEngine(
        session_factory=session_factory,
        collaboration=collab,
    )
    ctrl = AutonomousExecutionController(
        orchestration=orch,
        collaboration=collab,
        session_factory=session_factory,
        run_store=run_store if session_factory is not None else None,
    )
    ctrl._reasoning = reasoning

    repo = repository or str(fixture)
    tmp = None
    if live:
        tmp = tempfile.mkdtemp(prefix="devpilot-demo17-")
        repo = str(Path(tmp) / Path(repository or fixture).name)
        shutil.copytree(str(fixture), repo, dirs_exist_ok=True)
        print(f"  Live workspace: {repo}")

    # ── Demonstration A: planner + coding + reviewer agree ────────
    demo_agree = await demo_consensus(
        reasoning, orch, collab, repo,
        "Fix auth token expiry (all agents agree)", fail_then_pass=False,
        live=live,
    )
    print_consensus_summary("DEMONSTRATION A - PLANNER + CODING AGREEMENT", demo_agree)

    # ── Demonstration B: coding vs testing conflict ───────────────
    demo_conflict = await demo_consensus(
        reasoning, orch, collab, repo,
        "Fix auth token expiry (tests fail first)", fail_then_pass=True,
        live=live,
    )
    print_consensus_summary("DEMONSTRATION B - CODING vs TESTING CONFLICT", demo_conflict)

    # ── Demonstration C: reviewer context carries consensus ───────
    sep("DEMONSTRATION C - REVIEWER SEES SHARED CONSENSUS")
    try:
        from app.services.reasoning_service import CollaborativeReasoningEngine as CRE

        fresh_reasoning = CRE(session_factory=session_factory, collaboration=collab)
        await fresh_reasoning.recover(demo_conflict["run_id"])
        conflict_run = await orch._store.get(demo_conflict["run_id"])
        ctx = None
        if conflict_run is not None:
            ctx = await orch._build_agent_context(
                conflict_run, agent_type="reviewer"
            )
        notes = getattr(ctx, "cross_agent_notes", None) or []
        consensus_lines = [n for n in notes if "Consensus" in n or "consensus" in n]
        print("  Reviewer context consensus notes:")
        if consensus_lines:
            for n in consensus_lines[:6]:
                print(f"    - {n}")
        else:
            print("    (none surfaced in this context)")
    except Exception as exc:
        print(f"  [warn] Reviewer-context demo skipped: {exc}")

    # ── Demonstration D: autonomy replan uses consensus ───────────
    sep("DEMONSTRATION D - AUTONOMY REPLAN USES CONSENSUS")
    autonomy_summary = await demo_autonomy_consensus(
        orch, collab, ctrl, reasoning, repo,
    )

    # ── Demonstration E: restart recovery with notebook ───────────
    sep("DEMONSTRATION E - RESTART RECOVERY WITH NOTEBOOK PERSISTENCE")
    if session_factory is not None:
        fresh = CollaborativeReasoningEngine(
            session_factory=session_factory, collaboration=collab,
        )
        await fresh.recover(demo_conflict["run_id"])
        nb = await fresh.get_notebook(demo_conflict["run_id"])
        consensus = await fresh.list_consensus(demo_conflict["run_id"])
        if nb is not None:
            print(f"  Recovered notebook: {nb.notebook_id}  version={nb.version}")
            print(f"    timeline entries: {len(nb.timeline)}  "
                  f"consensus: {len(nb.consensus)}  conflicts: {len(nb.conflicts)}")
        else:
            print("  [warn] Notebook not found after recovery (schema/DB unavailable).")
        print(f"  Recovered consensus: {len(consensus)} record(s)")
    else:
        print("  [warn] In-memory persistence - restart recovery skipped "
              "(no test-named database available).")

    # ── Final summary ─────────────────────────────────────────────
    sep("PHASE 17 SUMMARY")
    print(f"  Run A (agreement):      {demo_agree['run_id']}  "
          f"{len(demo_agree['contradictions'])} contradiction(s), "
          f"{len(demo_agree['consensus'])} consensus")
    print(f"  Run B (conflict):       {demo_conflict['run_id']}  "
          f"{len(demo_conflict['contradictions'])} contradiction(s), "
          f"{len(demo_conflict['consensus'])} consensus")
    print(f"  Autonomy goal:          {autonomy_summary['goal_id']} -> "
          f"{autonomy_summary['state']}  (consensus topics: "
          f"{len(autonomy_summary['consensus_topics'])})")
    print()
    print("  Key invariant: deterministic evidence outranks LLM claims;")
    print("  consensus is evidence-driven, bounded confidence - never CoT.")

    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)
        print("\n  Live workspace cleaned up.")

    if json_output:
        print()
        print("JSON:")
        print(json.dumps({
            "agreement": {
                "run_id": demo_agree["run_id"],
                "contradictions": [c.kind.value for c in demo_agree["contradictions"]],
                "consensus": [
                    {"topic": c.topic, "status": c.status.value,
                     "confidence": round(c.confidence.value, 2),
                     "decision": c.final_decision}
                    for c in demo_agree["consensus"]
                ],
            },
            "conflict": {
                "run_id": demo_conflict["run_id"],
                "contradictions": [
                    {"kind": c.kind.value, "resolution": c.resolution}
                    for c in demo_conflict["contradictions"]
                ],
                "consensus": [
                    {"topic": c.topic, "status": c.status.value,
                     "confidence": round(c.confidence.value, 2),
                     "decision": c.final_decision}
                    for c in demo_conflict["consensus"]
                ],
            },
            "autonomy": autonomy_summary,
        }, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Phase 17 collaborative reasoning demo")
    parser.add_argument("--json", action="store_true", help="Emit JSON summary")
    parser.add_argument("--live", action="store_true",
                        help="Run a REAL execute_run with the configured LLM provider")
    parser.add_argument("--repository", help="Override the target repository path")
    args = parser.parse_args()
    asyncio.run(main(json_output=args.json, live=args.live,
                     repository=args.repository))
