"""
OrchestrationService — Phase 10/11 end-to-end multi-agent orchestrator.

Coordinates the complete pipeline: repository analysis → planning →
coding → testing → repair → review → quality gate.

Phase 11: All RunStore operations are async to support PostgreSQL-backed
persistent storage (PostgresRunStore). Added recovery/resume methods.
"""

from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.agents.planner import PlannerAgent
from app.agents.coding_agent import CodingAgent
from app.agents.test_agent import TestAgent
from app.config import settings
from app.core.exceptions import DevPilotError
from app.core.logging import logger
from app.models.base import new_id
from app.models.coding import (
    CodingAgentInput,
    FileOperation,
    PatchApplicationResult,
    PatchSet,
    PatchStatus,
)
from app.models.issues import (
    ImplementationPlan,
    StructuredRequirements,
    TaskInput,
)
from app.models.orchestration import (
    STAGE_TRANSITIONS,
    TERMINAL_STAGES,
    DevPilotRun,
    DevPilotRunResult,
    EventType,
    FailureCode,
    OrchestrationCapabilities,
    RunEvent,
    RunFailure,
    RunSource,
    RunSourceType,
    RunStateMachine,
    RunStatus,
    StageResult,
    StageStatus,
    StageType,
)
from app.models.profile import RepositoryProfile
from app.models.rag import RetrievedContext
from app.models.repair import RepairResult, RepairSessionStatus
from app.models.review import QualityGateResult, ReviewReport
from app.models.testing import (
    ExecutionPlan,
    ExecutionStatus,
    TestRunResult,
)
from app.services.bounded_retry import run_bounded_retry
from app.services.github import GitHubService
from app.services.index_builder import RepositoryIndexBuilder
from app.services.plan_validator import PlanValidator
from app.services.planning_service import PlanningService
from app.services.patch_validator import PatchValidator
from app.services.repair_service import RepairService
from app.services.review_service import ReviewService
from app.services.run_store import InMemoryRunStore, RunStore, generate_run_id
from app.services.safe_patch_engine import SafePatchEngine
from app.services.testing_service import TestingService
from app.workflows.repository_analysis import RepositoryAnalysisWorkflow

# Phase 11 WebSocket — imported lazily to avoid circular imports
_ws_manager = None

# The coding LLM occasionally returns a valid-but-empty patch set (~20-25%
# on Gemini — see docs/GEMINI_API_KEY_REPORT.md, PROJECT_STATE item 12).
# _stage_coding retries once before failing the stage so a transient empty
# response does not fail the whole run (surfaced by the live goal-API
# validation in verify_api_durability.py).
_CODING_MAX_ATTEMPTS = 2

# The task-analysis LLM (issue analyzer) has the same ~20-25% transient
# variance on Gemini: it can return empty requirements, which the planner
# surfaces as 'No requirements to plan against' and failed the raw-HTTP
# run-API path intermittently (PROJECT_STATE item 13). _stage_task_analysis
# retries once before failing the stage, mirroring the coding retry.
#
# Retry interplay: this stage-level retry is effectively run-API-path only —
# the autonomy goal path pre-populates run.requirements from the goal so
# task analysis never runs there, and the item-13 iteration-level retry
# fires only on CODING_FAILED. The two retries are disjoint by design (no
# double-retry of the same failure).
_TASK_ANALYSIS_MAX_ATTEMPTS = 2


def _get_ws_manager():
    """Lazily import and return the WebSocket manager singleton."""
    global _ws_manager
    if _ws_manager is None:
        from app.services.ws_manager import ws_manager
        _ws_manager = ws_manager
    return _ws_manager


def _get_org_service():
    """Lazily instantiate the OrganizationKnowledgeGraphService.

    Used by Phase 20 auxiliary-repository materialization. Returns None when
    the import fails so the pipeline degrades gracefully (same pattern as the
    ContextEngine's org-graph hook).
    """
    try:
        from app.services.organization_graph_service import (
            OrganizationKnowledgeGraphService,
        )

        return OrganizationKnowledgeGraphService()
    except Exception:  # pragma: no cover - defensive
        logger.warning("Organization graph service unavailable", exc_info=True)
        return None


