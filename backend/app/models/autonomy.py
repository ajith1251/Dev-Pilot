"""
Phase 16 — Autonomous Execution models.

A higher-level controller sits ABOVE the Phase 10/15 orchestrator:

    Autonomous Controller = decides WHAT happens next
    Orchestrator          = executes engineering stages
    Agents                = perform specialized work
    Quality Gate          = determines final engineering approval

These models define the durable, bounded autonomous state:

- ExecutionGoal         — the user goal + acceptance criteria
- AcceptanceCriterion   — a single measurable criterion
- GoalProgress          — criteria progress across iterations
- ExecutionBudget       — bounded autonomy limits + usage
- PlanVersion           — immutable plan history (never overwritten)
- TaskScope             — allowed / forbidden change areas
- HumanEscalation       — structured request for human input
- AutonomousDecision    — a recorded controller decision (no CoT)
- AutonomyPolicy        — what autonomy is allowed to do
- AutonomousCheckpoint  — durable iteration checkpoint
- AutonomousRunState    — aggregate state for one autonomous run

All text fields are treated as untrusted content and are never injected
as system instructions. Deterministic evidence is authoritative.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator

from app.models.base import new_id
from app.models.collaboration import EvidenceRef
from app.models.orchestration import RepositoryPatchResult


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Bounds (Safety & Performance) ────────────────────────────────
# Autonomous execution must remain bounded in every dimension.

MAX_CRITERIA_PER_GOAL = 20
MAX_PLAN_VERSIONS = 10
MAX_DECISIONS_PER_GOAL = 100
MAX_CHECKPOINTS_PER_GOAL = 50
MAX_ESCALATIONS_PER_GOAL = 10
SUMMARY_MAX_LEN = 500
CLAIM_MAX_LEN = 300

DEFAULT_MAX_ITERATIONS = 5
DEFAULT_MAX_REPLANS = 2
DEFAULT_MAX_REPAIRS = 3
DEFAULT_MAX_AGENT_CALLS = 50
DEFAULT_MAX_LLM_CALLS = 100
DEFAULT_MAX_FILES_CHANGED = 25
DEFAULT_MAX_TEST_RUNS = 10
DEFAULT_MAX_EXECUTION_TIME_SECONDS = 3600


# ── Enums ───────────────────────────────────────────────────────


class CriterionStatus(str, Enum):
    """Status of a single acceptance criterion."""

    PENDING = "pending"
    SATISFIED = "satisfied"
    UNSATISFIED = "unsatisfied"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class CriterionType(str, Enum):
    """Category of an acceptance criterion."""

    FUNCTIONAL = "functional"
    TEST = "test"
    SECURITY = "security"
    PERFORMANCE = "performance"
    REGRESSION = "regression"
    QUALITY = "quality"


class ExecutionState(str, Enum):
    """Autonomous run lifecycle state."""

    RUNNING = "running"
    PAUSED = "paused"
    WAITING_FOR_HUMAN = "waiting_for_human"
    RESUMING = "resuming"
    COMPLETED = "completed"
    STOPPED = "stopped"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AutonomousAction(str, Enum):
    """Explicit controller actions (§6)."""

    CONTINUE = "continue"
    REPLAN = "replan"
    RETRY = "retry"
    REPAIR = "repair"
    REVIEW = "review"
    COMPLETE = "complete"
    ESCALATE = "escalate"
    STOP = "stop"


class ProgressTrend(str, Enum):
    """Stuck-detection trend classification (§11)."""

    PROGRESSING = "progressing"
    STALLED = "stalled"
    LOOPING = "looping"
    BLOCKED = "blocked"


class EscalationReason(str, Enum):
    """Structured reasons for human escalation (§16)."""

    AMBIGUOUS_REQUIREMENT = "ambiguous_requirement"
    MISSING_INFORMATION = "missing_information"
    BUDGET_EXHAUSTED = "budget_exhausted"
    STUCK = "stuck"
    SECURITY_RISK = "security_risk"
    SCOPE_EXPANSION = "scope_expansion"
    ENVIRONMENT_FAILURE = "environment_failure"
    UNRESOLVED_CONFLICT = "unresolved_conflict"
    QUALITY_GATE_REJECTION = "quality_gate_rejection"


class FailureClass(str, Enum):
    """Environment vs code failure classification (§20)."""

    CODE = "code"
    TEST = "test"
    ENVIRONMENT = "environment"
    CONFIGURATION = "configuration"
    DEPENDENCY = "dependency"
    UNKNOWN = "unknown"


class EscalationStatus(str, Enum):
    OPEN = "open"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


# ── Acceptance Criteria ─────────────────────────────────────────


class AcceptanceCriterion(BaseModel):
    """A single measurable acceptance criterion."""

    criterion_id: str = Field(default_factory=lambda: f"CR-{new_id().upper()[:8]}")
    description: str = Field(description="What must be true", max_length=CLAIM_MAX_LEN)
    criterion_type: CriterionType = Field(default=CriterionType.FUNCTIONAL)
    status: CriterionStatus = Field(default=CriterionStatus.PENDING)
    evidence: List[EvidenceRef] = Field(
        default_factory=list,
        description="Deterministic evidence backing the status",
        max_length=20,
    )
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    verification: Optional[str] = Field(
        default=None,
        description=(
            "Deterministic verification hint: 'test:<name>', 'suite:pass', "
            "'gate:approved', 'file:<path>', 'review:no_blocking'"
        ),
    )
    updated_at: str = Field(default_factory=_utcnow_iso)


# ── Goal & Progress ─────────────────────────────────────────────


class GoalProgress(BaseModel):
    """Criteria progress snapshot for the autonomous loop."""

    criteria_total: int = Field(default=0)
    criteria_satisfied: int = Field(default=0)
    criteria_unsatisfied: int = Field(default=0)
    criteria_unknown: int = Field(default=0)
    criteria_blocked: int = Field(default=0)
    iteration: int = Field(default=0)
    trend: ProgressTrend = Field(default=ProgressTrend.PROGRESSING)
    improved_last_iteration: bool = Field(default=False)
    previous_satisfied: int = Field(default=0)


class ExecutionGoal(BaseModel):
    """The user goal being pursued by the autonomous controller."""

    goal_id: str = Field(default_factory=lambda: f"GOAL-{new_id().upper()[:8]}")
    task: str = Field(description="The user goal / task", max_length=SUMMARY_MAX_LEN)
    repository: Optional[str] = Field(default=None, description="Target repository")
    acceptance_criteria: List[AcceptanceCriterion] = Field(
        default_factory=list, max_length=MAX_CRITERIA_PER_GOAL
    )
    constraints: List[str] = Field(default_factory=list, max_length=20)
    status: ExecutionState = Field(default=ExecutionState.RUNNING)
    progress: GoalProgress = Field(default_factory=GoalProgress)
    attempt: int = Field(default=0, description="Iterations executed")
    replan_count: int = Field(default=0)
    created_at: str = Field(default_factory=_utcnow_iso)
    updated_at: str = Field(default_factory=_utcnow_iso)

    def criteria_summary(self) -> Dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "task": self.task[:200],
            "status": self.status.value,
            "attempt": self.attempt,
            "replan_count": self.replan_count,
            "progress": self.progress.model_dump(),
            "criteria": [
                {
                    "criterion_id": c.criterion_id,
                    "description": c.description[:200],
                    "type": c.criterion_type.value,
                    "status": c.status.value,
                    "confidence": round(c.confidence, 2),
                }
                for c in self.acceptance_criteria
            ],
        }


# ── Budget ──────────────────────────────────────────────────────


class ExecutionBudget(BaseModel):
    """Bounded execution budget + usage counters (§8)."""

    max_iterations: int = Field(default=DEFAULT_MAX_ITERATIONS, ge=1, le=100)
    max_replans: int = Field(default=DEFAULT_MAX_REPLANS, ge=0, le=20)
    max_repairs: int = Field(default=DEFAULT_MAX_REPAIRS, ge=0, le=20)
    max_agent_calls: int = Field(default=DEFAULT_MAX_AGENT_CALLS, ge=1, le=1000)
    max_llm_calls: int = Field(default=DEFAULT_MAX_LLM_CALLS, ge=1, le=10000)
    max_files_changed: int = Field(default=DEFAULT_MAX_FILES_CHANGED, ge=1, le=500)
    max_test_runs: int = Field(default=DEFAULT_MAX_TEST_RUNS, ge=0, le=100)
    max_execution_time_seconds: int = Field(
        default=DEFAULT_MAX_EXECUTION_TIME_SECONDS, ge=1, le=86400 * 7
    )

    iterations_used: int = Field(default=0)
    replans_used: int = Field(default=0)
    repairs_used: int = Field(default=0)
    agent_calls_used: int = Field(default=0)
    llm_calls_used: int = Field(default=0)
    files_changed_used: int = Field(default=0)
    test_runs_used: int = Field(default=0)
    execution_time_used_seconds: float = Field(default=0.0)

    def usage(self) -> Dict[str, Any]:
        return {
            "iterations": self.iterations_used,
            "replans": self.replans_used,
            "repairs": self.repairs_used,
            "agent_calls": self.agent_calls_used,
            "llm_calls": self.llm_calls_used,
            "files_changed": self.files_changed_used,
            "test_runs": self.test_runs_used,
            "execution_time_seconds": round(self.execution_time_used_seconds, 1),
        }

    def limits(self) -> Dict[str, Any]:
        return {
            "max_iterations": self.max_iterations,
            "max_replans": self.max_replans,
            "max_repairs": self.max_repairs,
            "max_agent_calls": self.max_agent_calls,
            "max_llm_calls": self.max_llm_calls,
            "max_files_changed": self.max_files_changed,
            "max_test_runs": self.max_test_runs,
            "max_execution_time_seconds": self.max_execution_time_seconds,
        }

    def exhausted(self) -> Optional[str]:
        """Return the first exhausted limit name, or None.

        A limit of 0 means the action is *disabled* (routed by the controller's
        decision logic), NOT instantly exhausted — so zero limits are skipped.
        """
        if self.max_iterations and self.iterations_used >= self.max_iterations:
            return "max_iterations"
        if self.max_replans and self.replans_used >= self.max_replans:
            return "max_replans"
        if self.max_repairs and self.repairs_used >= self.max_repairs:
            return "max_repairs"
        if self.max_agent_calls and self.agent_calls_used >= self.max_agent_calls:
            return "max_agent_calls"
        if self.max_llm_calls and self.llm_calls_used >= self.max_llm_calls:
            return "max_llm_calls"
        if self.max_files_changed and self.files_changed_used >= self.max_files_changed:
            return "max_files_changed"
        if self.max_test_runs and self.test_runs_used >= self.max_test_runs:
            return "max_test_runs"
        if (self.max_execution_time_seconds
                and self.execution_time_used_seconds >= self.max_execution_time_seconds):
            return "max_execution_time_seconds"
        return None


# ── Plan Versioning ─────────────────────────────────────────────


class PlanVersion(BaseModel):
    """An immutable plan version. Previous versions are never overwritten."""

    version: int = Field(description="Monotonic version number")
    plan_summary: str = Field(default="", max_length=SUMMARY_MAX_LEN)
    plan_objective: str = Field(default="", max_length=SUMMARY_MAX_LEN)
    step_count: int = Field(default=0)
    status: str = Field(default="active", description="active | superseded")
    superseded_reason: Optional[str] = Field(default=None, max_length=CLAIM_MAX_LEN)
    completed_steps: List[str] = Field(default_factory=list, max_length=20)
    remaining_criteria: List[str] = Field(default_factory=list, max_length=20)
    triggering_evidence: List[EvidenceRef] = Field(default_factory=list, max_length=10)
    test_set: List[str] = Field(
        default_factory=list,
        max_length=50,
        description="Impact-analysis-selected test files targeting this plan version",
    )
    created_at: str = Field(default_factory=_utcnow_iso)

    def summary(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "plan_summary": self.plan_summary[:200],
            "plan_objective": self.plan_objective[:200],
            "step_count": self.step_count,
            "status": self.status,
            "superseded_reason": self.superseded_reason,
            "completed_steps": self.completed_steps[:10],
            "remaining_criteria": self.remaining_criteria[:10],
            "test_set": self.test_set[:10],
        }


# ── Scope Control ───────────────────────────────────────────────


class TaskScope(BaseModel):
    """Explicit scope bounds (§15)."""

    allowed_modules: List[str] = Field(default_factory=list, max_length=50)
    expected_change_area: List[str] = Field(default_factory=list, max_length=50)
    forbidden_areas: List[str] = Field(default_factory=list, max_length=20)
    scope_expansion_requests: int = Field(default=0)
    max_scope_expansions: int = Field(default=2, ge=0, le=10)
    violations: List[str] = Field(default_factory=list, max_length=20)

    def summary(self) -> Dict[str, Any]:
        return {
            "allowed_modules": self.allowed_modules[:20],
            "expected_change_area": self.expected_change_area[:20],
            "forbidden_areas": self.forbidden_areas[:10],
            "scope_expansion_requests": self.scope_expansion_requests,
            "max_scope_expansions": self.max_scope_expansions,
            "violations": self.violations[:10],
        }


# ── Human Escalation ────────────────────────────────────────────


class HumanEscalation(BaseModel):
    """Structured request for human input (§16)."""

    escalation_id: str = Field(default_factory=lambda: f"ESC-{new_id().upper()[:8]}")
    goal_id: str = Field(description="Owning goal")
    reason: EscalationReason = Field(description="Why we need a human")
    what_happened: str = Field(default="", max_length=CLAIM_MAX_LEN)
    attempted: str = Field(default="", max_length=CLAIM_MAX_LEN)
    current_evidence: List[EvidenceRef] = Field(default_factory=list, max_length=10)
    remaining_criteria: List[str] = Field(default_factory=list, max_length=20)
    needed_input: str = Field(default="", max_length=CLAIM_MAX_LEN)
    status: EscalationStatus = Field(default=EscalationStatus.OPEN)
    resolution: Optional[str] = Field(default=None, max_length=CLAIM_MAX_LEN)
    created_at: str = Field(default_factory=_utcnow_iso)
    resolved_at: Optional[str] = Field(default=None)

    def summary(self) -> Dict[str, Any]:
        return {
            "escalation_id": self.escalation_id,
            "reason": self.reason.value,
            "what_happened": self.what_happened[:200],
            "attempted": self.attempted[:200],
            "needed_input": self.needed_input[:200],
            "status": self.status.value,
            "remaining_criteria": self.remaining_criteria[:10],
        }


# ── Decisions ───────────────────────────────────────────────────


class AutonomousDecision(BaseModel):
    """A recorded controller decision. Never persists chain-of-thought."""

    decision_id: str = Field(default_factory=lambda: f"AD-{new_id().upper()[:8]}")
    goal_id: str = Field(description="Owning goal")
    iteration: int = Field(default=0)
    action: AutonomousAction = Field(description="Chosen next action")
    reason_code: str = Field(default="", max_length=100)
    rationale: str = Field(default="", max_length=CLAIM_MAX_LEN)
    evidence_refs: List[EvidenceRef] = Field(default_factory=list, max_length=10)
    timestamp: str = Field(default_factory=_utcnow_iso)

    def summary(self) -> Dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "iteration": self.iteration,
            "action": self.action.value,
            "reason_code": self.reason_code,
            "rationale": self.rationale[:200],
            "timestamp": self.timestamp,
        }


# ── Policy ──────────────────────────────────────────────────────


class AutonomyPolicy(BaseModel):
    """What autonomous execution is allowed to do (§28)."""

    allow_repair: bool = Field(default=True)
    allow_replan: bool = Field(default=True)
    allow_test_execution: bool = Field(default=True)
    allow_scope_expansion: bool = Field(default=False)
    allow_human_escalation: bool = Field(default=True)
    max_scope_expansions: int = Field(default=2, ge=0, le=10)


# ── Checkpoints ─────────────────────────────────────────────────


class AutonomousCheckpoint(BaseModel):
    """Durable per-iteration checkpoint (§24)."""

    checkpoint_id: str = Field(default_factory=lambda: f"CK-{new_id().upper()[:8]}")
    goal_id: str = Field(description="Owning goal")
    iteration: int = Field(default=0)
    state: ExecutionState = Field(default=ExecutionState.RUNNING)
    action: AutonomousAction = Field(default=AutonomousAction.CONTINUE)
    reason_code: str = Field(default="", max_length=100)
    plan_version: int = Field(default=1)
    budget_usage: Dict[str, Any] = Field(default_factory=dict)
    progress: GoalProgress = Field(default_factory=GoalProgress)
    version: int = Field(default=1, description="Optimistic-concurrency version")
    persisted_at: str = Field(default_factory=_utcnow_iso)

    def summary(self) -> Dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "iteration": self.iteration,
            "state": self.state.value,
            "action": self.action.value,
            "reason_code": self.reason_code,
            "plan_version": self.plan_version,
            "budget_usage": self.budget_usage,
            "progress": self.progress.model_dump(),
            "version": self.version,
            "persisted_at": self.persisted_at,
        }


# ── Aggregate State ─────────────────────────────────────────────


class AutonomousRunState(BaseModel):
    """Aggregate state for one autonomous run."""

    goal_id: str = Field(default_factory=lambda: f"GOAL-{new_id().upper()[:8]}")
    task: str = Field(max_length=SUMMARY_MAX_LEN)
    repository: Optional[str] = Field(default=None)
    state: ExecutionState = Field(default=ExecutionState.RUNNING)

    goal: ExecutionGoal = Field(description="The goal model")
    budget: ExecutionBudget = Field(default_factory=ExecutionBudget)
    policy: AutonomyPolicy = Field(default_factory=AutonomyPolicy)
    scope: TaskScope = Field(default_factory=TaskScope)

    plan_versions: List[PlanVersion] = Field(default_factory=list, max_length=MAX_PLAN_VERSIONS)
    decisions: List[AutonomousDecision] = Field(
        default_factory=list, max_length=MAX_DECISIONS_PER_GOAL
    )
    escalations: List[HumanEscalation] = Field(
        default_factory=list, max_length=MAX_ESCALATIONS_PER_GOAL
    )
    checkpoints: List[AutonomousCheckpoint] = Field(
        default_factory=list, max_length=MAX_CHECKPOINTS_PER_GOAL
    )

    # Bounded iteration evidence history (used by stuck detection).
    evidence_history: List[IterationEvidence] = Field(
        default_factory=list, max_length=MAX_CHECKPOINTS_PER_GOAL
    )
    stalled_iterations: int = Field(
        default=0,
        description="Consecutive iterations with no measurable progress (§11)",
    )

    events: List[Dict[str, Any]] = Field(default_factory=list, max_length=200)

    # Phase 17: evidence-only consensus topics observed on the latest run.
    # Used to enrich REPLAN rationale with shared reasoning (never CoT).
    consensus_topics: List[str] = Field(default_factory=list, max_length=20)

    version: int = Field(default=1, description="Optimistic-concurrency version")
    created_at: str = Field(default_factory=_utcnow_iso)
    updated_at: str = Field(default_factory=_utcnow_iso)

    @model_validator(mode="before")
    @classmethod
    def _ensure_goal(cls, data: Any):
        """Auto-build the goal model when constructed with just a task."""
        if isinstance(data, dict):
            task = data.get("task", "")
            goal_id = data.get("goal_id") or f"GOAL-{new_id().upper()[:8]}"
            if not data.get("goal"):
                data = {
                    **data,
                    "goal_id": goal_id,
                    "goal": {
                        "goal_id": goal_id,
                        "task": task,
                        "repository": data.get("repository"),
                        "status": data.get("state", "running"),
                    },
                }
        return data

    def add_event(self, event_type: str, message: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Record an autonomy event (bounded, sanitized)."""
        self.events.append({
            "event_type": event_type,
            "message": message[:500],
            "metadata": metadata or {},
            "timestamp": _utcnow_iso(),
        })
        self.events = self.events[-200:]

    def status_summary(self) -> Dict[str, Any]:
        """Compact public status — no hidden reasoning (§30/§34)."""
        return {
            "goal_id": self.goal_id,
            "task": self.task[:200],
            "repository": self.repository,
            "state": self.state.value,
            "goal": self.goal.criteria_summary(),
            "budget": {
                "limits": self.budget.limits(),
                "usage": self.budget.usage(),
            },
            "plan_versions": [p.summary() for p in self.plan_versions],
            "latest_decision": self.decisions[-1].summary() if self.decisions else None,
            "escalations": [e.summary() for e in self.escalations],
            "latest_checkpoint": self.checkpoints[-1].summary() if self.checkpoints else None,
            "scope": self.scope.summary(),
            "events": self.events[-20:],
            "version": self.version,
        }


