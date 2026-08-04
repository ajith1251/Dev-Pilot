"""
Phase 10 — End-to-End Multi-Agent Orchestration models.

Defines the core run abstraction, state machine, stage model,
event system, and result envelope for the autonomous pipeline.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.models.coding import PatchApplicationResult, PatchSet
from app.models.issues import ImplementationPlan, StructuredRequirements
from app.models.profile import RepositoryProfile
from app.models.rag import RetrievedContext
from app.models.repair import RepairResult
from app.models.review import QualityGateResult, ReviewReport
from app.models.testing import TestRunResult


# ── Enums ───────────────────────────────────────────────────────


class RunSourceType(str, Enum):
    """Origin of a run."""

    USER_TASK = "user_task"
    GITHUB_ISSUE = "github_issue"


class RunStatus(str, Enum):
    """High-level status of a DevPilot run.

    Terminal states: APPROVED, REJECTED, NEEDS_HUMAN_REVIEW, FAILED, CANCELLED
    """

    PENDING = "pending"
    RUNNING = "running"
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_HUMAN_REVIEW = "needs_human_review"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StageType(str, Enum):
    """All stages in the DevPilot orchestration pipeline."""

    INITIALIZING = "initializing"

    ACQUIRING_REPOSITORY = "acquiring_repository"
    ANALYZING_REPOSITORY = "analyzing_repository"
    ANALYZING_TASK = "analyzing_task"
    PLANNING = "planning"
    RETRIEVING_CONTEXT = "retrieving_context"
    CODING = "coding"
    VALIDATING_PATCH = "validating_patch"
    APPLYING_PATCH = "applying_patch"
    TESTING = "testing"
    REPAIRING = "repairing"
    REVIEWING = "reviewing"
    QUALITY_GATE = "quality_gate"

    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StageStatus(str, Enum):
    """Status of an individual pipeline stage."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class EventType(str, Enum):
    """Types of run events for observability."""

    RUN_CREATED = "run_created"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    RUN_CANCELLED = "run_cancelled"

    STAGE_STARTED = "stage_started"
    STAGE_COMPLETED = "stage_completed"
    STAGE_FAILED = "stage_failed"
    STAGE_SKIPPED = "stage_skipped"

    PATCH_GENERATED = "patch_generated"
    PATCH_VALIDATED = "patch_validated"
    PATCH_APPLIED = "patch_applied"
    PATCH_REJECTED = "patch_rejected"
    CODING_RETRY = "coding_retry"
    TASK_ANALYSIS_RETRY = "task_analysis_retry"

    TESTS_COMPLETED = "tests_completed"
    REPAIR_STARTED = "repair_started"
    REPAIR_COMPLETED = "repair_completed"

    REVIEW_COMPLETED = "review_completed"
    QUALITY_GATE_COMPLETED = "quality_gate_completed"

    # Phase 15 collaboration events
    HANDOFF_CREATED = "handoff_created"
    DECISION_RECORDED = "decision_recorded"
    CONFLICT_DETECTED = "conflict_detected"
    MEMORY_PROMOTED = "memory_promoted"

    # Phase 17 collaborative reasoning events
    CONSENSUS_BUILT = "consensus_built"
    NOTEBOOK_UPDATED = "notebook_updated"

    # Phase 16 autonomous execution events
    GOAL_CREATED = "goal_created"
    CRITERION_UPDATED = "criterion_updated"
    ITERATION_STARTED = "iteration_started"
    PROGRESS_EVALUATED = "progress_evaluated"
    REPLAN_REQUESTED = "replan_requested"
    PLAN_SUPERSEDED = "plan_superseded"
    STUCK_DETECTED = "stuck_detected"
    BUDGET_WARNING = "budget_warning"
    BUDGET_EXHAUSTED = "budget_exhausted"
    ESCALATION_CREATED = "escalation_created"
    GOAL_COMPLETED = "goal_completed"
    RUN_RETRY = "run_retry"

    CANCELLATION_REQUESTED = "cancellation_requested"


class FailureCode(str, Enum):
    """Machine-readable failure codes."""

    REPOSITORY_ACQUISITION_FAILED = "repository_acquisition_failed"
    REPOSITORY_ANALYSIS_FAILED = "repository_analysis_failed"
    TASK_ANALYSIS_FAILED = "task_analysis_failed"
    PLANNING_FAILED = "planning_failed"
    RETRIEVAL_FAILED = "retrieval_failed"
    CODING_FAILED = "coding_failed"
    PATCH_VALIDATION_FAILED = "patch_validation_failed"
    PATCH_APPLICATION_FAILED = "patch_application_failed"
    TEST_EXECUTION_FAILED = "test_execution_failed"
    REPAIR_FAILED = "repair_failed"
    REVIEW_FAILED = "review_failed"
    QUALITY_GATE_FAILED = "quality_gate_failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


