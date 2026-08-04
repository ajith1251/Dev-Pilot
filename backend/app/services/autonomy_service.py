"""
Phase 16 — AutonomousExecutionController and supporting evaluators.

The controller sits ABOVE the Phase 10/15 orchestrator:

    Autonomous Controller = decides WHAT happens next
    Orchestrator          = executes engineering stages
    Agents                = perform specialized work
    Quality Gate          = determines final engineering approval

Responsibilities (all bounded and auditable):
- Goal creation + acceptance-criteria extraction (deterministic-first)
- Iteration loop with explicit next-action decisions (CONTINUE / REPAIR /
  REPLAN / REVIEW / COMPLETE / ESCALATE / STOP)
- Execution budgets with pre/post operation checks
- Progress evaluation across iterations
- Stuck detection via deterministic fingerprints
- Plan versioning (immutable history, replan validation)
- Scope control
- Human escalation with structured input requests
- Pause / resume / cancellation
- Durable checkpoints + restart recovery + optimistic concurrency
- Dry-run estimation (no mutations)

Deterministic evidence is authoritative. LLM claims can never override
test results, patch evidence, or the quality gate. Never persists
chain-of-thought.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import logger
from app.models.autonomy import (
    DEFAULT_MAX_AGENT_CALLS,
    DEFAULT_MAX_FILES_CHANGED,
    DEFAULT_MAX_ITERATIONS,
    DEFAULT_MAX_LLM_CALLS,
    DEFAULT_MAX_REPAIRS,
    DEFAULT_MAX_REPLANS,
    DEFAULT_MAX_TEST_RUNS,
    MAX_CRITERIA_PER_GOAL,
    AcceptanceCriterion,
    AutonomousAction,
    AutonomousCheckpoint,
    AutonomousDecision,
    AutonomousRunState,
    AutonomyPolicy,
    CriterionStatus,
    CriterionType,
    DryRunReport,
    EscalationReason,
    EscalationStatus,
    ExecutionBudget,
    ExecutionGoal,
    ExecutionState,
    FailureClass,
    GoalProgress,
    HumanEscalation,
    IterationEvidence,
    PlanVersion,
    ProgressTrend,
    TaskScope,
)
from app.models.collaboration import EvidenceRef, EvidenceType
from app.models.orchestration import (
    EventType,
    FailureCode,
    RunSource,
    RunSourceType,
    StageType,
)
from app.services.bounded_retry import run_bounded_retry
from app.models.issues import (
    ImplementationPlan,
    ImplementationStep,
    Requirement,
    RequirementType,
    StructuredRequirements,
)


class ConcurrencyConflictError(RuntimeError):
    """Raised when another autonomous worker advanced the same goal."""

    pass


# Event mapping onto the shared EventType enum (Phase 16 members added
# to app/models/orchestration.py EventType).
_EVENT_MAP = {
    "GOAL_CREATED": EventType.GOAL_CREATED,
    "CRITERION_UPDATED": EventType.CRITERION_UPDATED,
    "ITERATION_STARTED": EventType.ITERATION_STARTED,
    "PROGRESS_EVALUATED": EventType.PROGRESS_EVALUATED,
    "REPLAN_REQUESTED": EventType.REPLAN_REQUESTED,
    "PLAN_SUPERSEDED": EventType.PLAN_SUPERSEDED,
    "STUCK_DETECTED": EventType.STUCK_DETECTED,
    "BUDGET_WARNING": EventType.BUDGET_WARNING,
    "BUDGET_EXHAUSTED": EventType.BUDGET_EXHAUSTED,
    "ESCALATION_CREATED": EventType.ESCALATION_CREATED,
    "GOAL_COMPLETED": EventType.GOAL_COMPLETED,
    "RUN_RETRY": EventType.RUN_RETRY,
}


# ── Failure classification (§20) ────────────────────────────────
# Classify BEFORE repairing: never let a coding agent "fix" code when the
# problem is environmental.

_ENV_FAILURE_CODES = {
    FailureCode.REPOSITORY_ACQUISITION_FAILED,
    FailureCode.REPOSITORY_ANALYSIS_FAILED,
}


def classify_failure(
    failure_code: Optional[str],
    test_status: Optional[str],
) -> FailureClass:
    """Classify a failure as CODE / TEST / ENVIRONMENT / CONFIG / DEPENDENCY."""
    if test_status == "environment_not_ready":
        return FailureClass.ENVIRONMENT
    if failure_code:
        code = FailureCode(failure_code) if failure_code in FailureCode._value2member_map_ else None
        if code in _ENV_FAILURE_CODES:
            return FailureClass.ENVIRONMENT
        if code in (FailureCode.PATCH_VALIDATION_FAILED, FailureCode.PATCH_APPLICATION_FAILED):
            return FailureClass.CODE
        if code in (FailureCode.TEST_EXECUTION_FAILED,):
            return FailureClass.TEST
        if code == FailureCode.REPAIR_FAILED:
            return FailureClass.TEST
        if code in (FailureCode.REVIEW_FAILED, FailureCode.QUALITY_GATE_FAILED):
            return FailureClass.CODE
    return FailureClass.UNKNOWN


# Phase 16 live updates — imported lazily to avoid circular imports.
_ws_manager = None


def _get_ws_manager():
    """Lazily import and return the WebSocket manager singleton."""
    global _ws_manager
    if _ws_manager is None:
        from app.services.ws_manager import ws_manager

        _ws_manager = ws_manager
    return _ws_manager


# ── BudgetManager (§9) ──────────────────────────────────────────


class BudgetManager:
    """Checks remaining budget before operations; records usage after."""

    def check(self, state: AutonomousRunState) -> Optional[str]:
        """Return the exhausted limit name, or None if budget remains."""
        return state.budget.exhausted()

    def check_warning(self, state: AutonomousRunState) -> Optional[str]:
        """Warn when 80%+ of any limit is consumed."""
        limits = state.budget.limits()
        usage = state.budget.usage()
        for key in ("iterations", "replans", "repairs"):
            limit_key = f"max_{key}"
            limit = limits.get(limit_key, 0)
            used = usage.get(key, 0)
            if limit and used >= limit * 0.8:
                return f"{key} usage at {used}/{limit}"
        return None

    def record(self, state: AutonomousRunState, evidence: IterationEvidence) -> None:
        """Record usage after an iteration."""
        budget = state.budget
        budget.iterations_used += 1
        # Approximate agent calls from executed engineering stages.
        budget.agent_calls_used += 5  # planning, coding, testing, review, gate
        budget.llm_calls_used += 3  # planner, coder, reviewer (bounded estimate)
        if evidence.test_status is not None:
            budget.test_runs_used += 1
        budget.files_changed_used = max(
            budget.files_changed_used, len(evidence.changed_files)
        )
        # Repair budget is consumed by autonomy-level REPAIR decisions in the
        # loop; internal orchestrator repair attempts are informational only.
        budget.execution_time_used_seconds += evidence.duration_seconds

    def record_replan(self, state: AutonomousRunState) -> None:
        state.budget.replans_used += 1


# ── GoalEvaluator (§5) ──────────────────────────────────────────


class GoalEvaluator:
    """Deterministic-first acceptance-criteria evaluation.

    LLMs may help interpret ambiguous criteria, but can never override
    deterministic evidence (test results, patch evidence, quality gate).
    """

    def extract_criteria(
        self,
        task: str,
        criteria_texts: Optional[List[str]] = None,
        requirements: Optional[StructuredRequirements] = None,
        plan: Optional[ImplementationPlan] = None,
    ) -> List[AcceptanceCriterion]:
        """Extract acceptance criteria from explicit texts + requirements + plan."""
        criteria: List[AcceptanceCriterion] = []

        if criteria_texts:
            for text in criteria_texts[:20]:
                criteria.append(AcceptanceCriterion(
                    description=text[:300],
                    criterion_type=CriterionType.FUNCTIONAL,
                    verification="suite:pass",
                ))

        if requirements:
            for req in (requirements.requirements or [])[:10]:
                criteria.append(AcceptanceCriterion(
                    description=(req.description or req.acceptance_note or "")[:300],
                    criterion_type=_req_type(req.requirement_type),
                    verification="suite:pass",
                ))

        if plan and plan.steps:
            for step in plan.steps[:10]:
                criteria.append(AcceptanceCriterion(
                    description=(step.title or step.description or "")[:300],
                    criterion_type=CriterionType.FUNCTIONAL,
                    verification="suite:pass",
                ))

        # Deduplicate by description
        seen: set[str] = set()
        unique: List[AcceptanceCriterion] = []
        for c in criteria:
            key = c.description.strip().lower()
            if key and key not in seen:
                seen.add(key)
                unique.append(c)
        return unique[:MAX_CRITERIA_PER_GOAL]

    def evaluate(
        self,
        goal: ExecutionGoal,
        evidence: IterationEvidence,
    ) -> GoalProgress:
        """Update criterion statuses from deterministic evidence."""
        for criterion in goal.acceptance_criteria:
            status, confidence, refs = self._evaluate_criterion(criterion, evidence)
            criterion.status = status
            criterion.confidence = confidence
            criterion.evidence = refs
            criterion.updated_at = _utcnow_iso()

        progress = GoalProgress(
            criteria_total=len(goal.acceptance_criteria),
            criteria_satisfied=sum(
                1 for c in goal.acceptance_criteria if c.status == CriterionStatus.SATISFIED
            ),
            criteria_unsatisfied=sum(
                1 for c in goal.acceptance_criteria if c.status == CriterionStatus.UNSATISFIED
            ),
            criteria_unknown=sum(
                1 for c in goal.acceptance_criteria if c.status == CriterionStatus.UNKNOWN
            ),
            criteria_blocked=sum(
                1 for c in goal.acceptance_criteria if c.status == CriterionStatus.BLOCKED
            ),
            iteration=evidence.iteration,
        )
        return progress

    def _evaluate_criterion(
        self,
        criterion: AcceptanceCriterion,
        evidence: IterationEvidence,
    ) -> tuple[CriterionStatus, float, List[EvidenceRef]]:
        """Evaluate a single criterion against deterministic evidence.

        Returns (status, confidence, evidence refs). A criterion is only
        marked SATISFIED when deterministic evidence supports it.
        """
        hint = (criterion.verification or "").lower()
        test_passed = evidence.test_status == "passed"
        test_failed = evidence.test_status == "failed"
        gate_approved = evidence.quality_gate_decision == "approved"
        gate_rejected = evidence.quality_gate_decision == "rejected"

        refs: List[EvidenceRef] = []

        if hint.startswith("test:"):
            name = hint.split(":", 1)[1].strip()
            if name in evidence.failing_test_names:
                refs.append(_test_ref(name, "failed"))
                return CriterionStatus.UNSATISFIED, 0.9, refs
            if test_passed:
                refs.append(_test_ref(name, "passed"))
                return CriterionStatus.SATISFIED, 0.9, refs
            if test_failed:
                refs.append(_test_ref(name, "unknown"))
                return CriterionStatus.UNKNOWN, 0.2, refs
            return CriterionStatus.UNKNOWN, 0.1, refs

        if hint == "suite:pass":
            if test_passed:
                refs.append(EvidenceRef(type=EvidenceType.TEST_RESULT,
                                        reference="passed",
                                        detail="Test suite passed", confidence=1.0))
                return CriterionStatus.SATISFIED, 1.0, refs
            if test_failed:
                refs.append(EvidenceRef(type=EvidenceType.TEST_RESULT,
                                        reference="failed",
                                        detail=f"{evidence.tests_failed} failing test(s)",
                                        confidence=1.0))
                return CriterionStatus.UNSATISFIED, 1.0, refs
            return CriterionStatus.UNKNOWN, 0.0, refs

        if hint == "gate:approved":
            if gate_approved:
                refs.append(EvidenceRef(type=EvidenceType.QUALITY_GATE,
                                        reference="approved",
                                        detail="Quality gate approved", confidence=1.0))
                return CriterionStatus.SATISFIED, 1.0, refs
            if gate_rejected:
                refs.append(EvidenceRef(type=EvidenceType.QUALITY_GATE,
                                        reference="rejected",
                                        detail="Quality gate rejected", confidence=1.0))
                return CriterionStatus.UNSATISFIED, 1.0, refs
            return CriterionStatus.UNKNOWN, 0.0, refs

        if hint.startswith("file:"):
            path = hint.split(":", 1)[1].strip()
            if any(path in f or f in path for f in evidence.changed_files):
                refs.append(EvidenceRef(type=EvidenceType.PATCH,
                                        reference=path,
                                        detail="File modified by patch", confidence=0.9))
                return CriterionStatus.SATISFIED, 0.8, refs
            return CriterionStatus.UNKNOWN, 0.2, refs

        if hint == "review:no_blocking":
            if evidence.review_findings == 0:
                refs.append(EvidenceRef(type=EvidenceType.REVIEW_FINDING,
                                        reference="none",
                                        detail="No blocking review findings", confidence=0.9))
                return CriterionStatus.SATISFIED, 0.8, refs
            refs.append(EvidenceRef(type=EvidenceType.REVIEW_FINDING,
                                    reference="blocking",
                                    detail=f"{evidence.review_findings} finding(s)",
                                    confidence=0.9))
            return CriterionStatus.UNSATISFIED, 0.8, refs

        # Default: functional/quality criteria need suite + gate evidence.
        if criterion.criterion_type in (CriterionType.SECURITY, CriterionType.QUALITY):
            if gate_approved and evidence.review_findings == 0:
                refs.append(EvidenceRef(type=EvidenceType.QUALITY_GATE,
                                        reference="approved",
                                        detail="Gate approved without findings", confidence=1.0))
                return CriterionStatus.SATISFIED, 0.9, refs
            if gate_rejected:
                return CriterionStatus.UNSATISFIED, 0.9, refs
            return CriterionStatus.UNKNOWN, 0.2, refs

        if test_passed and gate_approved:
            refs.append(EvidenceRef(type=EvidenceType.TEST_RESULT,
                                    reference="passed",
                                    detail="Tests passed with gate approved", confidence=1.0))
            return CriterionStatus.SATISFIED, 0.85, refs
        if test_failed:
            return CriterionStatus.UNSATISFIED, 0.8, refs
        if gate_rejected:
            return CriterionStatus.UNSATISFIED, 0.7, refs
        return CriterionStatus.UNKNOWN, 0.2, refs


def _req_type(req_type: RequirementType) -> CriterionType:
    mapping = {
        RequirementType.SECURITY: CriterionType.SECURITY,
        RequirementType.PERFORMANCE: CriterionType.PERFORMANCE,
        RequirementType.TEST: CriterionType.TEST,
    }
    return mapping.get(req_type, CriterionType.FUNCTIONAL)


def _test_ref(name: str, status: str) -> EvidenceRef:
    return EvidenceRef(
        type=EvidenceType.TEST_RESULT,
        reference=name,
        detail=f"Test '{name}' {status}",
        confidence=1.0 if status != "unknown" else 0.3,
    )


# ── ProgressEvaluator (§10) ─────────────────────────────────────


class ProgressEvaluator:
    """Compares iterations to detect measurable progress (or the lack of it)."""

    def evaluate(self, previous: GoalProgress, current: GoalProgress,
                 evidence: IterationEvidence) -> ProgressTrend:
        if evidence.failure_class == FailureClass.ENVIRONMENT:
            return ProgressTrend.BLOCKED
        if current.criteria_satisfied > previous.criteria_satisfied:
            return ProgressTrend.PROGRESSING
        if current.criteria_satisfied == previous.criteria_satisfied and previous.criteria_total == 0:
            return ProgressTrend.PROGRESSING
        # Same satisfaction, but fewer failing tests → still progressing.
        return ProgressTrend.STALLED


# ── StuckDetector (§11) ─────────────────────────────────────────


class StuckDetector:
    """Deterministic stuck detection from iteration fingerprints.

    Detects looping before budgets are exhausted:
    - same failing tests repeatedly
    - same error fingerprint repeatedly
    - repeated quality-gate rejection
    - no criteria improvement
    """

    def evaluate(self, state: AutonomousRunState) -> ProgressTrend:
        history = state.evidence_history
        if not history:
            return ProgressTrend.PROGRESSING

        # Environment failure blocks the whole run.
        if any(e.failure_class == FailureClass.ENVIRONMENT for e in history[-3:]):
            return ProgressTrend.BLOCKED

        # Same failing-test fingerprint for 3 consecutive iterations → LOOPING.
        if len(history) >= 3:
            last3 = history[-3:]
            if all(e.failing_test_fingerprint() for e in last3):
                fp = last3[0].failing_test_fingerprint()
                if all(e.failing_test_fingerprint() == fp for e in last3):
                    return ProgressTrend.LOOPING

        # Same error fingerprint for 3 consecutive iterations → STALLED.
        if len(history) >= 3:
            last3 = history[-3:]
            if all(e.error_fingerprint() for e in last3):
                fp = last3[0].error_fingerprint()
                if all(e.error_fingerprint() == fp for e in last3):
                    return ProgressTrend.STALLED

        # Repeated quality-gate rejection → STALLED.
        if len(history) >= 3:
            last3 = [e.quality_gate_decision for e in history[-3:]]
            if all(d == "rejected" for d in last3):
                return ProgressTrend.STALLED

        # No criteria improvement while tests failing → STALLED.
        checkpoints = state.checkpoints
        if len(checkpoints) >= 3:
            satisfied_counts = [c.progress.criteria_satisfied for c in checkpoints[-3:]]
            if len(set(satisfied_counts)) == 1 and history[-1].tests_failed > 0:
                return ProgressTrend.STALLED

        # Replanning to an identical plan → LOOPING.
        if len(state.plan_versions) >= 2:
            a = state.plan_versions[-2]
            b = state.plan_versions[-1]
            if a.plan_summary and a.plan_summary == b.plan_summary:
                return ProgressTrend.LOOPING

        return ProgressTrend.PROGRESSING


# ── ScopeController (§15) ───────────────────────────────────────


class ScopeController:
    """Enforces explicit task scope on changed files."""

    def check(self, state: AutonomousRunState, evidence: IterationEvidence) -> Optional[str]:
        """Return a scope-violation reason, or None if in scope."""
        scope = state.scope
        if not scope.allowed_modules and not scope.expected_change_area:
            return None  # no scope constraints — nothing to enforce

        violations: List[str] = []
        for f in evidence.changed_files:
            if any(forbidden in f for forbidden in scope.forbidden_areas):
                violations.append(f"forbidden:{f}")
            in_allowed = any(mod in f for mod in scope.allowed_modules) if scope.allowed_modules else True
            in_expected = any(area in f for area in scope.expected_change_area) if scope.expected_change_area else True
            if not in_allowed or not in_expected:
                violations.append(f"out_of_scope:{f}")

        if violations:
            scope.scope_expansion_requests += 1
            scope.violations = violations[:10]
            if not state.policy.allow_scope_expansion:
                return f"Scope violation on changed file(s): {', '.join(violations[:3])}"
        return None


# ── PlanVersionStore (§13/§14) ──────────────────────────────────


class PlanVersionStore:
    """Immutable plan history with replan validation."""

    def __init__(self) -> None:
        from app.services.plan_validator import PlanValidator
        self._validator = PlanValidator()

    def latest(self, state: AutonomousRunState) -> Optional[PlanVersion]:
        return state.plan_versions[-1] if state.plan_versions else None

    def record(
        self,
        state: AutonomousRunState,
        plan: Optional[ImplementationPlan],
        superseded_reason: Optional[str] = None,
        completed_steps: Optional[List[str]] = None,
        remaining_criteria: Optional[List[str]] = None,
        test_set: Optional[List[str]] = None,
    ) -> Optional[PlanVersion]:
        """Append a new plan version, superseding the previous one."""
        if plan is None:
            return None
        if state.plan_versions:
            prev = state.plan_versions[-1]
            if prev.status == "active":
                prev.status = "superseded"
                prev.superseded_reason = superseded_reason or "superseded by v%d" % (
                    len(state.plan_versions) + 1
                )
        version = PlanVersion(
            version=len(state.plan_versions) + 1,
            plan_summary=(plan.summary or "")[:500],
            plan_objective=(plan.objective or "")[:500],
            step_count=len(plan.steps or []),
            completed_steps=completed_steps or [],
            remaining_criteria=remaining_criteria or [],
            test_set=(test_set or [])[:50],
        )
        state.plan_versions.append(version)
        return version

    def validate_replan(
        self,
        state: AutonomousRunState,
        old_plan: Optional[ImplementationPlan],
        new_plan: Optional[ImplementationPlan],
    ) -> tuple[bool, List[str]]:
        """Reject invalid replans (§14):
        - must pass the existing deterministic PlanValidator
        - must not repeat an identical failed plan
        - must not drop unresolved requirements (non-empty steps)
        - must not silently expand scope beyond the goal
        """
        errors: List[str] = []
        if new_plan is None:
            return False, ["Replan produced no plan"]

        result = self._validator.validate(new_plan)
        if not result.is_valid:
            errors.extend(result.errors[:5])

        if not new_plan.steps:
            errors.append("Replan has no steps")

        if old_plan is not None:
            if old_plan.summary and old_plan.summary == new_plan.summary:
                errors.append("Replan repeats the identical failed plan")
            if old_plan.objective and old_plan.objective == new_plan.objective:
                # Same objective is fine; identical step IDs is the real signal.
                old_ids = {s.id for s in old_plan.steps}
                new_ids = {s.id for s in new_plan.steps}
                if old_ids and old_ids == new_ids:
                    errors.append("Replan repeats the identical step set")

        return not errors, errors


# ── AutonomousExecutionController (§7) ──────────────────────────
# Bounded goal-path retry (PROJECT_STATE item 13): a goal iteration whose
# run fails with the transient coding 'No patch produced' signature (empty
# patch / INSUFFICIENT_CONTEXT twice in a row — the ~20-25% Gemini variance
# the orchestrator's own item-12 stage retry could not absorb) is retried
# once with a FRESH run (2 attempts total, so each attempt stays in the
# audit trail). Only that signature retries — hard coding errors and
# environmental failures fail the iteration immediately.
_GOAL_RUN_MAX_ATTEMPTS = 2


class AutonomousExecutionController:
    """Bounded, auditable autonomous execution loop above the orchestrator."""

    def __init__(
        self,
        orchestration: Any = None,
        collaboration: Any = None,
        session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
        iteration_runner: Optional[Callable] = None,
        run_store: Optional[Any] = None,
        test_selector: Optional[Any] = None,
    ) -> None:
        self._orchestration = orchestration
        self._collaboration = collaboration
        self._factory = session_factory
        # Injectable deterministic iteration runner (used by tests).
        self._iteration_runner = iteration_runner
        # Durable run store for the autonomy path (defaults to in-memory when
        # no DB is available; PostgresRunStore when the schema is present).
        self._run_store = run_store
        # Impact-driven test selector (Phase 12d) used to pick the test set
        # for each replanned plan version. EKG impact edges (patch → test)
        # are the primary evidence source; an injected semantic-graph
        # selector remains as a fallback when the graph has no evidence.
        self._test_selector = test_selector

        self._goals: Dict[str, AutonomousRunState] = {}
        self._budget = BudgetManager()
        self._goal_evaluator = GoalEvaluator()
        self._progress_evaluator = ProgressEvaluator()
        self._stuck_detector = StuckDetector()
        self._scope_controller = ScopeController()
        self._plan_store = PlanVersionStore()
        # Lazily initialized Phase 17 reasoning engine (see _get_reasoning).
        self._reasoning: Any = None
        # Phase 18 — Engineering Knowledge Graph (see _get_engineering_graph).
        self._engineering_graph: Any = None

        self._cancellation: Dict[str, bool] = {}
        self._pause_requested: Dict[str, bool] = {}
        self._pending_input: Dict[str, str] = {}

    # ── Service accessors ────────────────────────────────────────

    async def _get_orchestration(self) -> Any:
        if self._orchestration is None:
            from app.services.orchestration_service import OrchestrationService
            self._orchestration = OrchestrationService(
                run_store=await self._get_run_store()
            )
        return self._orchestration

    async def _get_run_store(self) -> Any:
        """Return a durable run store when the schema supports it, else in-memory.

        PostgresRunStore is used so runs created by the autonomous loop land in
        the `runs` table (durable handoffs + full audit trail). The store is
        only selected after a capability probe confirms migration 008's
        `context_json` column exists — an unmigrated DB degrades gracefully to
        InMemoryRunStore instead of surfacing every run write as an ENVIRONMENT
        failure.
        """
        if self._run_store is not None:
            return self._run_store
        factory = self._get_factory()
        if factory is not None:
            try:
                from sqlalchemy import text

                async with factory() as session:
                    await session.execute(
                        text("SELECT context_json FROM runs LIMIT 1")
                    )
                from app.services.postgres_run_store import PostgresRunStore

                store = PostgresRunStore()
                store._session_factory = factory
                self._run_store = store
            except Exception as exc:
                logger.debug("Autonomy run store unavailable (in-memory): %s", exc)
                from app.services.run_store import InMemoryRunStore

                self._run_store = InMemoryRunStore()
        else:
            from app.services.run_store import InMemoryRunStore

            self._run_store = InMemoryRunStore()
        return self._run_store

    def _get_collaboration(self) -> Any:
        if self._collaboration is None:
            try:
                from app.services.collaboration_service import CollaborationService
                self._collaboration = CollaborationService(session_factory=self._factory)
            except Exception as exc:
                logger.debug("Collaboration unavailable: %s", exc)
                self._collaboration = None
        return self._collaboration

    def _get_reasoning(self) -> Any:
        """Lazily initialize the Phase 17 CollaborativeReasoningEngine.

        Degrades to None when unavailable. Shares the collaboration service
        so evidence and reasoning records stay consistent.
        """
        if getattr(self, "_reasoning", None) is not None:
            return self._reasoning
        try:
            from app.services.reasoning_service import CollaborativeReasoningEngine

            self._reasoning = CollaborativeReasoningEngine(
                session_factory=self._factory,
                collaboration=self._get_collaboration(),
            )
        except Exception as exc:
            logger.debug("CollaborativeReasoningEngine unavailable: %s", exc)
            self._reasoning = None
        return self._reasoning

    def _get_engineering_graph(self) -> Any:
        """Lazily initialize the Phase 18 EngineeringKnowledgeGraph.

        Prefers the orchestration service's graph when one exists — that is
        the instance that actually ingests runs (record_run), so replan test
        selection sees the real patch → test impact edges. Falls back to a
        controller-owned in-memory instance when the orchestrator is not yet
        available. Degrades to None when unavailable. The graph informs
        replanning and historical retrieval but NEVER overrides deterministic
        validation.
        """
        if getattr(self, "_engineering_graph", None) is not None:
            return self._engineering_graph
        # Reuse the orchestrator's graph (the one populated by run ingestion)
        # so EKG impact-edge test selection is live, not an empty instance.
        if self._orchestration is not None:
            try:
                shared = self._orchestration._get_engineering_graph()
                if shared is not None:
                    self._engineering_graph = shared
                    return shared
            except Exception as exc:
                logger.debug("Orchestrator EKG unavailable: %s", exc)
        try:
            from app.services.engineering_graph_service import (
                EngineeringKnowledgeGraphService,
            )

            self._engineering_graph = EngineeringKnowledgeGraphService(
                session_factory=self._factory
            )
        except Exception as exc:
            logger.debug("EngineeringKnowledgeGraph unavailable: %s", exc)
            self._engineering_graph = None
        return self._engineering_graph

    async def _refresh_consensus_topics(self, state: AutonomousRunState, run: Any) -> None:
        """Analyze a completed iteration run for consensus topics.

        Evidence-only: stores topic/status/decision summaries, never CoT.
        Consensus improves replanning decisions by adding shared reasoning
        to the rationale when a REPLAN is chosen.
        """
        reasoning = self._get_reasoning()
        if reasoning is None or run is None:
            return
        try:
            outcome = await reasoning.analyze_run(run)
            consensus = outcome.get("consensus") or []
            if not consensus:
                return
            state.consensus_topics = [
                f"{c.topic}:{c.status.value}:{c.final_decision[:60]}"
                for c in consensus[:10]
            ][:20]
        except Exception as exc:
            logger.debug("Consensus refresh failed for %s: %s", state.goal_id, exc)

    def _get_factory(self) -> Optional[async_sessionmaker[AsyncSession]]:
        if self._factory is None:
            try:
                from app.db.session import create_session_factory
                self._factory = create_session_factory()
            except Exception as exc:
                logger.debug("Autonomy DB unavailable (in-memory): %s", exc)
                self._factory = None
        return self._factory

    # ── Goal lifecycle ───────────────────────────────────────────

    async def create_goal(
        self,
        task: str,
        repository: Optional[str] = None,
        criteria_texts: Optional[List[str]] = None,
        constraints: Optional[List[str]] = None,
        budget: Optional[ExecutionBudget] = None,
        policy: Optional[AutonomyPolicy] = None,
        scope: Optional[TaskScope] = None,
    ) -> AutonomousRunState:
        """Create a goal and its acceptance criteria. No execution yet."""
        goal_id = f"GOAL-{_new_id().upper()[:8]}"
        criteria = self._goal_evaluator.extract_criteria(
            task=task, criteria_texts=criteria_texts
        )
        if not criteria:
            criteria = [AcceptanceCriterion(
                description="Task completed without regressions",
                verification="suite:pass",
            )]

        goal = ExecutionGoal(
            goal_id=goal_id,
            task=task[:500],
            repository=repository,
            acceptance_criteria=criteria,
            constraints=constraints or [],
            status=ExecutionState.RUNNING,
        )
        goal.progress = GoalProgress(
            criteria_total=len(criteria),
            criteria_unknown=len(criteria),
        )

        state = AutonomousRunState(
            goal_id=goal_id,
            task=task[:500],
            repository=repository,
            state=ExecutionState.RUNNING,
            goal=goal,
            budget=budget or ExecutionBudget(),
            policy=policy or AutonomyPolicy(),
            scope=scope or TaskScope(),
        )
        state.add_event(_EVENT_MAP["GOAL_CREATED"], f"Goal created: {task[:200]}")
        self._goals[goal_id] = state
        await self._persist_goal(state)
        await self._broadcast(state, "goal_created", "Goal created")
        return state

    # ── Dry run (§29) ────────────────────────────────────────────

    async def dry_run(
        self,
        task: str,
        repository: Optional[str] = None,
        criteria_texts: Optional[List[str]] = None,
    ) -> DryRunReport:
        """Estimate scope/budget/workflow WITHOUT any mutations."""
        criteria = self._goal_evaluator.extract_criteria(task, criteria_texts)
        warnings: List[str] = []
        if not criteria:
            warnings.append("No explicit criteria — a default regression criterion will be used")

        estimated_budget = {
            "max_iterations": DEFAULT_MAX_ITERATIONS,
            "max_replans": DEFAULT_MAX_REPLANS,
            "max_repairs": DEFAULT_MAX_REPAIRS,
            "max_agent_calls": DEFAULT_MAX_AGENT_CALLS,
            "max_llm_calls": DEFAULT_MAX_LLM_CALLS,
            "max_files_changed": DEFAULT_MAX_FILES_CHANGED,
            "max_test_runs": DEFAULT_MAX_TEST_RUNS,
        }
        estimated_scope = {
            "criteria_count": len(criteria),
            "repository": repository or "not specified",
        }

        return DryRunReport(
            task=task[:500],
            repository=repository,
            extracted_criteria=[
                {"description": c.description[:200], "type": c.criterion_type.value}
                for c in criteria
            ],
            estimated_scope=estimated_scope,
            estimated_budget=estimated_budget,
            likely_workflow=[
                "PLAN",
                "IMPLEMENT",
                "TEST",
                "REPAIR (on failure)",
                "REVIEW",
                "QUALITY GATE",
                "GOAL EVALUATION → DONE / REPLAN / ESCALATE",
            ],
            warnings=warnings,
            feasibility="ok",
        )

    # ── The autonomous loop (§7) ─────────────────────────────────

    async def start(self, goal_id: str) -> AutonomousRunState:
        """Run the bounded autonomous loop until a terminal state."""
        state = self._goals.get(goal_id)
        if state is None:
            state = await self._load_goal(goal_id)
        if state is None:
            raise KeyError(f"Goal {goal_id} not found")

        if state.state in (ExecutionState.COMPLETED, ExecutionState.STOPPED,
                           ExecutionState.FAILED, ExecutionState.CANCELLED):
            return state

        # §17/§37: a goal awaiting human input must NOT auto-resume.
        # Resume is only allowed via provide_input()/resume().
        if state.state == ExecutionState.WAITING_FOR_HUMAN:
            return state
        if state.state == ExecutionState.PAUSED:
            return state

        state.state = ExecutionState.RUNNING
        state.goal.status = ExecutionState.RUNNING

        while state.state == ExecutionState.RUNNING:
            # 1. Cancellation is authoritative (§18).
            if self._cancellation.get(goal_id, False):
                state.state = ExecutionState.CANCELLED
                state.goal.status = ExecutionState.CANCELLED
                await self._checkpoint(state, AutonomousAction.STOP, "cancelled")
                return state

            # 2. Pause between operations (§17).
            if self._pause_requested.get(goal_id, False):
                state.state = ExecutionState.PAUSED
                state.goal.status = ExecutionState.PAUSED
                await self._checkpoint(state, AutonomousAction.STOP, "paused")
                return state

            # 3. Decide next action based on the latest evidence. Completion
            # takes priority over budget exhaustion: a *provably complete*
            # goal (criteria satisfied + gate approved) completes even when a
            # non-iteration budget counter is at its limit. Budgets bound
            # expensive operations, not proven outcomes (§36).
            action, reason_code, rationale = self._decide(state)

            # 4. Handle terminal-ish actions.
            if action == AutonomousAction.COMPLETE:
                state.state = ExecutionState.COMPLETED
                state.goal.status = ExecutionState.COMPLETED
                state.add_event(_EVENT_MAP["GOAL_COMPLETED"],
                                f"Goal completed after {state.budget.iterations_used} iteration(s)")
                await self._record_decision(state, action, reason_code, rationale)
                await self._checkpoint(state, action, reason_code)
                await self._promote_to_memory(state)
                return state

            if action == AutonomousAction.ESCALATE:
                await self._escalate(
                    state,
                    EscalationReason.STUCK,
                    what_happened=rationale,
                    attempted=f"{state.budget.iterations_used} autonomous iteration(s)",
                    needed_input="Manual guidance or scope/requirement clarification",
                )
                state.state = ExecutionState.WAITING_FOR_HUMAN
                state.goal.status = ExecutionState.WAITING_FOR_HUMAN
                await self._record_decision(state, action, reason_code, rationale)
                await self._checkpoint(state, action, reason_code)
                return state

            if action == AutonomousAction.STOP:
                state.state = ExecutionState.STOPPED
                state.goal.status = ExecutionState.STOPPED
                await self._record_decision(state, action, reason_code, rationale)
                await self._checkpoint(state, action, reason_code)
                return state

            # 7. Budget check BEFORE expensive operations (§9). Runs after
            # the completion/stop decision so proven completion wins.
            #
            # Only GLOBAL limits stop the loop (iterations, agent calls, LLM
            # calls, test runs, files changed, execution time). The repair and
            # replan budgets are routed by _decide (REPAIR → REPLAN → ESCALATE),
            # so reaching one of them must not abort a run that still has a
            # viable next action.
            exhausted = self._budget.check(state)
            if exhausted and exhausted not in ("max_repairs", "max_replans"):
                state.add_event(_EVENT_MAP["BUDGET_EXHAUSTED"],
                                f"Budget exhausted: {exhausted}")
                await self._record_decision(state, AutonomousAction.STOP,
                                            "budget_exhausted",
                                            f"Execution budget exhausted ({exhausted})")
                if state.policy.allow_human_escalation:
                    await self._escalate(
                        state,
                        EscalationReason.BUDGET_EXHAUSTED,
                        what_happened=f"Execution budget exhausted ({exhausted})",
                        attempted="Bounded autonomous iterations",
                        needed_input="Approve additional budget or adjust scope",
                    )
                    state.state = ExecutionState.WAITING_FOR_HUMAN
                    state.goal.status = ExecutionState.WAITING_FOR_HUMAN
                else:
                    state.state = ExecutionState.STOPPED
                    state.goal.status = ExecutionState.STOPPED
                await self._checkpoint(state, AutonomousAction.STOP, "budget_exhausted")
                return state

            # 8. Budget warning (observability).
            warning = self._budget.check_warning(state)
            if warning:
                state.add_event(_EVENT_MAP["BUDGET_WARNING"], warning)

            # 9. Execute one iteration (CONTINUE / REPAIR / REPLAN / REVIEW).
            state.add_event(_EVENT_MAP["ITERATION_STARTED"],
                            f"Iteration {state.budget.iterations_used + 1}: {action.value}")
            await self._record_decision(state, action, reason_code, rationale)
            if action == AutonomousAction.REPLAN:
                self._budget.record_replan(state)
            if action == AutonomousAction.REPAIR:
                # Count the autonomy-level REPAIR decision toward the repair
                # budget so the controller cannot loop on repairs forever.
                state.budget.repairs_used = min(
                    state.budget.max_repairs, state.budget.repairs_used + 1
                )
            evidence = await self._run_iteration(state, action, reason_code)

            # 10. Record usage AFTER the operation.
            self._budget.record(state, evidence)
            state.evidence_history.append(evidence)
            state.evidence_history = state.evidence_history[-50:]

            # 10b. Record the plan version (§13). Immutable history — previous
            # versions are never overwritten. A NEW version is only recorded when
            # (a) this was an explicit REPLAN, or (b) the plan genuinely differs
            # from the latest version. CONTINUE/REPAIR iterations that reuse the
            # same plan must NOT create duplicate versions (which would trip the
            # identical-replan LOOPING detector on a healthy repair loop).
            latest_version = self._plan_store.latest(state)
            plan_changed = (
                latest_version is None
                or (evidence.plan_summary and latest_version.plan_summary != evidence.plan_summary)
            )
            if (evidence.plan_summary or evidence.plan_objective) and (
                action == AutonomousAction.REPLAN or plan_changed
            ):
                self._plan_store.record(
                    state,
                    ImplementationPlan(
                        summary=evidence.plan_summary or "",
                        objective=evidence.plan_objective or "",
                        steps=[],
                    ),
                    superseded_reason=(
                        "superseded by replan"
                        if action == AutonomousAction.REPLAN else None
                    ),
                    completed_steps=[
                        c.description[:100] for c in state.goal.acceptance_criteria
                        if c.status == CriterionStatus.SATISFIED
                    ][:20],
                    remaining_criteria=[
                        c.description[:100] for c in state.goal.acceptance_criteria
                        if c.status != CriterionStatus.SATISFIED
                    ][:20],
                    test_set=self._select_impact_tests(
                        evidence.changed_files, state.repository
                    ),
                )
                if action == AutonomousAction.REPLAN:
                    state.goal.replan_count += 1
                await self._persist_plan_versions(state)

            # 11. Evaluate criteria + progress.
            state.goal.attempt = state.budget.iterations_used
            previous = state.goal.progress
            new_progress = self._goal_evaluator.evaluate(state.goal, evidence)
            new_progress.previous_satisfied = previous.criteria_satisfied
            new_progress.improved_last_iteration = (
                new_progress.criteria_satisfied > previous.criteria_satisfied
            )
            trend = self._progress_evaluator.evaluate(previous, new_progress, evidence)
            new_progress.trend = trend
            state.goal.progress = new_progress

            # 12. Stuck detection (before next iteration). Track consecutive
            # stalled iterations so STALLED escalates before budget runs out (§11).
            if trend == ProgressTrend.STALLED:
                state.stalled_iterations += 1
            else:
                state.stalled_iterations = 0
            stuck = self._stuck_detector.evaluate(state)
            if stuck in (ProgressTrend.LOOPING, ProgressTrend.BLOCKED):
                state.add_event(_EVENT_MAP["STUCK_DETECTED"],
                                f"Stuck detected: {stuck.value}")
            if stuck == ProgressTrend.STALLED:
                state.add_event(_EVENT_MAP["STUCK_DETECTED"], "Progress stalled")

            # 13. Scope check.
            scope_reason = self._scope_controller.check(state, evidence)
            if scope_reason and not state.policy.allow_scope_expansion:
                await self._escalate(
                    state,
                    EscalationReason.SCOPE_EXPANSION,
                    what_happened=scope_reason,
                    attempted="Autonomous changes",
                    needed_input="Approve scope expansion or narrow the task",
                )
                state.state = ExecutionState.WAITING_FOR_HUMAN
                state.goal.status = ExecutionState.WAITING_FOR_HUMAN
                await self._checkpoint(state, AutonomousAction.ESCALATE, "scope_violation")
                return state

            # 14. Persist a durable checkpoint.
            await self._checkpoint(state, action, reason_code)

            # 15. Human escalation for ambiguous requirements (§16).
            if self._requires_human_input(state):
                await self._escalate(
                    state,
                    EscalationReason.AMBIGUOUS_REQUIREMENT,
                    what_happened="Task requirements are ambiguous",
                    attempted="Initial analysis",
                    needed_input="Clarify the acceptance criteria",
                )
                state.state = ExecutionState.WAITING_FOR_HUMAN
                state.goal.status = ExecutionState.WAITING_FOR_HUMAN
                await self._checkpoint(state, AutonomousAction.ESCALATE, "ambiguous_requirement")
                return state

        return state

    # ── Decision logic (§6) ──────────────────────────────────────

    def _decide(
        self,
        state: AutonomousRunState,
    ) -> tuple[AutonomousAction, str, str]:
        """Choose the next action from deterministic evidence.

        Returns (action, reason_code, rationale). No chain-of-thought.
        """
        evidence = state.evidence_history[-1] if state.evidence_history else None

        if evidence is None:
            return (AutonomousAction.CONTINUE, "initial_iteration",
                    "Begin: plan, implement, test, review, gate")

        progress = state.goal.progress
        all_satisfied = (
            progress.criteria_total > 0
            and progress.criteria_satisfied == progress.criteria_total
            and progress.criteria_unsatisfied == 0
        )
        gate_approved = evidence.quality_gate_decision == "approved"
        test_failed = evidence.test_status == "failed"

        # Stuck/looping takes priority over continuing. STALLED also escalates
        # once it persists for 3 consecutive iterations (§11: detect loops
        # before budgets are exhausted).
        stuck = self._stuck_detector.evaluate(state)
        if stuck in (ProgressTrend.LOOPING, ProgressTrend.BLOCKED):
            if state.policy.allow_human_escalation:
                return (AutonomousAction.ESCALATE, "stuck_detected",
                        f"Autonomous execution is {stuck.value} — escalating for guidance")
            return (AutonomousAction.STOP, "stuck_detected",
                    f"Autonomous execution is {stuck.value} — stopping")
        if stuck == ProgressTrend.STALLED and state.stalled_iterations >= 3:
            if state.policy.allow_human_escalation:
                return (AutonomousAction.ESCALATE, "stalled_detected",
                        "No measurable progress for 3 consecutive iterations — escalating")
            return (AutonomousAction.STOP, "stalled_detected",
                    "No measurable progress for 3 consecutive iterations — stopping")

        # Deterministic completion (§36).
        if all_satisfied and gate_approved:
            return (AutonomousAction.COMPLETE, "criteria_satisfied_gate_approved",
                    "All acceptance criteria satisfied and quality gate approved")

        if all_satisfied and not gate_approved:
            return (AutonomousAction.REVIEW, "criteria_satisfied_gate_pending",
                    "Criteria satisfied; review pending quality gate")

        # Consensus-aware replan rationale (Phase 17): shared engineering
        # consensus may strengthen or refine the replan reason, but never
        # overrides deterministic evidence — it only adds evidence-only
        # context to the recorded rationale.
        consensus_note = ""
        if state.consensus_topics:
            consensus_note = " | consensus: " + "; ".join(state.consensus_topics[:3])

        # Tests failing → repair (bounded), then replan. Repair is the
        # cheapest next step when test evidence is the problem; the gate is
        # a review-stage signal and must not pre-empt repair (§7 flow).
        if test_failed:
            if state.policy.allow_repair and state.budget.repairs_used < state.budget.max_repairs:
                return (AutonomousAction.REPAIR, "tests_failing",
                        f"{evidence.tests_failed} test(s) failing — repairing")
            if state.policy.allow_replan and state.budget.replans_used < state.budget.max_replans:
                return (AutonomousAction.REPLAN, "plan_inadequate",
                        "Repair budget consumed — replanning with failure evidence" + consensus_note)
            if state.policy.allow_human_escalation:
                return (AutonomousAction.ESCALATE, "repair_replan_exhausted",
                        "Repair and replan budgets consumed with tests still failing")
            return (AutonomousAction.STOP, "repair_replan_exhausted",
                    "Repair and replan budgets consumed with tests still failing")

        # Gate rejected with tests passing → replan (if allowed and budget remains).
        if evidence.quality_gate_decision == "rejected":
            if state.policy.allow_replan and state.budget.replans_used < state.budget.max_replans:
                return (AutonomousAction.REPLAN, "quality_gate_rejected",
                        "Quality gate rejected — replanning with new strategy" + consensus_note)
            if state.policy.allow_human_escalation:
                return (AutonomousAction.ESCALATE, "quality_gate_rejected",
                        "Quality gate rejected with no replan budget")
            return (AutonomousAction.STOP, "quality_gate_rejected",
                    "Quality gate rejected with no replan budget")

        # Tests passed but gate not yet approved → continue to review.
        if evidence.test_status == "passed" and not gate_approved:
            return (AutonomousAction.REVIEW, "tests_passed_gate_pending",
                    "Tests passed; reviewing before the quality gate")

        return (AutonomousAction.CONTINUE, "continue",
                "Iterating with current plan")


    # ── Iteration execution ──────────────────────────────────────

    async def _run_iteration(
        self,
        state: AutonomousRunState,
        action: AutonomousAction,
        reason_code: str,
    ) -> IterationEvidence:
        """Run one iteration. Default: drive the real orchestrator.

        Tests inject a deterministic `iteration_runner` instead.

        Bounded goal-path retry (PROJECT_STATE item 13): the orchestrator's
        `_stage_coding` already retries once per run (item 12), but the live
        goal path still flaked when Gemini returned INSUFFICIENT_CONTEXT twice
        in a row — a run that fails with 'No patch produced'. This level
        retries the WHOLE run once with a FRESH run so each attempt stays in
        the audit trail. Only the transient coding signature retries; hard
        coding errors and environmental failures fail the iteration
        immediately. Bounded by `_GOAL_RUN_MAX_ATTEMPTS` (= 2 attempts = 1
        retry). Driven by the shared `run_bounded_retry` helper (Phase 19)
        — the attempt body is the `attempt_fn`, the transient-coding
        detector is both the `is_success`/`should_retry` predicates, and
        exceptions propagate to the caller where they map to ENVIRONMENT
        evidence (never retried).
        """
        if self._iteration_runner is not None:
            evidence = await self._iteration_runner(state, action, reason_code)
            return evidence

        orch = await self._get_orchestration()
        repo = state.repository or ""

        # A FRESH run per attempt keeps a retried attempt in the audit trail
        # (same pre-population pattern as the Phase 15 demo / orchestrator
        # tests: skips acquisition/analysis so the controller focuses on the
        # collaboration layer). Track the last run id so an environmental
        # exception still reports which run it occurred on.
        last_run_id: Optional[str] = None

        async def _attempt(_attempt_no: int):
            nonlocal last_run_id
            run_id = f"RUN-{_new_id().upper()[:8]}"
            last_run_id = run_id
            source = RunSource(
                source_type=RunSourceType.USER_TASK,
                title=state.task[:200],
                repository_path=repo,
            )
            run = await orch.create_run(source)
            run.repository_path = repo
            # Pre-populate repository_profile so execute_run skips the
            # analysis stage. Without it the analysis stage transitions
            # from the run's INITIALIZING stage, which the strict state
            # machine rejects (only initializing -> acquiring_repository
            # is allowed). This path was never exercised in CI (tests
            # inject an iteration runner) — the real execute_run path is
            # what the live-LLM demo validates.
            if repo:
                try:
                    from app.workflows.repository_analysis import (
                        RepositoryAnalysisWorkflow,
                    )
                    from app.models.profile import RepositoryProfile

                    # The workflow returns an AnalysisState dataclass
                    # wrapping the RepositoryProfile — extract `.profile`
                    # so the field holds the real model (which the durable
                    # store round-trips). Never fall back to the dataclass
                    # itself (it cannot round-trip); use a minimal valid
                    # RepositoryProfile instead.
                    analysis_state = await RepositoryAnalysisWorkflow().run(repo)
                    profile = getattr(analysis_state, "profile", None)
                    run.repository_profile = (
                        profile
                        if profile is not None
                        else RepositoryProfile(name=Path(repo).name or "repository")
                    )
                except Exception as exc:
                    logger.warning(
                        "Autonomy repository analysis unavailable (%s); using stub profile", exc
                    )
                    from app.models.profile import RepositoryProfile

                    run.repository_profile = RepositoryProfile(
                        name=Path(repo).name or "repository"
                    )
            run.requirements = self._requirements_from_goal(state)
            # NOTE: retrieved_context is intentionally left UNSET so the
            # real retrieval stage runs and the coding agent sees actual
            # repository code. A zero-item stub made the coding LLM return
            # INSUFFICIENT_CONTEXT (it must not hallucinate file contents),
            # surfacing as 'No patch produced'.

            # Reuse the latest plan unless we are replanning.
            if action == AutonomousAction.REPLAN:
                run.plan = None  # force the planner to produce a new plan
                # First real stage is planning (ANALYZING_TASK -> PLANNING).
                run.current_stage = StageType.ANALYZING_TASK
            else:
                latest = self._plan_store.latest(state)
                if latest is not None:
                    run.plan = self._plan_from_version(latest)
                    # Planning will be skipped (plan pre-populated): start
                    # at PLANNING so retrieval's _complete_stage can advance
                    # to CODING via a valid PLANNING -> RETRIEVING_CONTEXT
                    # transition.
                    run.current_stage = StageType.PLANNING
                else:
                    run.current_stage = StageType.ANALYZING_TASK

            await orch._store.update(run)
            result = await orch.execute_run(run.run_id, workspace_root=repo)
            # With a durable store, execute_run re-hydrates the run from
            # the DB, so the in-memory `run` object is stale afterwards.
            # Re-fetch so evidence reflects the persisted stage outputs
            # (test/gate/repair). For InMemoryRunStore the same object is
            # returned (no-op).
            fresh = await orch._store.get(run.run_id)
            if fresh is not None:
                run = fresh
            return run, result

        def _on_retry(attempt_no: int, rr):
            run, _result = rr
            # Item 13: transient coding variance retries the whole run ONCE
            # with a fresh run. The live gate proved (Session 18) Gemini's
            # variance surfaces through MULTIPLE signatures — 'No patch
            # produced' (empty patch / INSUFFICIENT_CONTEXT), 'No changes
            # found in LLM output', and malformed LLM JSON ('Failed to parse
            # LLM output as JSON') — so ANY CODING_FAILED run failure is
            # retryable at this level. The retry is bounded; a genuinely
            # broken pipeline fails the second attempt too, so the gate
            # still fails. Environmental/non-coding failures never retry.
            state.add_event(
                EventType.RUN_RETRY,
                f"Goal run attempt {attempt_no} failed at coding with "
                f"transient variance ({run.run_id}); retrying once "
                "with a fresh run",
            )
            logger.warning(
                "Autonomy iteration %s attempt %d transient coding "
                "failure — retrying once", state.goal_id, attempt_no)

        try:
            outcome = await run_bounded_retry(
                attempt_fn=_attempt,
                is_success=lambda rr: not self._is_transient_coding_failure(rr[0]),
                should_retry=lambda rr: self._is_transient_coding_failure(rr[0]),
                max_attempts=_GOAL_RUN_MAX_ATTEMPTS,
                on_retry=_on_retry,
            )
        except Exception as exc:
            logger.warning(
                "Autonomy iteration failed for goal %s: %s",
                state.goal_id, exc)
            # Environmental/hard exceptions are NOT the transient coding
            # variance — fail the iteration immediately (no retry).
            return IterationEvidence(
                iteration=state.budget.iterations_used + 1,
                run_id=last_run_id or "",
                failure_class=FailureClass.ENVIRONMENT,
                failure_message=(str(exc) or "Autonomy iteration failed")[:300],
            )

        run, result = outcome.result
        evidence = self._evidence_from_run(
            run, result, state.budget.iterations_used + 1)
        # Phase 17: analyze shared evidence → consensus topics for this
        # run. Enriches replanning rationale with evidence-only consensus.
        await self._refresh_consensus_topics(state, run)
        return evidence

    @staticmethod
    def _is_transient_coding_failure(run: Any) -> bool:
        """True when a run failed at coding with the LLM's transient variance.

        Any `CODING_FAILED` run failure is retryable at the goal level. The
        live gate (Session 18) proved Gemini's variance surfaces through
        MULTIPLE signatures — a valid-but-empty patch set ('Coding agent
        produced no changes'), an exhausted INSUFFICIENT_CONTEXT refusal, an
        empty `changes` array ('No changes found in LLM output'), and
        malformed LLM JSON ('Failed to parse LLM output as JSON: ...'). The
        orchestrator's `_stage_coding` retry (item 12) only covers the first
        two (its 'status=error is deterministic' assumption is empirically
        false for Gemini); this goal-level retry is the bounded safety net
        for ALL coding-output variance. The retry is bounded (one fresh run)
        and a genuinely broken pipeline fails the second attempt too, so the
        gate still fails — no masking of deterministic failures.
        """
        failure = getattr(run, "failure", None)
        if failure is None:
            return False
        code = getattr(failure, "code", None)
        return code == FailureCode.CODING_FAILED

    def _select_impact_tests(
        self,
        changed_files: List[str],
        repository: Optional[str] = None,
    ) -> List[str]:
        """Select tests covering the changed files, EKG-impact-edge driven.

        Phase 12d closure: the test set for a replanned plan version is now
        selected from Engineering Knowledge Graph impact edges (patch → test)
        that the EKG persists for every ingested run — no lazy per-repo
        semantic-graph re-index. When the EKG has no evidence for the changed
        files, an injected semantic-graph selector (TestSelectionService)
        remains as a fallback. Gracefully degrades to an empty list when
        neither source is available (never raises) — the same pattern as the
        rest of the codebase.
        """
        if not changed_files:
            return []
        try:
            graph = self._get_engineering_graph()
            if graph is not None:
                graph_tests = graph.select_tests_for_changes(changed_files)
                if graph_tests:
                    return graph_tests[:20]
            if self._test_selector is not None:
                result = self._test_selector.select_for_changed_files(
                    changed_files=changed_files
                )
                return result.file_paths[:20]
            return []
        except Exception as exc:
            logger.debug("Impact test selection unavailable: %s", exc)
            return []

    def _requirements_from_goal(self, state: AutonomousRunState) -> StructuredRequirements:
        reqs = [
            Requirement(
                description=c.description[:300],
                requirement_type=_criterion_to_req_type(c.criterion_type),
                acceptance_note=c.verification,
            )
            for c in state.goal.acceptance_criteria[:10]
        ]
        return StructuredRequirements(
            objective=state.task[:300],
            requirements=reqs,
            constraints=[{"description": c} for c in state.goal.constraints[:10]],
        )

    def _plan_from_version(self, version: PlanVersion) -> Optional[ImplementationPlan]:
        """Reconstruct a continuation plan from the persisted version.

        Only reused when the plan actually had steps; a version with no
        steps (e.g. a failed initial plan) returns None so the planner
        re-runs instead of executing a hollow plan.
        """
        if not version.plan_objective or version.step_count == 0:
            return None
        test_strategy = "autonomous continuation"
        if version.test_set:
            test_strategy = "impact-driven tests: " + ", ".join(version.test_set[:8])
        return ImplementationPlan(
            summary=version.plan_summary or "",
            objective=version.plan_objective,
            steps=[
                ImplementationStep(
                    id=f"STEP-C{version.version:02d}",
                    title=version.plan_summary or "Continue plan",
                    description="Continuation of persisted plan version %d" % version.version,
                )
            ],
            test_strategy=test_strategy,
        )

    def _evidence_from_run(self, run: Any, result: Any, iteration: int) -> IterationEvidence:
        test_result = getattr(run, "test_result", None)
        gate = getattr(run, "quality_gate_result", None)
        review = getattr(run, "review_report", None)
        repair = getattr(run, "repair_result", None)
        failure = getattr(run, "failure", None)

        failing_names: List[str] = []
        if test_result and test_result.failures:
            failing_names = [
                getattr(f, "test_name", getattr(f, "name", ""))
                for f in test_result.failures[:50]
                if getattr(f, "test_name", getattr(f, "name", ""))
            ]

        changed_files: List[str] = []
        if run.patch_set and run.patch_set.changes:
            changed_files = [c.path for c in run.patch_set.changes[:100]]

        changed_symbols: List[str] = []
        if run.patch_result:
            changed_symbols = [
                s for s in (getattr(run.patch_result, "changed_symbols", None) or [])
                if isinstance(s, str)
            ][:100]

        test_status = test_result.status.value if test_result else None
        gate_decision = gate.decision.value if gate else None

        failure_code = failure.code.value if failure and failure.code else None
        failure_class = classify_failure(failure_code, test_status)
        failure_message = (failure.message or "")[:300] if failure else ""

        plan = getattr(run, "plan", None)
        return IterationEvidence(
            iteration=iteration,
            run_id=run.run_id,
            test_status=test_status,
            tests_total=getattr(test_result, "tests_total", 0) or 0,
            tests_passed=getattr(test_result, "tests_passed", 0) or 0,
            tests_failed=getattr(test_result, "tests_failed", 0) or 0,
            failing_test_names=failing_names,
            quality_gate_decision=gate_decision,
            review_findings=len(review.findings or []) if review else 0,
            changed_files=changed_files,
            changed_symbols=changed_symbols,
            repair_attempts=getattr(repair, "attempts", 0) or 0,
            failure_code=failure_code,
            failure_class=failure_class,
            failure_message=failure_message,
            plan_summary=(plan.summary or "") if plan else "",
            plan_objective=(plan.objective or "") if plan else "",
            plan_step_count=len(plan.steps or []) if plan else 0,
        )

    # ── Replanning (§12) ─────────────────────────────────────────

    def _requires_human_input(self, state: AutonomousRunState) -> bool:
        """Detect ambiguous requirements deterministically."""
        if not state.goal.acceptance_criteria:
            return True
        if not state.task or len(state.task.strip()) < 10:
            return True
        return False

    # ── Live broadcasts (Phase 16 WebSocket) ────────────────────

    async def _broadcast(
        self,
        state: AutonomousRunState,
        event_type: str,
        message: str = "",
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Emit a live autonomy event to WebSocket clients.

        Fire-and-forget: failures are logged, never propagated. Every event
        carries a full status snapshot so dashboard clients stay live without
        polling.
        """
        try:
            ws = _get_ws_manager()
            if ws.active_connections == 0:
                return
            data: Dict[str, Any] = {"status": state.status_summary()}
            if extra:
                data.update(extra)
            await ws.broadcast_autonomy(
                state.goal_id, event_type, data, message=message
            )
        except Exception as exc:
            logger.debug("Autonomy broadcast failed (non-critical): %s", exc)

    def _decision_snapshot(self, decision: AutonomousDecision) -> Dict[str, Any]:
        """Bounded public decision shape (mirrors the API serializer)."""
        return {
            "decision_id": decision.decision_id,
            "iteration": decision.iteration,
            "action": decision.action.value,
            "reason_code": decision.reason_code[:100],
            "rationale": decision.rationale[:200],
            "evidence_refs": [
                {
                    "type": e.type.value,
                    "reference": e.reference[:100],
                    "confidence": round(float(e.confidence), 2),
                }
                for e in decision.evidence_refs[:5]
            ],
            "timestamp": decision.timestamp,
        }

    # ── Escalation (§16) ─────────────────────────────────────────

    async def _escalate(
        self,
        state: AutonomousRunState,
        reason: EscalationReason,
        what_happened: str,
        attempted: str,
        needed_input: str,
    ) -> HumanEscalation:
        escalation = HumanEscalation(
            goal_id=state.goal_id,
            reason=reason,
            what_happened=what_happened[:300],
            attempted=attempted[:300],
            needed_input=needed_input[:300],
            remaining_criteria=[
                c.description[:200] for c in state.goal.acceptance_criteria
                if c.status != CriterionStatus.SATISFIED
            ][:10],
        )
        state.escalations.append(escalation)
        state.escalations = state.escalations[-10:]
        state.add_event(_EVENT_MAP["ESCALATION_CREATED"],
                        f"Escalation ({reason.value}): {needed_input[:150]}")
        await self._persist_escalation(state, escalation)
        await self._broadcast(
            state,
            "escalation",
            f"Escalation ({reason.value})",
            extra={"escalation": escalation.summary()},
        )
        return escalation

    # ── Pause / Resume / Cancel (§17/§18) ────────────────────────

    async def pause(self, goal_id: str) -> AutonomousRunState:
        state = self._goals.get(goal_id) or await self._load_goal(goal_id)
        if state is None:
            raise KeyError(f"Goal {goal_id} not found")
        if state.state in (ExecutionState.RUNNING, ExecutionState.RESUMING,
                           ExecutionState.WAITING_FOR_HUMAN):
            self._pause_requested[goal_id] = True
        return state

    async def resume(self, goal_id: str) -> AutonomousRunState:
        """Resume a paused run and re-enter the autonomous loop (§17).

        The loop runs inline until the next terminal/blocking state, so
        resume() drives execution to completion (or the next pause/escalation).
        """
        state = self._goals.get(goal_id) or await self._load_goal(goal_id)
        if state is None:
            raise KeyError(f"Goal {goal_id} not found")
        if state.state in (ExecutionState.PAUSED, ExecutionState.WAITING_FOR_HUMAN):
            self._pause_requested[goal_id] = False
            state.state = ExecutionState.RESUMING
            state.goal.status = ExecutionState.RESUMING
            await self._persist_goal(state)
        # Re-enter the loop (RESUMING passes the WAITING/PAUSED guard).
        return await self.start(goal_id)

    async def cancel(self, goal_id: str) -> AutonomousRunState:
        state = self._goals.get(goal_id) or await self._load_goal(goal_id)
        if state is None:
            raise KeyError(f"Goal {goal_id} not found")
        self._cancellation[goal_id] = True
        state.state = ExecutionState.CANCELLED
        state.goal.status = ExecutionState.CANCELLED
        await self._checkpoint(state, AutonomousAction.STOP, "cancelled")
        await self._persist_goal(state)
        return state

    async def provide_input(self, goal_id: str, clarification: str) -> AutonomousRunState:
        """Resolve a human escalation with clarification (§16)."""
        state = self._goals.get(goal_id) or await self._load_goal(goal_id)
        if state is None:
            raise KeyError(f"Goal {goal_id} not found")
        if state.state != ExecutionState.WAITING_FOR_HUMAN:
            return state
        self._pending_input[goal_id] = clarification[:500]
        resolved: List[HumanEscalation] = []
        for escalation in state.escalations:
            if escalation.status == EscalationStatus.OPEN:
                escalation.status = EscalationStatus.RESOLVED
                escalation.resolution = clarification[:300]
                escalation.resolved_at = _utcnow_iso()
                resolved.append(escalation)
        for escalation in resolved:
            await self._persist_escalation(state, escalation)
        state.state = ExecutionState.RESUMING
        state.goal.status = ExecutionState.RESUMING
        await self._persist_goal(state)
        # Re-enter the loop with the human clarification (§17/§16).
        return await self.start(goal_id)

    # ── Decisions / Checkpoints / Recovery ───────────────────────

    async def _record_decision(
        self,
        state: AutonomousRunState,
        action: AutonomousAction,
        reason_code: str,
        rationale: str,
    ) -> None:
        decision = AutonomousDecision(
            goal_id=state.goal_id,
            iteration=state.budget.iterations_used + 1,
            action=action,
            reason_code=reason_code[:100],
            rationale=rationale[:300],
        )
        state.decisions.append(decision)
        state.decisions = state.decisions[-100:]
        await self._persist_decision(state, decision)
        await self._broadcast(
            state,
            "decision",
            f"Decision: {action.value} ({reason_code})",
            extra={"decision": self._decision_snapshot(decision)},
        )

    async def _checkpoint(
        self,
        state: AutonomousRunState,
        action: AutonomousAction,
        reason_code: str,
    ) -> None:
        """Persist a durable checkpoint with optimistic concurrency.

        Read version → decide → atomic compare-and-swap update. On conflict
        the memory version is restored and a ConcurrencyConflictError raised
        so callers can retry safely or abort (§27).
        """
        expected_version = state.version
        checkpoint = AutonomousCheckpoint(
            goal_id=state.goal_id,
            iteration=state.budget.iterations_used,
            state=state.state,
            action=action,
            reason_code=reason_code[:100],
            plan_version=len(state.plan_versions),
            budget_usage=state.budget.usage(),
            progress=state.goal.progress,
            version=expected_version + 1,
        )
        state.checkpoints.append(checkpoint)
        state.checkpoints = state.checkpoints[-50:]
        state.version = expected_version + 1
        state.updated_at = _utcnow_iso()

        persisted = await self._persist_checkpoint(state, checkpoint, expected_version)
        if not persisted:
            # Optimistic-concurrency conflict: another worker advanced the row.
            logger.warning("Checkpoint version conflict for %s — aborting write", state.goal_id)
            state.version = expected_version  # restore (we did not win the CAS)
            state.checkpoints = state.checkpoints[:-1]
            fresh = await self._load_goal(state.goal_id)
            if fresh is not None and fresh.version > expected_version:
                # Abort safely: another worker advanced the run.
                raise ConcurrencyConflictError(
                    f"Concurrent autonomous worker advanced goal {state.goal_id} "
                    f"(version {expected_version} -> {fresh.version})"
                )
            return
        await self._broadcast(
            state, "status", f"Checkpoint iteration {state.budget.iterations_used}"
        )

    # ── Graceful concurrency conflict (§27) ─────────────────────
    # The autonomous loop aborts safely when another worker advanced the
    # same goal. The conflict is surfaced as an error; the run is not
    # corrupted and may be reloaded from its last durable checkpoint.

    # ── Status accessors ─────────────────────────────────────────

    async def get_status(self, goal_id: str) -> AutonomousRunState:
        state = self._goals.get(goal_id) or await self._load_goal(goal_id)
        if state is None:
            raise KeyError(f"Goal {goal_id} not found")
        return state

    async def get_progress(self, goal_id: str) -> GoalProgress:
        state = await self.get_status(goal_id)
        return state.goal.progress

    async def get_decisions(self, goal_id: str) -> List[AutonomousDecision]:
        state = await self.get_status(goal_id)
        return state.decisions

    async def list_goals(
        self,
        limit: int = 50,
        state: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List known goals (in-memory + persisted) with open escalations.

        Used by the dashboard goal browser and the human escalation queue.
        In-memory goals always win; persisted rows (recovery across restarts)
        are merged in best-effort when a DB is available. Pass `state` to
        filter by a specific ExecutionState value (e.g. "running",
        "waiting_for_human", "completed").
        """
        goals: Dict[str, Dict[str, Any]] = {}

        def _in_filter(value: str) -> bool:
            return state is None or value == state

        # In-memory goals (authoritative, latest state)
        for goal_id, st in self._goals.items():
            value = st.state.value
            if not _in_filter(value):
                continue
            goals[goal_id] = {
                "goal_id": goal_id,
                "task": st.task[:200],
                "repository": st.repository,
                "state": value,
                "open_escalations": [
                    e.summary() for e in st.escalations
                    if e.status.value == "open"
                ][:5],
                "updated_at": (
                    st.checkpoints[-1].persisted_at
                    if st.checkpoints else None
                ),
            }

        # Persisted goals (recovery across restarts) — best-effort merge
        async def _load_persisted(session: AsyncSession) -> None:
            from app.db.models import ExecutionGoalModel, HumanEscalationModel

            rows = (
                await session.execute(
                    select(ExecutionGoalModel).order_by(
                        ExecutionGoalModel.updated_at.desc()
                    ).limit(limit)
                )
            ).scalars().all()
            open_escs = (
                await session.execute(
                    select(HumanEscalationModel).where(
                        HumanEscalationModel.status == "open"
                    )
                )
            ).scalars().all()
            esc_by_goal: Dict[str, List[Dict[str, Any]]] = {}
            for esc in open_escs:
                esc_by_goal.setdefault(esc.goal_id, []).append({
                    "escalation_id": esc.escalation_id,
                    "reason": esc.reason,
                    "what_happened": (esc.what_happened or "")[:200],
                    "attempted": (esc.attempted or "")[:200],
                    "needed_input": (esc.needed_input or "")[:200],
                    "status": esc.status,
                })

            for row in rows:
                if not _in_filter(row.state):
                    continue
                entry = goals.get(row.goal_id)
                if entry is not None:
                    known = {e["escalation_id"] for e in entry["open_escalations"]}
                    for esc in esc_by_goal.get(row.goal_id, []):
                        if esc["escalation_id"] not in known:
                            entry["open_escalations"].append(esc)
                            known.add(esc["escalation_id"])
                    continue
                goals[row.goal_id] = {
                    "goal_id": row.goal_id,
                    "task": (row.task or "")[:200],
                    "repository": row.repository,
                    "state": row.state,
                    "open_escalations": esc_by_goal.get(row.goal_id, [])[:5],
                    "updated_at": (
                        row.updated_at.isoformat() if row.updated_at else None
                    ),
                }

        await self._with_session(_load_persisted)
        return list(goals.values())[:limit]

    # ── Recovery (§25) ───────────────────────────────────────────

    async def recover(self, goal_id: str) -> AutonomousRunState:
        """Restart services and resume from the last safe checkpoint."""
        state = await self._load_goal(goal_id)
        if state is None:
            raise KeyError(f"Goal {goal_id} not found")
        self._goals[goal_id] = state
        if state.checkpoints:
            last = state.checkpoints[-1]
            logger.info("Recovering goal %s from checkpoint iteration %d",
                        goal_id, last.iteration)
        return state

    # ── Memory promotion (§23) ───────────────────────────────────

    async def _promote_to_memory(self, state: AutonomousRunState) -> None:
        """Promote verified completion knowledge to repository memory."""
        collab = self._get_collaboration()
        if collab is None or not state.repository:
            return
        try:
            repo_id = state.repository.rstrip("/\\").split("/")[-1].split("\\")[-1]
            from app.models.memory import (
                MemoryEvidence,
                MemoryStatus,
                MemoryType,
                RepositoryMemory,
            )
            memory_service = getattr(collab, "_memory_service", None)
            if memory_service is None:
                return
            await memory_service.create_memory(RepositoryMemory(
                memory_id=f"mem_{state.goal_id[:8].lower()}_goal",
                repository_id=repo_id,
                memory_type=MemoryType.SUCCESSFUL_CHANGE,
                status=MemoryStatus.VERIFIED,
                content=(
                    f"Autonomous goal {state.goal_id} completed: {state.task[:200]}"
                ),
                confidence=0.9,
                evidence=[MemoryEvidence(
                    source_type="autonomy",
                    source_id=state.goal_id,
                    description="Goal completed with quality gate approval",
                )],
                source_run_id=state.goal_id,
                tags=["autonomy", "goal_completed"],
            ))
        except Exception as exc:
            logger.debug("Memory promotion (autonomy) skipped: %s", exc)

    # ── Persistence plumbing ─────────────────────────────────────

    async def _with_session(self, callback, fallback: Any = None) -> Any:
        factory = self._get_factory()
        if factory is None:
            return fallback
        try:
            async with factory() as session:
                return await callback(session)
        except Exception as exc:
            logger.debug("Autonomy DB op failed (in-memory fallback): %s", exc)
            return fallback

    async def _persist_goal(self, state: AutonomousRunState) -> None:
        from app.db.models import ExecutionGoalModel

        async def _impl(session: AsyncSession) -> None:
            # Query by the unique goal_id column — the table PK is an integer
            # `id`, so session.get(pk, goal_id) would fail against a real DB.
            model = (
                await session.execute(
                    select(ExecutionGoalModel).where(
                        ExecutionGoalModel.goal_id == state.goal_id
                    )
                )
            ).scalar_one_or_none()
            if model is None:
                model = ExecutionGoalModel(
                    goal_id=state.goal_id,
                    task=state.task[:500],
                    repository=state.repository,
                    state=state.state.value,
                    goal_json=state.goal.model_dump(),
                    budget_json=state.budget.model_dump(),
                    policy_json=state.policy.model_dump(),
                    scope_json=state.scope.model_dump(),
                    version=state.version,
                )
                session.add(model)
            else:
                model.task = state.task[:500]
                model.repository = state.repository
                model.state = state.state.value
                model.goal_json = state.goal.model_dump()
                model.budget_json = state.budget.model_dump()
                model.policy_json = state.policy.model_dump()
                model.scope_json = state.scope.model_dump()
                model.version = state.version
            await session.commit()

        await self._with_session(_impl)

    async def _persist_plan_versions(self, state: AutonomousRunState) -> None:
        """Persist plan versions (immutable history) for restart recovery.

        Inserts new versions and updates existing rows' superseded status so
        §13 'why superseded' survives a restart.
        """
        from app.db.models import PlanVersionModel

        async def _impl(session: AsyncSession) -> None:
            for version in state.plan_versions:
                existing = (
                    await session.execute(
                        select(PlanVersionModel).where(
                            PlanVersionModel.goal_id == state.goal_id,
                            PlanVersionModel.version == version.version,
                        )
                    )
                ).scalar_one_or_none()
                if existing is None:
                    session.add(PlanVersionModel(
                        goal_id=state.goal_id,
                        version=version.version,
                        plan_summary=version.plan_summary,
                        plan_objective=version.plan_objective,
                        step_count=version.step_count,
                        status=version.status,
                        superseded_reason=version.superseded_reason,
                        completed_steps=version.completed_steps or None,
                        remaining_criteria=version.remaining_criteria or None,
                        test_set=version.test_set or None,
                    ))
                else:
                    existing.status = version.status
                    existing.superseded_reason = version.superseded_reason
                    existing.test_set = version.test_set or None
            await session.commit()

        await self._with_session(_impl)

    async def _persist_decision(self, state: AutonomousRunState, decision: AutonomousDecision) -> None:
        from app.db.models import AutonomousDecisionModel

        async def _impl(session: AsyncSession) -> None:
            session.add(AutonomousDecisionModel(
                goal_id=state.goal_id,
                iteration=decision.iteration,
                action=decision.action.value,
                reason_code=decision.reason_code,
                rationale=decision.rationale,
                evidence_refs=[e.model_dump() for e in decision.evidence_refs] or None,
            ))
            await session.commit()

        await self._with_session(_impl)

    async def _persist_checkpoint(
        self,
        state: AutonomousRunState,
        checkpoint: AutonomousCheckpoint,
        expected_version: int,
    ) -> bool:
        """Persist a checkpoint with optimistic concurrency (version check)."""
        from app.db.models import ExecutionCheckpointModel

        async def _impl(session: AsyncSession) -> bool:
            # Compare-and-swap on the goal version. Also sync the state column
            # so a restart rehydrates the goal at its true terminal state
            # (only _persist_goal wrote it before, at creation time).
            from sqlalchemy import text
            result = await session.execute(
                text(
                    "UPDATE execution_goals SET version = :new_version, "
                    "state = :state "
                    "WHERE goal_id = :goal_id AND version = :expected_version"
                ),
                {
                    "new_version": expected_version + 1,
                    "state": state.state.value,
                    "goal_id": state.goal_id,
                    "expected_version": expected_version,
                },
            )
            if result.rowcount == 0:
                await session.rollback()
                return False
            session.add(ExecutionCheckpointModel(
                goal_id=state.goal_id,
                iteration=checkpoint.iteration,
                state=checkpoint.state.value,
                action=checkpoint.action.value,
                reason_code=checkpoint.reason_code,
                plan_version=checkpoint.plan_version,
                budget_usage=checkpoint.budget_usage,
                progress_json=checkpoint.progress.model_dump(),
                evidence_json=(
                    state.evidence_history[-1].model_dump()
                    if state.evidence_history else None
                ),
                version=checkpoint.version,
            ))
            await session.commit()
            return True

        persisted = await self._with_session(_impl, fallback=True)
        return bool(persisted)

    async def _persist_escalation(self, state: AutonomousRunState, escalation: HumanEscalation) -> None:
        """Insert or update a human escalation row (resolution persists)."""
        from app.db.models import HumanEscalationModel

        async def _impl(session: AsyncSession) -> None:
            existing = (
                await session.execute(
                    select(HumanEscalationModel).where(
                        HumanEscalationModel.escalation_id == escalation.escalation_id
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                session.add(HumanEscalationModel(
                    escalation_id=escalation.escalation_id,
                    goal_id=state.goal_id,
                    reason=escalation.reason.value,
                    what_happened=escalation.what_happened,
                    attempted=escalation.attempted,
                    needed_input=escalation.needed_input,
                    status=escalation.status.value,
                    resolution=escalation.resolution,
                    resolved_at=(
                        datetime.fromisoformat(escalation.resolved_at)
                        if escalation.resolved_at else None
                    ),
                ))
            else:
                existing.status = escalation.status.value
                existing.resolution = escalation.resolution
                existing.resolved_at = (
                    datetime.fromisoformat(escalation.resolved_at)
                    if escalation.resolved_at else None
                )
            await session.commit()

        await self._with_session(_impl)

    async def _load_goal(self, goal_id: str) -> Optional[AutonomousRunState]:
        """Rehydrate a goal from persistence (idempotent recovery)."""
        from app.db.models import (
            AutonomousDecisionModel,
            ExecutionCheckpointModel,
            ExecutionGoalModel,
            HumanEscalationModel,
        )

        async def _impl(session: AsyncSession) -> Optional[AutonomousRunState]:
            from app.db.models import PlanVersionModel

            # Query by the unique goal_id column (PK is an integer id).
            model = (
                await session.execute(
                    select(ExecutionGoalModel).where(
                        ExecutionGoalModel.goal_id == goal_id
                    )
                )
            ).scalar_one_or_none()
            if model is None:
                return None
            goal = ExecutionGoal(**model.goal_json)
            budget = ExecutionBudget(**model.budget_json)
            policy = AutonomyPolicy(**model.policy_json)
            scope = TaskScope(**model.scope_json)

            plan_versions: List[PlanVersion] = []
            p_stmt = select(PlanVersionModel).where(
                PlanVersionModel.goal_id == goal_id
            ).order_by(PlanVersionModel.version.asc())
            for p in (await session.execute(p_stmt)).scalars().all():
                plan_versions.append(PlanVersion(
                    version=p.version,
                    plan_summary=p.plan_summary or "",
                    plan_objective=p.plan_objective or "",
                    step_count=p.step_count,
                    status=p.status,
                    superseded_reason=p.superseded_reason,
                    completed_steps=p.completed_steps or [],
                    remaining_criteria=p.remaining_criteria or [],
                    test_set=p.test_set or [],
                ))

            decisions: List[AutonomousDecision] = []
            d_stmt = select(AutonomousDecisionModel).where(
                AutonomousDecisionModel.goal_id == goal_id
            ).order_by(AutonomousDecisionModel.iteration.asc())
            for d in (await session.execute(d_stmt)).scalars().all():
                decisions.append(AutonomousDecision(
                    goal_id=d.goal_id,
                    iteration=d.iteration,
                    action=AutonomousAction(d.action),
                    reason_code=d.reason_code or "",
                    rationale=d.rationale or "",
                    evidence_refs=[EvidenceRef(**e) for e in (d.evidence_refs or [])],
                ))

            checkpoints: List[AutonomousCheckpoint] = []
            evidence_history: List[IterationEvidence] = []
            c_stmt = select(ExecutionCheckpointModel).where(
                ExecutionCheckpointModel.goal_id == goal_id
            ).order_by(ExecutionCheckpointModel.iteration.asc())
            for c in (await session.execute(c_stmt)).scalars().all():
                checkpoints.append(AutonomousCheckpoint(
                    goal_id=c.goal_id,
                    iteration=c.iteration,
                    state=ExecutionState(c.state),
                    action=AutonomousAction(c.action),
                    reason_code=c.reason_code or "",
                    plan_version=c.plan_version,
                    budget_usage=c.budget_usage or {},
                    progress=GoalProgress(**(c.progress_json or {})),
                    version=c.version,
                ))
                if c.evidence_json:
                    evidence_history.append(IterationEvidence(**c.evidence_json))

            escalations: List[HumanEscalation] = []
            e_stmt = select(HumanEscalationModel).where(
                HumanEscalationModel.goal_id == goal_id
            )
            for e in (await session.execute(e_stmt)).scalars().all():
                escalations.append(HumanEscalation(
                    escalation_id=e.escalation_id,
                    goal_id=e.goal_id,
                    reason=EscalationReason(e.reason),
                    what_happened=e.what_happened or "",
                    attempted=e.attempted or "",
                    needed_input=e.needed_input or "",
                    status=EscalationStatus(e.status),
                ))

            state = AutonomousRunState(
                goal_id=model.goal_id,
                task=model.task,
                repository=model.repository,
                state=ExecutionState(model.state),
                goal=goal,
                budget=budget,
                policy=policy,
                scope=scope,
                plan_versions=plan_versions,
                decisions=decisions,
                checkpoints=checkpoints,
                escalations=escalations,
                evidence_history=evidence_history[-50:],
                version=model.version,
            )
            return state

        return await self._with_session(_impl, fallback=None)


def _criterion_to_req_type(criterion_type: CriterionType) -> RequirementType:
    mapping = {
        CriterionType.SECURITY: RequirementType.SECURITY,
        CriterionType.PERFORMANCE: RequirementType.PERFORMANCE,
        CriterionType.TEST: RequirementType.TEST,
    }
    return mapping.get(criterion_type, RequirementType.FUNCTIONAL)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    from app.models.base import new_id
    return new_id()