# ── Iteration Evidence ──────────────────────────────────────────


class IterationEvidence(BaseModel):
    """Deterministic evidence collected from one iteration."""

    iteration: int = Field(default=0)
    run_id: str = Field(default="")
    test_status: Optional[str] = Field(default=None)
    tests_total: int = Field(default=0)
    tests_passed: int = Field(default=0)
    tests_failed: int = Field(default=0)
    failing_test_names: List[str] = Field(default_factory=list, max_length=50)
    quality_gate_decision: Optional[str] = Field(default=None)
    review_findings: int = Field(default=0)
    changed_files: List[str] = Field(default_factory=list, max_length=100)
    changed_symbols: List[str] = Field(default_factory=list, max_length=100)
    repair_attempts: int = Field(default=0)
    failure_code: Optional[str] = Field(default=None)
    failure_class: FailureClass = Field(default=FailureClass.UNKNOWN)
    failure_message: str = Field(default="", max_length=500)
    duration_seconds: float = Field(default=0.0)
    plan_summary: str = Field(default="", max_length=SUMMARY_MAX_LEN)
    plan_objective: str = Field(default="", max_length=SUMMARY_MAX_LEN)
    plan_step_count: int = Field(default=0)

    # Phase 20A4 — per-repository patch validation outcomes aggregated from
    # the orchestrator's validation stage. Each entry is one
    # RepositoryPatchResult. The ScopeController inspects these to detect
    # cross-repository scope violations without ever cross-checking two
    # repositories against the same checkout.
    repository_validation: List[RepositoryPatchResult] = Field(
        default_factory=list, max_length=20,
        description="Per-repository validation outcomes (Phase 20A4)",
    )

    def error_fingerprint(self) -> str:
        """Stable fingerprint of the dominant failure (for stuck detection)."""
        if not self.failure_message:
            return ""
        return self.failure_message[:120].strip().lower()

    def failing_test_fingerprint(self) -> str:
        return "|".join(sorted(self.failing_test_names)[:20])


# ── Dry-Run Report ──────────────────────────────────────────────


class DryRunReport(BaseModel):
    """Estimate produced by autonomous dry-run (§29). No mutations."""

    task: str = Field(default="")
    repository: Optional[str] = Field(default=None)
    extracted_criteria: List[Dict[str, Any]] = Field(default_factory=list)
    estimated_scope: Dict[str, Any] = Field(default_factory=dict)
    estimated_budget: Dict[str, Any] = Field(default_factory=dict)
    likely_workflow: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    feasibility: str = Field(default="ok", description="ok | review | blocked")

    def summary(self) -> Dict[str, Any]:
        return {
            "task": self.task[:200],
            "repository": self.repository,
            "criteria_count": len(self.extracted_criteria),
            "estimated_scope": self.estimated_scope,
            "estimated_budget": self.estimated_budget,
            "likely_workflow": self.likely_workflow,
            "warnings": self.warnings[:10],
            "feasibility": self.feasibility,
        }


# Resolve the forward reference to IterationEvidence used by AutonomousRunState.
AutonomousRunState.model_rebuild()