def _workspace_structure(root: str, max_files: int = 300) -> str:
    """Return a compact relative file listing of a workspace.

    Gives the coding LLM the actual file layout so it can confidently
    propose MODIFY changes (an empty structure made it return
    INSUFFICIENT_CONTEXT -> 'No patch produced').
    """
    base = Path(root or "")
    if not base.is_dir():
        return ""
    lines: List[str] = []
    for p in sorted(base.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(base).as_posix()
        if rel.startswith(".git") or rel.startswith(".venv") or rel.startswith("node_modules"):
            continue
        lines.append(rel)
        if len(lines) >= max_files:
            lines.append("... (truncated)")
            break
    return "\n".join(lines)


class OrchestrationService:
    """Coordinates the end-to-end DevPilot pipeline.

    Delegates to existing Phase 1-9 services. Does NOT reimplement
    any agent logic, patch manipulation, or execution control.

    Flow:
        create_run → execute stages sequentially → return final result
    """

    def __init__(
        self,
        run_store: Optional[RunStore] = None,
        # Phase 2/3
        analysis_workflow: Optional[RepositoryAnalysisWorkflow] = None,
        github_service: Optional[GitHubService] = None,
        # Phase 4
        planning_service: Optional[PlanningService] = None,
        plan_validator: Optional[PlanValidator] = None,
        # Phase 5
        index_builder: Optional[RepositoryIndexBuilder] = None,
        # Phase 6
        coding_agent: Optional[CodingAgent] = None,
        patch_validator: Optional[PatchValidator] = None,
        patch_engine: Optional[SafePatchEngine] = None,
        # Phase 7
        testing_service: Optional[TestingService] = None,
        # Phase 8
        repair_service: Optional[RepairService] = None,
        # Phase 9
        review_service: Optional[ReviewService] = None,
    ) -> None:
        self._store = run_store or InMemoryRunStore()

        # Phase 2-3
        self._analysis = analysis_workflow or RepositoryAnalysisWorkflow()
        self._github = github_service or GitHubService()

        # Phase 4
        self._planning = planning_service or PlanningService()
        self._plan_validator = plan_validator or PlanValidator()

        # Phase 5
        self._index_builder = index_builder or RepositoryIndexBuilder()

        # Phase 6
        self._coding_agent = coding_agent or CodingAgent()
        self._patch_validator = patch_validator or PatchValidator(workspace_root="")
        self._patch_engine = patch_engine or SafePatchEngine(workspace_root="")

        # Phase 7 — pass run_store for persistent workspace tracking
        if testing_service is not None:
            self._testing = testing_service
        else:
            self._testing = TestingService(run_store=self._store)

        # Phase 8
        self._repair = repair_service or RepairService()

        # Phase 9
        self._review = review_service or ReviewService()

        # Phase 13 — ContextEngine (lazy init, gracefully degrades)
        self._context_engine: Any = None

        # Phase 15 — CollaborationService (lazy init, gracefully degrades)
        self._collaboration: Any = None

        # Phase 17 — CollaborativeReasoningEngine (lazy init, gracefully degrades)
        self._reasoning: Any = None

        # Phase 18 — EngineeringKnowledgeGraph (lazy init, gracefully degrades)
        self._engineering_graph: Any = None

    # ── WebSocket Broadcasts ─────────────────────────────────────

    async def _broadcast_update(self, run: DevPilotRun) -> None:
        """Broadcast a run update to all connected WebSocket clients.

        Fire-and-forget — failures are logged but not propagated.
        """
        try:
            ws = _get_ws_manager()
            if ws.active_connections == 0:
                return
            # Build sanitized run data inline
            data = {
                "run_id": run.run_id,
                "status": run.status.value,
                "source": {
                    "source_type": run.source.source_type.value,
                    "title": run.source.title[:200],
                    "description": run.source.description[:500] if run.source.description else "",
                    "repository_path": run.source.repository_path,
                },
                "current_stage": run.current_stage.value,
                "created_at": run.created_at,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
                "stage_results": [
                    {
                        "stage": s.stage.value,
                        "status": s.status.value,
                        "started_at": s.started_at,
                        "finished_at": s.finished_at,
                        "duration_ms": s.duration_ms,
                        "error": s.error,
                    }
                    for s in run.stage_results
                ],
                "failure": {
                    "stage": run.failure.stage.value,
                    "code": run.failure.code.value,
                    "message": run.failure.message[:500],
                } if run.failure else None,
                "warnings": run.warnings[:10],
                "total_duration_ms": run.total_duration_ms,
                "cancellation_requested": run.cancellation_requested,
            }
            await ws.broadcast_run_update(run.run_id, data)
            # Also broadcast a lightweight event
            await ws.broadcast_event(
                run.run_id,
                "stage_transition",
                f"Stage: {run.current_stage.value}",
            )
        except Exception as exc:
            logger.debug("WebSocket broadcast failed (non-critical): %s", exc)

    # ── Run Lifecycle ───────────────────────────────────────────

    async def create_run(self, source: RunSource) -> DevPilotRun:
        """Create a new DevPilot run."""
        run = DevPilotRun(
            run_id=generate_run_id(),
            source=source,
            status=RunStatus.PENDING,
            current_stage=StageType.INITIALIZING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        self._add_event(run, EventType.RUN_CREATED, "Run created")

        # Persist BEFORE recording skipped stages. _record_stage calls
        # _store.update(), which on the durable PostgresRunStore raises
        # RunNotFoundError when the row does not exist yet. InMemoryRunStore
        # silently tolerated the order, so this bug only surfaced on the
        # real PostgreSQL path (autonomy API runs without a repository).
        changed = await self._store.create(run)

        # If no repository_path, skip acquisition/analysis. The event is
        # added BEFORE the stage record so it lands in the same persisted
        # update (the last event would otherwise only live in memory).
        if not source.repository_path:
            self._add_event(changed, EventType.STAGE_SKIPPED, "No repository — skipping acquisition")
            await self._record_stage(changed, StageType.ACQUIRING_REPOSITORY, StageStatus.SKIPPED)

        if not source.repository_path:
            self._add_event(changed, EventType.STAGE_SKIPPED, "No repository — skipping analysis")
            await self._record_stage(changed, StageType.ANALYZING_REPOSITORY, StageStatus.SKIPPED)

        await self._broadcast_update(changed)
        return changed

    # ── Main Execution ──────────────────────────────────────────

    async def execute_run(
        self,
        run_id: str,
        workspace_root: Optional[str] = None,
    ) -> DevPilotRunResult:
        """Execute a full pipeline for a given run.

        Args:
            run_id: The run to execute.
            workspace_root: Optional workspace root for file operations.

        Returns:
            Final run result.
        """
        run = await self._store.get(run_id)
        if not run:
            return self._error_result(run_id, "Run not found", FailureCode.UNKNOWN)

        run.status = RunStatus.RUNNING
        run.started_at = datetime.now(timezone.utc).isoformat()

        effective_ws = workspace_root or run.source.repository_path or ""
        total_start = time.time()

        # Phase 15: cross-agent context sharing.
        # Each stage appends a concise note about its decision; later
        # agents receive the accumulated notes so they build on prior
        # agent output instead of starting from a blank slate.
        cross_agent_notes: List[str] = []

        try:
            # ── STAGE: Repository Acquisition (if GitHub) ──────
            if run.source.repository_path and run.source.source_type == RunSourceType.GITHUB_ISSUE:
                if not await self._stage_acquisition(run):
                    return await self._finalize(run, total_start)
            else:
                # Local repository (or none): acquisition is a no-op, but
                # the strict state machine requires INITIALIZING →
                # ACQUIRING_REPOSITORY before analysis. Advance the state
                # machine so the raw HTTP path (POST /api/v1/runs) can
                # execute a fresh run instead of failing on the invalid
                # INITIALIZING → ANALYZING_REPOSITORY transition.
                if run.current_stage == StageType.INITIALIZING:
                    await self._transition_to(run, StageType.ACQUIRING_REPOSITORY)
                    if run.source.repository_path:
                        # Local repository: record the acquisition skip here
                        # (create_run only records skips for no-repo runs).
                        await self._skip_stage(
                            run, StageType.ACQUIRING_REPOSITORY,
                            "Acquisition not required (local repository)",
                        )
                if run.source.repository_path:
                    run.repository_path = run.source.repository_path
                elif run.current_stage == StageType.ACQUIRING_REPOSITORY:
                    # No repository: acquisition/analysis were already
                    # recorded as SKIPPED at create_run; advance so task
                    # analysis (ANALYZING_REPOSITORY → ANALYZING_TASK) is a
                    # valid transition.
                    await self._transition_to(run, StageType.ANALYZING_REPOSITORY)

            # ── Phase 20: auxiliary repositories ───────────────
            # Materialize + link any auxiliary repositories declared on the
            # source via the organization graph (deterministic, evidence-only;
            # the primary repository_path is unaffected). Runs for both the
            # GitHub and local paths.
            if run.source.repositories and not run.auxiliary_repositories:
                if not await self._materialize_auxiliary_repositories(run):
                    return await self._finalize(run, total_start)

            # ── STAGE: Repository Analysis ─────────────────────
            if run.source.repository_path and not run.repository_profile:
                if not await self._stage_analysis(run):
                    return await self._finalize(run, total_start)

            # ── STAGE: Task Analysis ───────────────────────────
            if not run.requirements:
                if not await self._stage_task_analysis(run):
                    return await self._finalize(run, total_start)

            # ── STAGE: Planning ────────────────────────────────
            if not run.plan:
                # Phase 13: Build planner context before planning
                planner_ctx = await self._build_agent_context(run, "planner", cross_agent_notes=cross_agent_notes)
                if not await self._stage_planning(run, effective_ws, agent_context=planner_ctx):
                    return await self._finalize(run, total_start)
                if run.plan:
                    step_count = len(run.plan.steps or [])
                    cross_agent_notes.append(
                        f"Planner produced a {step_count}-step implementation plan: "
                        f"{(run.plan.summary or run.plan.objective or '')[ :180]}"
                    )
                    # Phase 15: Planner → Coding structured handoff
                    affected = []
                    for step in (run.plan.steps or [])[:10]:
                        for sym in (getattr(step, "affected_symbols", None) or []):
                            if isinstance(sym, str):
                                affected.append(sym)
                    await self._create_handoff(
                        run,
                        from_agent="planner",
                        to_agent="coding",
                        summary=f"{step_count}-step implementation plan ready",
                        decisions=[
                            f"Follow plan: {(run.plan.summary or run.plan.objective or '')[:150]}"
                        ],
                        affected_symbols=affected[:10],
                    )
                    await self._record_decision(
                        run,
                        decision_type="planning",
                        statement=f"Adopt {step_count}-step implementation plan",
                        made_by="planner",
                    )

            # ── STAGE: Context Retrieval ───────────────────────
            if not run.retrieved_context:
                if not await self._stage_retrieval(run, effective_ws):
                    return await self._finalize(run, total_start)

            # ── STAGE: Coding ──────────────────────────────────
            if not run.patch_set:
                # Phase 13: Build coding context before coding
                coding_ctx = await self._build_agent_context(run, "coding", cross_agent_notes=cross_agent_notes)
                if not await self._stage_coding(run, effective_ws, agent_context=coding_ctx):
                    return await self._finalize(run, total_start)
                if run.patch_set and run.patch_set.changes:
                    cross_agent_notes.append(
                        f"Coding agent changed {len(run.patch_set.changes)} file(s): "
                        f"{', '.join(c.path for c in run.patch_set.changes[:5])}"
                    )
                    # Phase 15: Coding → Testing structured handoff
                    await self._create_handoff(
                        run,
                        from_agent="coding",
                        to_agent="testing",
                        summary=f"Changed {len(run.patch_set.changes)} file(s)",
                        decisions=[
                            "Implementation complete; verify with tests",
                        ],
                        affected_symbols=[c.path for c in run.patch_set.changes[:10]],
                    )
                    await self._record_decision(
                        run,
                        decision_type="implementation",
                        statement=f"Implemented changes across {len(run.patch_set.changes)} file(s)",
                        made_by="coding",
                    )

            # ── STAGE: Patch Validation ────────────────────────
            if not await self._stage_patch_validation(run, effective_ws):
                return await self._finalize(run, total_start)

            # ── STAGE: Patch Application ───────────────────────
            if not run.patch_result:
                if not await self._stage_patch_application(run, effective_ws):
                    return await self._finalize(run, total_start)

            # ── STAGE: Testing ─────────────────────────────────
            tests_passed = await self._stage_testing(run, effective_ws)
            if tests_passed is None:
                return await self._finalize(run, total_start)

            # Branch: tests passed → REVIEWING, tests failed → REPAIRING
            if run.test_result:
                cross_agent_notes.append(f"Test run result: {run.test_result.summary[:180]}")

            # Phase 15: detect conflicts between agent claims and deterministic test evidence
            await self._detect_handoff_conflicts(run)

            if tests_passed:
                # Phase 15: Testing → Reviewer structured handoff
                test_summary = (run.test_result.summary or "") if run.test_result else ""
                await self._create_handoff(
                    run,
                    from_agent="testing",
                    to_agent="reviewer",
                    summary=f"Tests passed: {test_summary[:200]}",
                    decisions=["Review the verified implementation"],
                    warnings=None,
                )
                await self._transition_to(run, StageType.REVIEWING)
            else:
                # ── STAGE: Repair (if tests failed) ────────────────
                # Phase 13: Build repair context before repair
                repair_ctx = await self._build_agent_context(run, "repair", cross_agent_notes=cross_agent_notes)
                if run.test_result and run.test_result.failures:
                    await self._create_handoff(
                        run,
                        from_agent="testing",
                        to_agent="repair",
                        summary=f"{len(run.test_result.failures)} failing test(s) need repair",
                        decisions=["Fix failures and re-test"],
                        affected_symbols=[
                            getattr(f, "test_name", getattr(f, "name", ""))
                            for f in run.test_result.failures[:10]
                            if getattr(f, "test_name", getattr(f, "name", ""))
                        ],
                    )
                repair_ok = await self._stage_repair(run, effective_ws, agent_context=repair_ctx)
                if run.repair_result:
                    cross_agent_notes.append(
                        f"Repair ended: {run.repair_result.summary[:180]}"
                    )
                    await self._create_handoff(
                        run,
                        from_agent="repair",
                        to_agent="testing",
                        summary=f"Repair completed: {run.repair_result.summary[:200]}",
                        decisions=[
                            "Attempt history preserved per repair bounds",
                        ],
                        warnings=[run.repair_result.stop_reason or ""]
                        if run.repair_result.stop_reason
                        else None,
                    )
                    await self._record_decision(
                        run,
                        decision_type="repair",
                        statement=run.repair_result.summary[:180],
                        made_by="repair",
                    )
                if repair_ok is None:
                    return await self._finalize(run, total_start)
                # After repair, re-test if repair made changes
                if run.repair_result and run.repair_result.attempts > 0:
                    re_test_passed = await self._stage_testing(run, effective_ws, is_retest=True)
                    if re_test_passed is None:
                        return await self._finalize(run, total_start)
                    # Phase 15: validate/detect conflicts against the retest outcome
                    await self._detect_handoff_conflicts(run)
                    # After re-test: pass → REVIEWING, fail → continue to review anyway
                    if re_test_passed:
                        retest_summary = (run.test_result.summary or "") if run.test_result else ""
                        await self._create_handoff(
                            run,
                            from_agent="testing",
                            to_agent="reviewer",
                            summary=f"Retest passed: {retest_summary[:200]}",
                            decisions=["Review the repaired implementation"],
                            warnings=None,
                        )
                        await self._transition_to(run, StageType.REVIEWING)
                    else:
                        run.warnings.append("Tests still failing after repair — proceeding to review")
                        await self._transition_to(run, StageType.REVIEWING)

            # ── STAGE: Review ──────────────────────────────────
            # Phase 13: Build review context before review
            review_ctx = await self._build_agent_context(run, "reviewer", cross_agent_notes=cross_agent_notes)
            if not await self._stage_review(run, agent_context=review_ctx):
                return await self._finalize(run, total_start)
            if run.review_report:
                cross_agent_notes.append(
                    f"Review found {len(run.review_report.findings or [])} finding(s)"
                )
                await self._create_handoff(
                    run,
                    from_agent="reviewer",
                    to_agent="quality_gate",
                    summary=f"Review found {len(run.review_report.findings or [])} finding(s)",
                    decisions=["Quality gate remains authoritative"],
                )
                await self._record_decision(
                    run,
                    decision_type="review",
                    statement=f"Review completed with {len(run.review_report.findings or [])} finding(s)",
                    made_by="reviewer",
                )

            # ── STAGE: Quality Gate ────────────────────────────
            if not await self._stage_quality_gate(run):
                return await self._finalize(run, total_start)

            # Phase 15: promote verified knowledge at terminal completion
            await self._promote_memory(run)

        except Exception as exc:
            logger.error("Run %s failed unexpectedly: %s", run_id, exc)
            run.failure = RunFailure(
                stage=run.current_stage,
                code=FailureCode.UNKNOWN,
                message=str(exc)[:500],
                recoverable=False,
            )
            await self._transition_to(run, StageType.FAILED, RunStatus.FAILED)
            self._add_event(run, EventType.RUN_FAILED, f"Unexpected error: {exc}")

        return await self._finalize(run, total_start)

    # ── Phase 15: Collaboration Service ─────────────────────────

    def _get_collaboration(self) -> Any:
        """Lazily initialize and return the CollaborationService.

        Gracefully degrades to None if the service is unavailable.
        """
        if self._collaboration is not None:
            return self._collaboration
        try:
            from app.services.collaboration_service import CollaborationService

            self._collaboration = CollaborationService()
        except Exception as exc:
            logger.debug("CollaborationService unavailable: %s", exc)
            self._collaboration = None
        return self._collaboration

    def _get_reasoning(self) -> Any:
        """Lazily initialize the Phase 17 CollaborativeReasoningEngine.

        Gracefully degrades to None if unavailable. Shares the collaboration
        service so evidence records and reasoning records stay consistent.
        """
        if self._reasoning is not None:
            return self._reasoning
        try:
            from app.services.reasoning_service import CollaborativeReasoningEngine

            self._reasoning = CollaborativeReasoningEngine(
                collaboration=self._get_collaboration(),
            )
        except Exception as exc:
            logger.debug("CollaborativeReasoningEngine unavailable: %s", exc)
            self._reasoning = None
        return self._reasoning

    async def _build_reasoning(self, run: DevPilotRun) -> Optional[Dict[str, Any]]:
        """Run the Phase 17 reasoning pipeline at run completion.

        Detect contradictions → consensus → engineering notebook. Never fatal:
        reasoning artifacts are observability; the run result already stands.
        Returns the reasoning outcome dict (used by Phase 18 graph ingestion).
        """
        reasoning = self._get_reasoning()
        if reasoning is None:
            return None
        try:
            outcome = await reasoning.analyze_run(run)
            consensus = outcome.get("consensus") or []
            contradictions = outcome.get("contradictions") or []
            if contradictions:
                self._add_event(
                    run,
                    EventType.CONFLICT_DETECTED,
                    f"Reasoning: {len(contradictions)} contradiction(s) detected",
                )
            if consensus:
                self._add_event(
                    run,
                    EventType.CONSENSUS_BUILT,
                    f"Reasoning: {len(consensus)} consensus record(s) built",
                )
            return outcome
        except Exception as exc:
            logger.debug("Reasoning build skipped (non-fatal): %s", exc)
            return None

    def _select_tests_from_graph(self, changed_files: List[str]) -> List[str]:
        """Select test files via EKG impact edges (patch → test).

        Never raises: no graph or no evidence degrades to [].
        """
        if not changed_files:
            return []
        graph = self._get_engineering_graph()
        if graph is None:
            return []
        try:
            return graph.select_tests_for_changes(changed_files) or []
        except Exception as exc:
            logger.debug("EKG test selection unavailable (non-fatal): %s", exc)
            return []

    def _get_engineering_graph(self) -> Any:
        """Lazily initialize and return the Phase 18 EngineeringKnowledgeGraph.

        Gracefully degrades to None if the service is unavailable. Shares the
        session factory so graph records persist alongside reasoning records.
        """
        if self._engineering_graph is not None:
            return self._engineering_graph
        try:
            from app.services.engineering_graph_service import (
                EngineeringKnowledgeGraphService,
            )

            self._engineering_graph = EngineeringKnowledgeGraphService()
        except Exception as exc:
            logger.debug("EngineeringKnowledgeGraph unavailable: %s", exc)
            self._engineering_graph = None
        return self._engineering_graph

    async def _ingest_into_graph(
        self,
        run: DevPilotRun,
        reasoning_outcome: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Phase 18: enrich the Engineering Knowledge Graph from a completed run.

        Never fatal — graph enrichment is observability; the run result stands.
        """
        graph = self._get_engineering_graph()
        if graph is None:
            return
        try:
            await graph.record_run(run, reasoning_outcome=reasoning_outcome)
        except Exception as exc:
            logger.debug("EKG ingestion skipped (non-fatal): %s", exc)

    async def _create_handoff(
        self,
        run: DevPilotRun,
        from_agent: str,
        to_agent: str,
        summary: str,
        decisions: Optional[List[str]] = None,
        affected_symbols: Optional[List[str]] = None,
        warnings: Optional[List[str]] = None,
    ) -> None:
        """Create a structured handoff + emit event. Never fatal."""
        collab = self._get_collaboration()
        if collab is None:
            return
        try:
            handoff = await collab.create_handoff(
                run_id=run.run_id,
                from_agent=from_agent,
                to_agent=to_agent,
                stage=run.current_stage.value,
                summary=summary,
                decisions=decisions,
                affected_symbols=affected_symbols,
                warnings=warnings,
            )
            if handoff:
                self._add_event(
                    run,
                    EventType.HANDOFF_CREATED,
                    f"{from_agent} → {to_agent} handoff created",
                )
        except Exception as exc:
            logger.debug("Handoff creation skipped (non-fatal): %s", exc)

    async def _record_decision(
        self,
        run: DevPilotRun,
        decision_type: str,
        statement: str,
        made_by: str,
    ) -> None:
        """Record a lightweight engineering decision. Never fatal."""
        collab = self._get_collaboration()
        if collab is None:
            return
        try:
            decision = await collab.record_decision(
                run_id=run.run_id,
                decision_type=decision_type,
                statement=statement,
                made_by=made_by,
            )
            if decision:
                self._add_event(
                    run,
                    EventType.DECISION_RECORDED,
                    f"[{decision_type}] {statement[:120]}",
                )
        except Exception as exc:
            logger.debug("Decision record skipped (non-fatal): %s", exc)

    async def _detect_handoff_conflicts(
        self, run: DevPilotRun
    ) -> None:
        """Validate handoff claims + detect conflicts against deterministic
        evidence (actual patch, actual test result). Never fatal.

        Deterministic evidence always outranks agent claims (§10, §12).
        """
        collab = self._get_collaboration()
        if collab is None or not run.test_result:
            return
        try:
            test_passed = run.test_result.status.value in ("passed", "succeeded")
            changed_files = []
            if run.patch_set and run.patch_set.changes:
                changed_files = [c.path for c in run.patch_set.changes]
            changed_symbols = []
            if run.patch_result:
                changed_symbols = [
                    s for s in (getattr(run.patch_result, "changed_symbols", None) or [])
                    if isinstance(s, str)
                ]

            handoffs = await collab.list_handoffs(run.run_id)
            for handoff in handoffs:
                # §10: validate claims against the actual patch/test result
                await collab.validate_handoff(
                    handoff,
                    changed_files=changed_files,
                    changed_symbols=changed_symbols,
                    test_passed=test_passed,
                )
                # §12: detect contradictions
                conflicts = await collab.detect_conflicts(
                    run_id=run.run_id,
                    handoff=handoff,
                    test_passed=test_passed,
                )
                for conflict in conflicts:
                    self._add_event(
                        run,
                        EventType.CONFLICT_DETECTED,
                        conflict.description[:200],
                    )
        except Exception as exc:
            logger.debug("Conflict detection skipped (non-fatal): %s", exc)

    async def _promote_memory(self, run: DevPilotRun) -> None:
        """Promote verified knowledge to RepositoryMemory at completion."""
        collab = self._get_collaboration()
        if collab is None:
            return
        try:
            promoted = await collab.promote_memory(run)
            if promoted:
                self._add_event(
                    run,
                    EventType.MEMORY_PROMOTED,
                    f"Promoted {promoted} knowledge item(s) to repository memory",
                )
        except Exception as exc:
            logger.debug("Memory promotion skipped (non-fatal): %s", exc)

    # ── Stage Implementations ───────────────────────────────────

    async def _stage_acquisition(self, run: DevPilotRun) -> bool:
        """Clone/acquire the repository."""
        await self._transition_to(run, StageType.ACQUIRING_REPOSITORY)
        try:
            from app.services.remote_analyzer import RemoteRepositoryAnalyzer

            remote = RemoteRepositoryAnalyzer()
            result = await remote.analyze(run.source.repository_path or "")
            if result.profile:
                run.repository_path = run.source.repository_path
                run.repository_profile = result.profile
                await self._complete_stage(run, StageType.ACQUIRING_REPOSITORY)
                return True

            run.failure = RunFailure(
                stage=StageType.ACQUIRING_REPOSITORY,
                code=FailureCode.REPOSITORY_ACQUISITION_FAILED,
                message="Failed to acquire repository",
            )
            await self._transition_to(run, StageType.FAILED, RunStatus.FAILED)
            return False
        except Exception as exc:
            run.failure = RunFailure(
                stage=StageType.ACQUIRING_REPOSITORY,
                code=FailureCode.REPOSITORY_ACQUISITION_FAILED,
                message=str(exc)[:500],
            )
            await self._fail_stage(run, StageType.ACQUIRING_REPOSITORY, str(exc))
            return False

    async def _materialize_auxiliary_repositories(self, run: DevPilotRun) -> bool:
        """Phase 20 — materialize + link a run's auxiliary repositories.

        Converts ``run.source.repositories`` (RepositorySpec) into the org
        graph's ``MultiRepoAcquisitionSpec`` and delegates to
        ``OrganizationKnowledgeGraphService.acquire_and_link_repositories``.

        Deterministic and evidence-only:

        - ``source=local`` checkouts are registered as namespaces with no
          network (the deterministic test path);
        - ``source=github`` repos are shallow-cloned via the org-graph
          acquisition service (never executed);
        - only explicitly declared ``relationships`` become cross-repository
          edges — the org graph is never LLM-inferred from here;
        - the primary ``repository_path`` is unaffected, and per-repo
          isolation is preserved because patches/tests only ever touch the
          primary checkout.

        On success the materialized namespaces are recorded on the run and an
        ``AUXILIARY_REPOSITORIES_ACQUIRED`` event is emitted. On failure the
        run is moved to FAILED and False is returned so the caller finalizes.
        """
        specs = run.source.repositories or []
        if not specs:
            return True

        org = _get_org_service()
        if org is None:
            run.failure = RunFailure(
                stage=StageType.ACQUIRING_REPOSITORY,
                code=FailureCode.REPOSITORY_ACQUISITION_FAILED,
                message="Auxiliary repositories requested but the organization "
                        "graph service is unavailable",
                recoverable=False,
            )
            await self._transition_to(run, StageType.FAILED, RunStatus.FAILED)
            return False

        try:
            from app.models.engineering_graph import (
                CrossRepositoryLinkSpec,
                MultiRepoAcquisitionSpec,
            )

            acq_specs = [
                MultiRepoAcquisitionSpec(
                    repository_id=spec.repository_id,
                    name=spec.name or spec.repository_id,
                    source=spec.source,
                    owner=spec.owner,
                    repo=spec.repo,
                    path=spec.path,
                    ref=spec.ref,
                    depth=spec.depth,
                    relationships=[
                        CrossRepositoryLinkSpec(
                            target_repository_id=rel["target_repository_id"],
                            relationship=rel.get("relationship", "depends_on"),
                            weight=float(rel.get("weight", 1.0)),
                        )
                        for rel in spec.relationships
                        if rel.get("target_repository_id")
                    ],
                )
                for spec in specs
            ]

            result = await org.acquire_and_link_repositories(
                acq_specs, acquisition_service=None, ingest=True,
            )
            namespaces = result.get("namespaces", []) or []
            run.auxiliary_repositories = namespaces
            registered = [n.get("repository_id", "") for n in namespaces]
            self._add_event(
                run,
                EventType.AUXILIARY_REPOSITORIES_ACQUIRED,
                f"Materialized auxiliary repositories: {', '.join(registered) or 'none'}",
                metadata={"repositories": registered},
            )
            await self._store.update(run)
            await self._broadcast_update(run)
            return True
        except Exception as exc:
            run.failure = RunFailure(
                stage=StageType.ACQUIRING_REPOSITORY,
                code=FailureCode.REPOSITORY_ACQUISITION_FAILED,
                message=f"Auxiliary repository materialization failed: {str(exc)[:500]}",
                recoverable=False,
            )
            await self._transition_to(run, StageType.FAILED, RunStatus.FAILED)
            return False

    async def _stage_analysis(self, run: DevPilotRun) -> bool:
        """Analyze the repository."""
        await self._transition_to(run, StageType.ANALYZING_REPOSITORY)
        try:
            if not run.repository_path:
                await self._skip_stage(run, StageType.ANALYZING_REPOSITORY, "No repository path")
                return True
            # RepositoryAnalysisWorkflow.run is async — awaiting it returns
            # the real AnalysisState; without await the profile would be a
            # coroutine (raw HTTP live runs surfaced this: "'coroutine'
            # object has no attribute 'languages'").
            state = await self._analysis.run(run.repository_path)
            profile = getattr(state, "profile", None)
            run.repository_profile = profile
            await self._complete_stage(run, StageType.ANALYZING_REPOSITORY)
            return True
        except Exception as exc:
            run.failure = RunFailure(
                stage=StageType.ANALYZING_REPOSITORY,
                code=FailureCode.REPOSITORY_ANALYSIS_FAILED,
                message=str(exc)[:500],
            )
            await self._fail_stage(run, StageType.ANALYZING_REPOSITORY, str(exc))
            return False

    async def _stage_task_analysis(self, run: DevPilotRun) -> bool:
        """Analyze the task/issue."""
        await self._transition_to(run, StageType.ANALYZING_TASK)
        try:
            task = TaskInput(
                source="user_task",
                title=run.source.title,
                description=run.source.description,
            )
            if run.repository_profile:
                task.repo_languages = [l.name for l in run.repository_profile.languages]
                task.repo_technologies = [t.name for t in run.repository_profile.technologies]
                task.repo_modules = [m.name for m in run.repository_profile.modules]
                task.repo_important_files = [f.path for f in run.repository_profile.important_files]
                if run.repository_profile.tree:
                    task.repo_tree_preview = run.repository_profile.tree.text

            # The task-analysis LLM occasionally returns empty requirements
            # (~20-25% on Gemini — same variance as the coding stage, see
            # docs/GEMINI_API_KEY_REPORT.md, PROJECT_STATE item 13), which
            # surfaces as 'No requirements to plan against' from the
            # planner. Retry once before failing the stage via the shared
            # bounded-retry helper — bounded, and a genuinely broken pipeline
            # fails the second attempt too (no masking).
            async def _analysis_attempt(_attempt: int):
                return await self._planning.plan_from_task(
                    title=run.source.title,
                    description=run.source.description,
                    repo_path=run.repository_path,
                )

            outcome = await run_bounded_retry(
                _analysis_attempt,
                is_success=lambda r: not r.error,
                should_retry=lambda r: bool(r.error),
                max_attempts=_TASK_ANALYSIS_MAX_ATTEMPTS,
                on_retry=lambda attempt, r: self._add_event(
                    run, EventType.TASK_ANALYSIS_RETRY,
                    f"Task analysis attempt {attempt} failed "
                    f"({r.error}); retrying"),
            )
            result = outcome.result
            if result.error:
                await self._fail_stage(run, StageType.ANALYZING_TASK, result.error)
                run.failure = RunFailure(
                    stage=StageType.ANALYZING_TASK,
                    code=FailureCode.TASK_ANALYSIS_FAILED,
                    message=result.error,
                )
                return False
            run.requirements = result.requirements
            await self._complete_stage(run, StageType.ANALYZING_TASK)
            return True
        except Exception as exc:
            await self._fail_stage(run, StageType.ANALYZING_TASK, str(exc))
            run.failure = RunFailure(
                stage=StageType.ANALYZING_TASK,
                code=FailureCode.TASK_ANALYSIS_FAILED,
                message=str(exc)[:500],
            )
            return False

    async def _stage_planning(self, run: DevPilotRun, workspace: str, agent_context: Any = None) -> bool:
        """Generate and validate the implementation plan."""
        await self._transition_to(run, StageType.PLANNING)
        try:
            result = await self._planning.plan_from_task(
                title=run.source.title,
                description=run.source.description,
                repo_path=run.repository_path or workspace,
                agent_context=agent_context,
                # Honor pre-computed requirements (e.g. task analysis already
                # ran, or the caller pre-populated run.requirements). This
                # avoids a redundant LLM issue-analysis call that re-derives
                # and can fail independently of the already-validated input.
                requirements=run.requirements,
            )
            if result.error or not result.plan:
                await self._fail_stage(run, StageType.PLANNING, result.error or "No plan produced")
                run.failure = RunFailure(
                    stage=StageType.PLANNING,
                    code=FailureCode.PLANNING_FAILED,
                    message=result.error or "No plan produced",
                )
                return False

            validation = self._plan_validator.validate(result.plan)
            if not validation.is_valid:
                await self._fail_stage(run, StageType.PLANNING, f"Plan validation: {validation.errors}")
                run.failure = RunFailure(
                    stage=StageType.PLANNING,
                    code=FailureCode.PLANNING_FAILED,
                    message=f"Plan validation failed: {validation.errors[:3]}",
                )
                return False

            run.plan = result.plan
            await self._complete_stage(run, StageType.PLANNING)
            return True
        except Exception as exc:
            await self._fail_stage(run, StageType.PLANNING, str(exc))
            run.failure = RunFailure(
                stage=StageType.PLANNING,
                code=FailureCode.PLANNING_FAILED,
                message=str(exc)[:500],
            )
            return False

    async def _stage_retrieval(self, run: DevPilotRun, workspace: str) -> bool:
        """Retrieve code context from the repository."""
        # Planning's _complete_stage already advanced current_stage to
        # RETRIEVING_CONTEXT, and a same-stage transition raises — so only
        # transition when needed, and never crash non-linear flows.
        if run.current_stage != StageType.RETRIEVING_CONTEXT:
            try:
                await self._transition_to(run, StageType.RETRIEVING_CONTEXT)
            except Exception:
                pass
        try:
            if not run.repository_path and not workspace:
                await self._skip_stage(run, StageType.RETRIEVING_CONTEXT, "No repository for retrieval")
                return True

            repo_path = run.repository_path or workspace
            from app.rag.retrieval.hybrid_retriever import HybridRetriever
            from app.models.rag import RetrievalQuery

            # Build the per-signal indexes and wire them into the retriever
            # (previously a bare RepositoryCodeIndex was passed as
            # lexical_index, so every retrieve() raised AttributeError on
            # `.built` and the stage silently skipped).
            code_index, lex_idx, sym_idx, vec_idx = (
                self._index_builder.build_with_indexes(str(repo_path))
            )
            retriever = HybridRetriever(
                lexical_index=lex_idx,
                symbol_index=sym_idx,
                vector_index=vec_idx,
            )
            retriever.set_indexes(lex_idx, sym_idx, vec_idx, code_index.chunks)
            query = RetrievalQuery(text=run.source.title)
            context = retriever.retrieve(query)

            run.retrieved_context = context
            await self._complete_stage(run, StageType.RETRIEVING_CONTEXT)
            return True
        except Exception as exc:
            await self._skip_stage(run, StageType.RETRIEVING_CONTEXT, f"Retrieval failed: {exc}")
            return True

    async def _stage_coding(self, run: DevPilotRun, workspace: str, agent_context: Any = None) -> bool:
        """Generate a patch via the CodingAgent."""
        await self._transition_to(run, StageType.CODING)
        try:
            if not run.plan:
                await self._fail_stage(run, StageType.CODING, "No plan available")
                return False

            coding_input = CodingAgentInput(
                plan=run.plan,
                requirements=run.requirements,
                retrieved_context=run.retrieved_context,
                # Surface the actual workspace file layout so the LLM knows
                # which files exist and can confidently propose MODIFY
                # changes (previously dropped/empty -> conservative
                # INSUFFICIENT_CONTEXT responses -> 'No patch produced').
                workspace_structure=_workspace_structure(workspace),
                agent_context=agent_context,
            )
            # The coding LLM occasionally fails to produce a patch (~20-25%
            # on Gemini — see docs/GEMINI_API_KEY_REPORT.md, PROJECT_STATE
            # item 12): either a valid-but-empty patch set (status success)
            # or a conservative INSUFFICIENT_CONTEXT refusal. Both are the
            # same transient variance (surfaced by the live goal-API
            # validation in verify_api_durability.py). Retry once via the
            # shared bounded-retry helper; only a hard parse/validation
            # error (status "error") is deterministic and fails immediately.
            last_missing_context = None

            async def _coding_attempt(_attempt: int):
                nonlocal last_missing_context
                out = await self._coding_agent.run(coding_input)
                if out.status == "insufficient_context":
                    last_missing_context = (
                        getattr(out, "missing_context", None)
                        or last_missing_context
                    )
                return out

            outcome = await run_bounded_retry(
                _coding_attempt,
                is_success=lambda out: bool(
                    out.patch_set and out.patch_set.changes),
                should_retry=lambda out: out.status != "error",
                max_attempts=_CODING_MAX_ATTEMPTS,
                on_retry=lambda attempt, out: self._add_event(
                    run, EventType.CODING_RETRY,
                    f"Coding attempt {attempt} produced no patch "
                    f"(status={out.status}); retrying"),
            )
            coding_output = outcome.result
            if coding_output.status == "error":
                await self._fail_stage(
                    run, StageType.CODING, coding_output.error or "Coding failed")
                run.failure = RunFailure(
                    stage=StageType.CODING,
                    code=FailureCode.CODING_FAILED,
                    message=coding_output.error or "Coding agent returned an error",
                )
                return False
            patch_set = coding_output.patch_set
            if not patch_set or not patch_set.changes:
                await self._fail_stage(run, StageType.CODING, "No patch produced")
                message = "Coding agent produced no changes"
                # Exhausted insufficient_context refusals: surface WHAT the
                # LLM claimed was missing so a real context bug (e.g. empty
                # workspace_structure) is diagnosable, not hidden behind the
                # generic message.
                if last_missing_context:
                    missing = ", ".join(
                        str(m) for m in last_missing_context[:5])
                    message += f" (insufficient context: {missing})"
                run.failure = RunFailure(
                    stage=StageType.CODING,
                    code=FailureCode.CODING_FAILED,
                    message=message,
                )
                return False

            run.patch_set = patch_set
            self._add_event(run, EventType.PATCH_GENERATED,
                            f"Generated {len(patch_set.changes)} file change(s)")
            await self._complete_stage(run, StageType.CODING)
            return True
        except Exception as exc:
            await self._fail_stage(run, StageType.CODING, str(exc))
            run.failure = RunFailure(
                stage=StageType.CODING,
                code=FailureCode.CODING_FAILED,
                message=str(exc)[:500],
            )
            return False

    async def _stage_patch_validation(self, run: DevPilotRun, workspace: str) -> bool:
        """Validate the generated patch deterministically."""
        await self._transition_to(run, StageType.VALIDATING_PATCH)
        try:
            if not run.patch_set:
                await self._skip_stage(run, StageType.VALIDATING_PATCH, "No patch to validate")
                return True

            # An LLM-generated patch cannot know the workspace file hashes —
            # compute them so deterministic validation can proceed. Files that
            # do NOT exist in the workspace stay hash-less and are rejected
            # (preserves the anti-hallucination security check).
            await self._enrich_patch_hashes(run.patch_set, workspace)
            self._patch_validator = PatchValidator(workspace_root=workspace)
            # workspace_root is already set via the constructor — the
            # validate() signature does not accept it.
            validation = self._patch_validator.validate(patch=run.patch_set)
            if not validation.is_valid:
                await self._fail_stage(run, StageType.VALIDATING_PATCH,
                                 f"Patch validation: {validation.errors}")
                run.failure = RunFailure(
                    stage=StageType.VALIDATING_PATCH,
                    code=FailureCode.PATCH_VALIDATION_FAILED,
                    message=f"Patch rejected: {validation.errors[:3]}",
                )
                self._add_event(run, EventType.PATCH_REJECTED, str(validation.errors))
                return False

            self._add_event(run, EventType.PATCH_VALIDATED, "Patch passed validation")
            await self._complete_stage(run, StageType.VALIDATING_PATCH)
            return True
        except Exception as exc:
            await self._fail_stage(run, StageType.VALIDATING_PATCH, str(exc))
            return False

    async def _enrich_patch_hashes(self, patch_set: PatchSet, workspace: str) -> None:
        """Fill missing original_hash for MODIFY/DELETE changes from the workspace.

        Deterministic and read-only: computes the SHA-256 of the file the
        patch targets. If the file does not exist, the hash is left unset so
        the validator rejects the change (prevents hallucinated files).
        """
        base = Path(workspace or "")
        for change in patch_set.changes:
            if change.operation not in (FileOperation.MODIFY, FileOperation.DELETE):
                continue
            if change.original_hash:
                continue
            try:
                target = base / change.path
                if target.is_file():
                    change.original_hash = hashlib.sha256(
                        target.read_bytes()).hexdigest()
            except OSError:
                pass

    async def _stage_patch_application(self, run: DevPilotRun, workspace: str) -> bool:
        """Apply the validated patch to the workspace."""
        await self._transition_to(run, StageType.APPLYING_PATCH)
        try:
            if not run.patch_set:
                await self._skip_stage(run, StageType.APPLYING_PATCH, "No patch to apply")
                return True

            self._patch_engine = SafePatchEngine(workspace_root=workspace)
            result = self._patch_engine.apply(run.patch_set)
            run.patch_result = result

            if result.status == PatchStatus.FAILED:
                await self._fail_stage(run, StageType.APPLYING_PATCH, f"Application failed: {result.errors}")
                run.failure = RunFailure(
                    stage=StageType.APPLYING_PATCH,
                    code=FailureCode.PATCH_APPLICATION_FAILED,
                    message=str(result.errors)[:500],
                )
                return False

            self._add_event(
                run,
                EventType.PATCH_APPLIED,
                f"Applied patch: {getattr(result, 'status', 'ok')}",
            )
            await self._complete_stage(run, StageType.APPLYING_PATCH)
            return True
        except Exception as exc:
            await self._fail_stage(run, StageType.APPLYING_PATCH, str(exc))
            return False

    async def _stage_testing(
        self, run: DevPilotRun, workspace: str, is_retest: bool = False
    ) -> Optional[bool]:
        """Run tests and return True if passed, False if failed, None if error.

        Does NOT call _complete_stage (which auto-transitions to the linear next
        stage). The execute_run method handles branching manually based on the
        return value: True → REVIEWING, False → REPAIRING, None → FAILED.
        """
        await self._transition_to(run, StageType.TESTING)
        try:
            ws_path = run.repository_path or workspace
            changed = [c.path for c in run.patch_set.changes] if run.patch_set else []

            candidates = self._testing.discover_commands(ws_path)
            # Phase 12d closure: target the test stage with EKG impact edges
            # (patch → test) when the graph has evidence for the changed
            # files. Replaces the old heuristics-only targeting with
            # cross-run graph evidence; degrades silently when unavailable.
            targeted = self._select_tests_from_graph(changed)
            if targeted:
                for cand in candidates:
                    if cand.executable == "python" and "-m" in cand.arguments:
                        cand.arguments.extend(targeted)
                        cand.reason += (
                            f" | EKG impact-selected tests: {', '.join(targeted)}"
                        )
            plan = self._testing.build_plan(
                workspace_id=run.run_id,
                workspace_root=ws_path,
                candidates=candidates,
                changed_files=changed,
            )
            result = await self._testing.run_tests(plan)
            run.test_result = result

            self._add_event(run, EventType.TESTS_COMPLETED, result.summary[:200])

            if result.status in (ExecutionStatus.PASSED,):
                # Record success — execute_run will transition to REVIEWING
                await self._record_stage(run, StageType.TESTING, StageStatus.SUCCEEDED)
                return True
            elif result.status == ExecutionStatus.ENVIRONMENT_NOT_READY:
                await self._fail_stage(run, StageType.TESTING, result.summary[:200])
                return None
            else:
                # Tests failed — record but don't fail the stage yet
                stage_status = StageStatus.FAILED if is_retest else StageStatus.SUCCEEDED
                await self._record_stage(run, StageType.TESTING, stage_status)
                return False
        except Exception as exc:
            await self._fail_stage(run, StageType.TESTING, str(exc))
            return None

    async def _stage_repair(self, run: DevPilotRun, workspace: str, agent_context: Any = None) -> Optional[bool]:
        """Run the bounded repair loop. Returns True if repair succeeded/not needed."""
        await self._transition_to(run, StageType.REPAIRING)
        self._add_event(run, EventType.REPAIR_STARTED, "Starting repair loop")
        try:
            if not run.test_result or not run.test_result.failures:
                await self._skip_stage(run, StageType.REPAIRING, "No failures to repair")
                return True

            result = await self._repair.run_repair(
                workspace_root=workspace,
                workspace_id=run.run_id,
                test_result=run.test_result,
                patch_set=run.patch_set,
                patch_result=run.patch_result,
                plan=run.plan,
                retrieved_context=run.retrieved_context,
                changed_files=[c.path for c in run.patch_set.changes] if run.patch_set else [],
                agent_context=agent_context,
            )
            run.repair_result = result

            self._add_event(run, EventType.REPAIR_COMPLETED, result.summary)

            if result.status == RepairSessionStatus.SUCCESS:
                await self._complete_stage(run, StageType.REPAIRING)
                return True
            elif result.status in (
                RepairSessionStatus.NO_REPAIR,
                RepairSessionStatus.NO_PROGRESS,
                RepairSessionStatus.REPEATED_PATCH,
            ):
                await self._complete_stage(run, StageType.REPAIRING)
                run.warnings.append(f"Repair incomplete: {result.stop_reason}")
                return True
            elif result.status == RepairSessionStatus.UNSAFE_REPAIR:
                await self._fail_stage(run, StageType.REPAIRING, result.stop_reason)
                run.failure = RunFailure(
                    stage=StageType.REPAIRING,
                    code=FailureCode.REPAIR_FAILED,
                    message=result.stop_reason or "Unsafe repair detected",
                )
                return None
            else:
                await self._complete_stage(run, StageType.REPAIRING)
                run.warnings.append(f"Repair ended: {result.stop_reason}")
                return True
        except Exception as exc:
            await self._fail_stage(run, StageType.REPAIRING, str(exc))
            return None

    async def _stage_review(self, run: DevPilotRun, agent_context: Any = None) -> bool:
        """Run the review pipeline."""
        await self._transition_to(run, StageType.REVIEWING)
        try:
            changed_files = []
            if run.patch_set:
                changed_files = [c.path for c in run.patch_set.changes]

            report, _ = await self._review.run_review(
                workspace_id=run.run_id,
                workspace_root=run.repository_path or "",
                requirements=run.requirements,
                implementation_plan=run.plan,
                original_patch=run.patch_set,
                patch_application=run.patch_result,
                repair_result=run.repair_result,
                test_result=run.test_result,
                repository_profile=run.repository_profile,
                retrieved_context=run.retrieved_context,
                changed_files=changed_files,
                agent_context=agent_context,
            )
            run.review_report = report
            self._add_event(run, EventType.REVIEW_COMPLETED,
                            f"{len(report.findings)} finding(s)")
            await self._complete_stage(run, StageType.REVIEWING)
            return True
        except Exception as exc:
            await self._fail_stage(run, StageType.REVIEWING, str(exc))
            return False

    async def _stage_quality_gate(self, run: DevPilotRun) -> bool:
        """Invoke the deterministic Quality Gate."""
        await self._transition_to(run, StageType.QUALITY_GATE)
        try:
            from app.services.deterministic_review import DeterministicReview
            from app.models.review import ReviewInput as RI

            dr = DeterministicReview()
            inp = RI(
                workspace_id=run.run_id,
                requirements=run.requirements,
                implementation_plan=run.plan,
                original_patch=run.patch_set,
                repair_result=run.repair_result,
                test_result=run.test_result,
                changed_files=[c.path for c in run.patch_set.changes] if run.patch_set else [],
            )
            det_result = dr.run(inp)

            # If we don't have a review report yet, run review service to get one
            report = run.review_report
            if not report:
                from app.services.review_service import ReviewService
                report, _ = await ReviewService().run_review(
                    workspace_id=run.run_id,
                    requirements=run.requirements,
                    implementation_plan=run.plan,
                    original_patch=run.patch_set,
                    repair_result=run.repair_result,
                    test_result=run.test_result,
                )

            from app.services.quality_gate import QualityGate
            gate_result = QualityGate().decide(
                report=report,
                deterministic_result=det_result,
                test_result=run.test_result,
            )
            run.quality_gate_result = gate_result
            self._add_event(run, EventType.QUALITY_GATE_COMPLETED,
                            f"Decision: {gate_result.decision.value}")

            if gate_result.decision.value == "approved":
                await self._transition_to(run, StageType.COMPLETED, RunStatus.APPROVED)
            elif gate_result.decision.value == "rejected":
                await self._transition_to(run, StageType.COMPLETED, RunStatus.REJECTED)
            elif gate_result.decision.value == "needs_human_review":
                await self._transition_to(run, StageType.COMPLETED, RunStatus.NEEDS_HUMAN_REVIEW)
            else:
                await self._transition_to(run, StageType.COMPLETED, RunStatus.FAILED)

            await self._complete_stage(run, StageType.QUALITY_GATE)
            return True
        except Exception as exc:
            await self._fail_stage(run, StageType.QUALITY_GATE, str(exc))
            return False

    # ── Transition Helpers (ALL ASYNC — they call self._store) ──

    async def _transition_to(
        self,
        run: DevPilotRun,
        target_stage: StageType,
        target_status: Optional[RunStatus] = None,
    ) -> None:
        """Transition the run to a new stage, updating the store.

        Same-stage calls are treated as a no-op for the stage change: a
        stage method's _complete_stage may already have advanced
        current_stage to this stage before its own _transition_to runs
        (e.g. retrieval completes -> CODING, then _stage_coding transitions
        to CODING). Same-stage transitions are invalid in the strict model.
        """
        if run.current_stage != target_stage:
            try:
                RunStateMachine.transition(run.current_stage, target_stage)
            except ValueError as e:
                logger.error("Invalid transition %s -> %s: %s",
                              run.current_stage.value, target_stage.value, e)
                raise DevPilotError(f"Invalid state transition: {e}") from e
        run.current_stage = target_stage
        if target_status:
            run.status = target_status
        await self._store.update(run)
        await self._broadcast_update(run)

    async def _record_stage(
        self, run: DevPilotRun, stage: StageType, status: StageStatus,
        error: Optional[str] = None,
    ) -> None:
        """Record a stage result."""
        now = datetime.now(timezone.utc).isoformat()
        sr = StageResult(
            stage=stage,
            status=status,
            started_at=now,
            finished_at=now,
            error=error,
        )
        run.stage_results.append(sr)
        await self._store.update(run)
        await self._broadcast_update(run)

    async def _complete_stage(self, run: DevPilotRun, stage: StageType) -> None:
        """Mark a stage as succeeded."""
        await self._record_stage(run, stage, StageStatus.SUCCEEDED)
        self._add_event(run, EventType.STAGE_COMPLETED, f"Stage '{stage.value}' succeeded")
        # Move to next stage
        next_s = RunStateMachine.next_stage(stage)
        if next_s and not RunStateMachine.is_terminal(next_s):
            await self._transition_to(run, next_s)

    async def _fail_stage(self, run: DevPilotRun, stage: StageType, error: str) -> None:
        """Mark a stage as failed."""
        await self._record_stage(run, stage, StageStatus.FAILED, error)
        self._add_event(run, EventType.STAGE_FAILED, f"Stage '{stage.value}' failed: {error}")

    async def _skip_stage(self, run: DevPilotRun, stage: StageType, reason: str) -> None:
        """Mark a stage as skipped."""
        await self._record_stage(run, stage, StageStatus.SKIPPED)
        self._add_event(run, EventType.STAGE_SKIPPED, f"Stage '{stage.value}' skipped: {reason}")

    def _add_event(
        self,
        run: DevPilotRun,
        event_type: EventType,
        message: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Add an event to the run (no store call — just appends to list)."""
        event = RunEvent(
            event_id=f"evt-{new_id()[:8]}",
            run_id=run.run_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            event_type=event_type,
            stage=run.current_stage,
            message=message[:500],
            metadata=metadata or {},
        )
        run.events.append(event)
        logger.info("Event run_id=%s stage=%s type=%s msg=%s",
                     run.run_id,
                     run.current_stage.value,
                     event_type.value,
                     message[:200])

    # ── Finalization ────────────────────────────────────────────

    async def _finalize(self, run: DevPilotRun, start_time: float) -> DevPilotRunResult:
        """Finalize a run and produce the result."""
        duration = time.time() - start_time
        run.finished_at = datetime.now(timezone.utc).isoformat()

        # Phase 17: build consensus + contradictions + engineering notebook
        # (non-fatal, evidence-only observability).
        outcome = await self._build_reasoning(run)

        # Phase 18: enrich the Engineering Knowledge Graph from this run
        # (goals → plans → patches → tests → review → gate → notebook →
        # consensus → memory) — non-fatal, evidence-only.
        await self._ingest_into_graph(run, reasoning_outcome=outcome)

        if run.status == RunStatus.RUNNING:
            if run.failure:
                run.status = RunStatus.FAILED
            else:
                run.status = RunStatus.FAILED

        run.total_duration_ms = duration * 1000
        await self._store.update(run)
        await self._broadcast_update(run)

        if run.status in (RunStatus.FAILED,):
            self._add_event(run, EventType.RUN_FAILED, run.failure.message if run.failure else "Run failed")
        elif run.status == RunStatus.APPROVED:
            self._add_event(run, EventType.RUN_COMPLETED, "Run completed: APPROVED")

        return self._build_result(run, duration)

    def _build_result(self, run: DevPilotRun, duration: float) -> DevPilotRunResult:
        """Build a DevPilotRunResult from the run state."""
        return DevPilotRunResult(
            run_id=run.run_id,
            status=run.status,
            source=run.source,
            repository=run.repository_path,
            auxiliary_repositories=run.auxiliary_repositories,
            stages=[
                {
                    "stage": s.stage.value,
                    "status": s.status.value,
                    "duration_ms": s.duration_ms,
                    "error": s.error,
                }
                for s in run.stage_results
            ],
            events=[
                {
                    "event_id": e.event_id,
                    "event_type": e.event_type.value,
                    "stage": e.stage.value if e.stage else None,
                    "message": e.message[:200],
                    "timestamp": e.timestamp,
                }
                for e in run.events
            ],
            requirements=run.requirements,
            plan=run.plan,
            test_result=run.test_result,
            review_report=run.review_report,
            quality_gate=run.quality_gate_result,
            failure=run.failure,
            warnings=run.warnings,
            started_at=run.started_at,
            finished_at=run.finished_at,
            duration_seconds=duration,
        )

    def _error_result(
        self, run_id: str, message: str, code: FailureCode = FailureCode.UNKNOWN
    ) -> DevPilotRunResult:
        """Build an error result when the run can't be found."""
        return DevPilotRunResult(
            run_id=run_id,
            status=RunStatus.FAILED,
            source=RunSource(source_type=RunSourceType.USER_TASK, title=""),
            failure=RunFailure(
                stage=StageType.INITIALIZING,
                code=code,
                message=message,
            ),
        )

    # ── Cancellation ────────────────────────────────────────────

    async def request_cancellation(self, run_id: str) -> bool:
        """Request cancellation of a run."""
        run = await self._store.get(run_id)
        if not run:
            return False
        if run.status in (
            RunStatus.APPROVED, RunStatus.REJECTED,
            RunStatus.NEEDS_HUMAN_REVIEW, RunStatus.FAILED, RunStatus.CANCELLED,
        ):
            return False
        run.cancellation_requested = True
        self._add_event(run, EventType.CANCELLATION_REQUESTED, "Cancellation requested")
        await self._store.update(run)
        await self._broadcast_update(run)
        return True

    async def _check_cancelled(self, run: DevPilotRun) -> bool:
        """Check if cancellation was requested. Returns True if should stop."""
        if run.cancellation_requested:
            for sr in run.stage_results:
                if sr.status == StageStatus.PENDING:
                    sr.status = StageStatus.CANCELLED
            run.status = RunStatus.CANCELLED
            self._add_event(run, EventType.RUN_CANCELLED, "Run cancelled")
            await self._store.update(run)
            return True
        return False

    # ── Public API ──────────────────────────────────────────────

    async def get_run(self, run_id: str) -> Optional[DevPilotRun]:
        """Get a run by ID."""
        return await self._store.get(run_id)

    async def list_runs(
        self,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        sort_by: str = "newest",
        created_after: Optional[str] = None,
        created_before: Optional[str] = None,
    ) -> List[DevPilotRun]:
        """List runs with optional filtering, sorting, and date range."""
        return await self._store.list(
            status=status, limit=limit, offset=offset, sort_by=sort_by,
            created_after=created_after, created_before=created_before,
        )

    async def list_runs_with_stats(
        self,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        sort_by: str = "newest",
        created_after: Optional[str] = None,
        created_before: Optional[str] = None,
    ) -> tuple[List[DevPilotRun], int, Dict[str, int]]:
        """List runs + total count + unfiltered stats — batched in one session.

        Falls back to calling individual methods if the store doesn't
        support the batched operation (e.g. InMemoryRunStore).

        Returns:
            (runs_list, total_matching_count, stats_by_status)
        """
        if hasattr(self._store, "list_with_total_and_stats"):
            return await self._store.list_with_total_and_stats(
                status=status, limit=limit, offset=offset, sort_by=sort_by,
                created_after=created_after, created_before=created_before,
            )
        # Fallback: three individual calls
        runs = await self._store.list(
            status=status, limit=limit, offset=offset, sort_by=sort_by,
            created_after=created_after, created_before=created_before,
        )
        total = await self._store.count_runs(status=status, created_after=created_after, created_before=created_before) if hasattr(self._store, "count_runs") else len(runs)
        stats = await self._store.count_runs_by_status() if hasattr(self._store, "count_runs_by_status") else {
            "total": 0, "pending": 0, "running": 0, "approved": 0,
            "rejected": 0, "needs_human_review": 0, "failed": 0, "cancelled": 0,
        }
        return runs, total, stats

    async def count_runs(self, status: Optional[str] = None, created_after: Optional[str] = None, created_before: Optional[str] = None) -> int:
        """Count runs, with optional status filter and date range."""
        if hasattr(self._store, "count_runs"):
            return await self._store.count_runs(status=status, created_after=created_after, created_before=created_before)
        return 0

    async def get_run_stats(self) -> Dict[str, int]:
        """Get aggregate run counts by status across all runs.

        These counts are unfiltered (across all runs), so stat cards
        always reflect the full picture regardless of active filters.
        """
        if hasattr(self._store, "count_runs_by_status"):
            return await self._store.count_runs_by_status()
        return {
            "total": 0, "pending": 0, "running": 0, "approved": 0,
            "rejected": 0, "needs_human_review": 0, "failed": 0, "cancelled": 0,
        }

    async def get_events(self, run_id: str) -> List[Dict[str, Any]]:
        """Get sanitized events for a run."""
        run = await self._store.get(run_id)
        if not run:
            return []
        return [
            {
                "event_id": e.event_id,
                "event_type": e.event_type.value,
                "stage": e.stage.value if e.stage else None,
                "message": e.message[:200],
                "timestamp": e.timestamp,
            }
            for e in run.events
        ]

    @staticmethod
    def get_capabilities() -> OrchestrationCapabilities:
        """Return current orchestration capabilities."""
        return OrchestrationCapabilities()

    @staticmethod
    def _parse_github_url(url: str) -> tuple[str, str]:
        """Parse owner/repo from a GitHub URL."""
        import re
        m = re.match(r"(?:https?://github\.com/)?([^/]+)/([^/#?]+)", url)
        if m:
            return m.group(1), m.group(2)
        return "", ""

    # ── Phase 13: Context Engine ─────────────────────────────────

    def _get_context_engine(self) -> Any:
        """Lazily initialize and return the ContextEngine.

        Gracefully degrades to None if the engine is unavailable.
        """
        if self._context_engine is not None:
            return self._context_engine
        try:
            from app.services.context_engine import ContextEngine
            self._context_engine = ContextEngine()
        except Exception as exc:
            logger.debug("ContextEngine unavailable: %s", exc)
            self._context_engine = None
        return self._context_engine

    async def _build_agent_context(
        self,
        run: DevPilotRun,
        agent_type: str,
        cross_agent_notes: Optional[List[str]] = None,
    ) -> Any:
        """Build agent-specific context from the current run state.

        Calls ContextEngine.build_context() with whatever evidence
        is available on the run. Returns None if ContextEngine is
        unavailable or context building fails (graceful degradation).

        Phase 15: accepts cross_agent_notes accumulated by earlier
        stages so later agents see what prior agents decided.
        """
        engine = self._get_context_engine()
        if engine is None:
            return None

        try:
            # Phase 15: structured handoffs relevant to this agent
            handoffs = None
            collab = self._get_collaboration()
            if collab is not None:
                handoffs = await collab.retrieve_relevant_handoffs(
                    run_id=run.run_id,
                    agent_type=agent_type,
                )
            # Gather available evidence from run state
            plan_text = None
            if run.plan:
                plan_text = run.plan.summary + "\n" + "\n".join(
                    f"{s.id}: {s.title}" for s in (run.plan.steps or [])
                )

            requirements_text = None
            if run.requirements:
                requirements_text = run.requirements.objective

            test_failures = None
            if run.test_result and run.test_result.failures:
                test_failures = [
                    {
                        "test_name": getattr(f, "test_name", getattr(f, "name", f"failure_{i}")),
                        "message": getattr(f, "message", ""),
                        "file_path": getattr(f, "file_path", ""),
                    }
                    for i, f in enumerate(run.test_result.failures[:10])
                ]

            repair_history = None
            if run.repair_result and run.repair_result.session:
                repair_history = [
                    {
                        "status": a.status.value if hasattr(a.status, "value") else str(a.status),
                        "reason": getattr(a, "stop_reason", "") or "; ".join(a.errors or []),
                    }
                    for a in (run.repair_result.session.attempts or [])
                ]

            review_findings = None
            if run.review_report and run.review_report.findings:
                review_findings = [
                    {
                        "title": getattr(f, "title", getattr(f, "finding", f"finding_{i}")),
                        "severity": getattr(f, "severity", "unknown"),
                    }
                    for i, f in enumerate(run.review_report.findings[:5])
                ]

            # Phase 17: reviewer sees shared consensus (evidence-only).
            consensus_notes = None
            if agent_type == "reviewer":
                reasoning = self._get_reasoning()
                if reasoning is not None:
                    try:
                        consensus = await reasoning.list_consensus(run.run_id)
                        if consensus:
                            consensus_notes = [
                                f"Consensus [{c.topic}] {c.status.value} "
                                f"confidence={round(c.confidence.value, 2)} "
                                f"decision={c.final_decision[:120]}"
                                for c in consensus[:5]
                            ]
                    except Exception as exc:
                        logger.debug("Consensus notes unavailable: %s", exc)

            all_notes = list(cross_agent_notes or [])
            if consensus_notes:
                all_notes.append("Shared engineering consensus (Phase 17):")
                all_notes.extend(consensus_notes)

            ctx = await engine.build_context(
                task=run.source.title,
                agent_type=agent_type,
                repository_path=run.repository_path or "",
                plan_text=plan_text,
                requirements_text=requirements_text,
                run_id=run.run_id,
                test_failures=test_failures,
                repair_history=repair_history,
                review_findings=review_findings,
                cross_agent_notes=all_notes or None,
                handoffs=handoffs,
            )
            return ctx
        except Exception as exc:
            logger.debug("Context building failed for %s (continuing): %s", agent_type, exc)
            return None

    # ── Phase 11: Recovery & Resume ─────────────────────────────

    async def check_recovery(self) -> Dict[str, Any]:
        """Check for recoverable runs on startup.

        Returns diagnostics about stale / recoverable runs.
        """
        if not hasattr(self._store, "find_recoverable_runs"):
            return {"store_type": "in_memory", "recovery_supported": False}

        try:
            recoverable = await self._store.find_recoverable_runs()
            stale_count = 0
            if recoverable:
                stale_count = await self._store.mark_stale_runs(max_age_minutes=60)

            return {
                "store_type": "postgres",
                "recovery_supported": True,
                "recoverable_found": len(recoverable),
                "marked_stale": stale_count,
                "recoverable_ids": [r.run_id for r in recoverable],
            }
        except Exception as exc:
            logger.error("Recovery check failed: %s", exc)
            return {
                "store_type": "postgres",
                "recovery_supported": True,
                "error": str(exc)[:200],
            }

    async def resume_run(
        self,
        run_id: str,
        workspace_root: Optional[str] = None,
    ) -> Optional[DevPilotRunResult]:
        """Resume a previously interrupted run from its last checkpoint.

        Determines the last completed stage and resumes execution
        from the next logical stage. Stages that are REPLAY_SAFE
        (analysis, planning, retrieval) are not re-executed.

        Returns None if the run is not resumable.
        """
        run = await self._store.get(run_id)
        if not run:
            logger.warning("Resume failed: run %s not found", run_id)
            return None

        # Check if run is resumable
        terminal_statuses = {
            RunStatus.APPROVED, RunStatus.REJECTED,
            RunStatus.NEEDS_HUMAN_REVIEW, RunStatus.FAILED, RunStatus.CANCELLED,
        }
        if run.status in terminal_statuses:
            logger.warning("Resume failed: run %s is terminal (%s)", run_id, run.status.value)
            return None

        if run.cancellation_requested:
            logger.warning("Resume failed: run %s has cancellation requested", run_id)
            return None

        # Determine last completed stage
        last_completed = StageType.INITIALIZING
        for sr in run.stage_results:
            if sr.status == StageStatus.SUCCEEDED:
                # Find the stage in the pipeline order
                for st in StageType:
                    if st == sr.stage:
                        last_completed = st
                        break

        logger.info("Resuming run %s from stage %s (last completed: %s)",
                     run_id, run.current_stage.value, last_completed.value)

        # Resume execution from current stage
        result = await self.execute_run(
            run_id=run_id,
            workspace_root=workspace_root or run.source.repository_path,
        )
        return result