# ── Transition Map ──────────────────────────────────────────────

STAGE_TRANSITIONS: Dict[StageType, List[StageType]] = {
    StageType.INITIALIZING: [StageType.ACQUIRING_REPOSITORY],

    StageType.ACQUIRING_REPOSITORY: [
        StageType.ANALYZING_REPOSITORY,
        StageType.FAILED,
        StageType.CANCELLED,
    ],
    StageType.ANALYZING_REPOSITORY: [
        StageType.ANALYZING_TASK,
        StageType.FAILED,
        StageType.CANCELLED,
    ],
    StageType.ANALYZING_TASK: [
        StageType.PLANNING,
        StageType.FAILED,
        StageType.CANCELLED,
    ],
    StageType.PLANNING: [
        StageType.RETRIEVING_CONTEXT,
        StageType.FAILED,
        StageType.CANCELLED,
    ],
    StageType.RETRIEVING_CONTEXT: [
        StageType.CODING,
        StageType.FAILED,
        StageType.CANCELLED,
    ],
    StageType.CODING: [
        StageType.VALIDATING_PATCH,
        StageType.FAILED,
        StageType.CANCELLED,
    ],
    StageType.VALIDATING_PATCH: [
        StageType.APPLYING_PATCH,
        StageType.FAILED,
        StageType.CANCELLED,
    ],
    StageType.APPLYING_PATCH: [
        StageType.TESTING,
        StageType.FAILED,
        StageType.CANCELLED,
    ],
    StageType.TESTING: [
        StageType.REPAIRING,
        StageType.REVIEWING,
        StageType.FAILED,
        StageType.CANCELLED,
    ],
    StageType.REPAIRING: [
        StageType.TESTING,  # Re-test after repair
        StageType.REVIEWING,  # Even if max attempts, review can run
        StageType.FAILED,
        StageType.CANCELLED,
    ],
    StageType.REVIEWING: [
        StageType.QUALITY_GATE,
        StageType.FAILED,
        StageType.CANCELLED,
    ],
    StageType.QUALITY_GATE: [
        StageType.COMPLETED,
        StageType.FAILED,
        StageType.CANCELLED,
    ],
}

TERMINAL_STAGES: set[StageType] = {
    StageType.COMPLETED,
    StageType.FAILED,
    StageType.CANCELLED,
}


# ── Domain Models ───────────────────────────────────────────────


class RunSource(BaseModel):
    """Source information for a DevPilot run."""

    source_type: RunSourceType = Field(description="Origin of the run")
    title: str = Field(description="Task / issue title")
    description: str = Field(default="", description="Full description")
    repository_path: Optional[str] = Field(default=None, description="Local path or remote URL")
    issue_number: Optional[int] = Field(default=None, description="GitHub issue number")
    issue_url: Optional[str] = Field(default=None, description="GitHub issue URL")


class StageResult(BaseModel):
    """Result of a single pipeline stage."""

    stage: StageType = Field(description="Stage identifier")
    status: StageStatus = Field(description="Outcome of the stage")
    started_at: Optional[str] = Field(default=None)
    finished_at: Optional[str] = Field(default=None)
    duration_ms: Optional[float] = Field(default=None)
    warnings: List[str] = Field(default_factory=list)
    error: Optional[str] = Field(default=None)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RunFailure(BaseModel):
    """Structured failure information for a run."""

    stage: StageType = Field(description="Stage where failure occurred")
    code: FailureCode = Field(description="Machine-readable failure code")
    message: str = Field(description="Human-readable message")
    recoverable: bool = Field(default=False)
    details: Dict[str, Any] = Field(default_factory=dict)


class RunEvent(BaseModel):
    """An event recorded during a run."""

    event_id: str = Field(description="Unique event identifier")
    run_id: str = Field(description="Run this event belongs to")
    timestamp: str = Field(description="ISO 8601 timestamp")
    event_type: EventType = Field(description="Type of event")
    stage: Optional[StageType] = Field(default=None)
    message: str = Field(default="")
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DevPilotRun(BaseModel):
    """A single DevPilot end-to-end orchestration run."""

    run_id: str = Field(description="Unique run identifier, e.g. RUN-abc123")
    source: RunSource = Field(description="Task/issue source")
    status: RunStatus = Field(default=RunStatus.PENDING)
    current_stage: StageType = Field(default=StageType.INITIALIZING)

    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at: Optional[str] = Field(default=None)
    finished_at: Optional[str] = Field(default=None)

    # Context — built up as stages complete
    repository_path: Optional[str] = Field(default=None)
    repository_profile: Optional[RepositoryProfile] = Field(default=None)
    requirements: Optional[StructuredRequirements] = Field(default=None)
    plan: Optional[ImplementationPlan] = Field(default=None)
    retrieved_context: Optional[RetrievedContext] = Field(default=None)
    patch_set: Optional[PatchSet] = Field(default=None)
    patch_result: Optional[PatchApplicationResult] = Field(default=None)
    test_result: Optional[TestRunResult] = Field(default=None)
    repair_result: Optional[RepairResult] = Field(default=None)
    review_report: Optional[ReviewReport] = Field(default=None)
    quality_gate_result: Optional[QualityGateResult] = Field(default=None)

    # Orchestration internals
    stage_results: List[StageResult] = Field(default_factory=list)
    events: List[RunEvent] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    failure: Optional[RunFailure] = Field(default=None)

    # Cancellation
    cancellation_requested: bool = Field(default=False)
    cancelled_at: Optional[str] = Field(default=None)

    # Timing
    total_duration_ms: Optional[float] = Field(default=None)


class DevPilotRunResult(BaseModel):
    """Final structured result of a DevPilot run."""

    run_id: str = Field(description="Run identifier")
    status: RunStatus = Field(description="Final run status")

    source: RunSource = Field(description="Original task/issue")
    repository: Optional[str] = Field(default=None)

    # Stage summary
    stages: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Summarized stage results",
    )
    events: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Sanitized run events",
    )

    # Final outputs
    requirements: Optional[StructuredRequirements] = Field(default=None)
    plan: Optional[ImplementationPlan] = Field(default=None)
    patch_summary: Optional[Dict[str, Any]] = Field(default=None)
    test_result: Optional[TestRunResult] = Field(default=None)
    repair_summary: Optional[Dict[str, Any]] = Field(default=None)
    review_report: Optional[ReviewReport] = Field(default=None)
    quality_gate: Optional[QualityGateResult] = Field(default=None)

    # Outcome
    failure: Optional[RunFailure] = Field(default=None)
    warnings: List[str] = Field(default_factory=list)

    # Timing
    started_at: Optional[str] = Field(default=None)
    finished_at: Optional[str] = Field(default=None)
    duration_seconds: Optional[float] = Field(default=None)


# ── Capabilities ────────────────────────────────────────────────


class OrchestrationCapabilities(BaseModel):
    """Phase 10 orchestration capabilities."""

    supported_sources: List[RunSourceType] = Field(
        default_factory=lambda: [RunSourceType.USER_TASK, RunSourceType.GITHUB_ISSUE]
    )
    stages: List[StageType] = Field(
        default_factory=lambda: list(STAGE_TRANSITIONS.keys())
    )
    cancellation_mode: str = Field(default="cooperative")
    persistence_mode: str = Field(default="in_memory")
    repair_enabled: bool = Field(default=True)
    review_enabled: bool = Field(default=True)
    github_write_enabled: bool = Field(default=False)
    version: str = Field(default="0.1.0")


# ── RunStateMachine ─────────────────────────────────────────────


class TransitionError(ValueError):
    """Raised when an invalid stage transition is attempted."""

    pass


class RunStateMachine:
    """Deterministic state machine for orchestration stage transitions.

    Validates every transition against the STAGE_TRANSITIONS map.
    """

    @classmethod
    def can_transition(cls, current: StageType, target: StageType) -> bool:
        """Check if a transition from current to target is allowed."""
        if current in TERMINAL_STAGES:
            return False
        allowed = STAGE_TRANSITIONS.get(current, [])
        return target in allowed

    @classmethod
    def transition(cls, current: StageType, target: StageType) -> StageType:
        """Attempt a transition, raising TransitionError if invalid.

        Returns the target stage on success.
        """
        if current in TERMINAL_STAGES:
            raise TransitionError(
                f"Cannot transition from terminal stage '{current.value}'"
            )
        if not cls.can_transition(current, target):
            allowed = [s.value for s in STAGE_TRANSITIONS.get(current, [])]
            raise TransitionError(
                f"Invalid transition: '{current.value}' -> '{target.value}'. "
                f"Allowed: {allowed}"
            )
        return target

    @classmethod
    def next_stage(cls, current: StageType) -> Optional[StageType]:
        """Get the default next stage in linear pipeline order.

        Returns None if current is terminal or has no linear next.
        """
        allowed = STAGE_TRANSITIONS.get(current, [])
        # Prefer the first non-terminal, non-failure option
        for s in allowed:
            if s not in TERMINAL_STAGES and s != StageType.FAILED:
                return s
        return None

    @classmethod
    def is_terminal(cls, stage: StageType) -> bool:
        """Check if a stage is terminal."""
        return stage in TERMINAL_STAGES
