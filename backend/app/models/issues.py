"""
Data models for issue analysis and planning (Phase 4).

Defines the input/output payloads for the Issue Analyzer,
StructuredRequirements, ImplementationPlan, PlanValidator, etc.
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from app.models.base import Severity


# ── Enums ───────────────────────────────────────────────────────


class IssueType(str, Enum):
    """Category of a GitHub issue or development task."""

    BUG = "bug"
    FEATURE = "feature"
    ENHANCEMENT = "enhancement"
    DOCUMENTATION = "documentation"
    PERFORMANCE = "performance"
    SECURITY = "security"
    REFACTOR = "refactor"
    TESTING = "testing"
    QUESTION = "question"
    DEPRECATION = "deprecation"
    OTHER = "other"


class EstimatedEffort(str, Enum):
    """Rough effort estimate for an issue."""

    TRIVIAL = "trivial"       # Minutes
    SMALL = "small"           # Hours
    MEDIUM = "medium"         # Half-day to a day
    LARGE = "large"           # Multiple days
    XLARGE = "xlarge"         # Week+
    UNCERTAIN = "uncertain"   # Cannot estimate


class RequirementType(str, Enum):
    """Type of a requirement extracted from the issue."""

    FUNCTIONAL = "functional"
    NON_FUNCTIONAL = "non_functional"
    TECHNICAL = "technical"
    UI_UX = "ui_ux"
    TEST = "test"
    DOCUMENTATION = "documentation"
    SECURITY = "security"
    PERFORMANCE = "performance"


# ── Phase 4 Enums ───────────────────────────────────────────────


class TaskSource(str, Enum):
    """Where the task originated."""

    GITHUB_ISSUE = "github_issue"
    USER_TASK = "user_task"


class AmbiguityCategory(str, Enum):
    """Category of ambiguity in a task."""

    MISSING_CONTEXT = "missing_context"
    VAGUE_DESCRIPTION = "vague_description"
    CONTRADICTORY = "contradictory"
    MULTIPLE_INTERPRETATIONS = "multiple_interpretations"
    UNSPECIFIED_SCOPE = "unspecified_scope"
    OTHER = "other"


class RiskCategory(str, Enum):
    """Category of engineering risk."""

    COMPATIBILITY = "compatibility"
    PERFORMANCE = "performance"
    SECURITY = "security"
    BREAKING_CHANGE = "breaking_change"
    COMPLEXITY = "complexity"
    DEPENDENCY = "dependency"
    DATA_LOSS = "data_loss"
    INCOMPLETE_SPECIFICATION = "incomplete_specification"
    LOGIC_BUG = "logic_bug"
    OTHER = "other"


class ConstraintCategory(str, Enum):
    """Category of constraint on implementation."""

    BACKWARD_COMPATIBILITY = "backward_compatibility"
    API_CONTRACT = "api_contract"
    FRAMEWORK = "framework"
    SECURITY = "security"
    PERFORMANCE = "performance"
    DATABASE = "database"
    RESOURCE = "resource"
    DEADLINE = "deadline"
    SCOPE = "scope"
    OTHER = "other"


class PlanStepStatus(str, Enum):
    """Status of a plan step during execution."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"
    SKIPPED = "skipped"


# ── Reusable sub-models ─────────────────────────────────────────


class Requirement(BaseModel):
    """A single requirement extracted from the issue."""

    description: str = Field(description="What needs to be done")
    requirement_type: RequirementType = Field(
        default=RequirementType.FUNCTIONAL
    )
    is_implied: bool = Field(
        default=False,
        description="Whether this was implied rather than explicitly stated",
    )
    acceptance_note: Optional[str] = Field(
        default=None,
        description="How to verify this requirement is met",
    )


# ── Phase 4 Domain Models ───────────────────────────────────────


class TaskInput(BaseModel):
    """Normalised input for the Phase 4 planning pipeline.

    Can originate from a GitHub issue or a direct user-provided task.
    """

    source: TaskSource = Field(description="Where the task originated")
    title: str = Field(description="Task / issue title")
    description: str = Field(
        default="", description="Full description / body of the task")

    # GitHub-specific (populated when source=github_issue)
    issue_number: Optional[int] = Field(default=None)
    issue_url: Optional[str] = Field(default=None)
    labels: List[str] = Field(default_factory=list)
    repository: Optional[str] = Field(
        default=None, description="owner/repo when from GitHub")

    # Repository context (compact, deterministic)
    repo_languages: List[str] = Field(default_factory=list)
    repo_technologies: List[str] = Field(default_factory=list)
    repo_modules: List[str] = Field(default_factory=list)
    repo_commands: List[str] = Field(default_factory=list)
    repo_important_files: List[str] = Field(default_factory=list)
    repo_tree_preview: str = Field(
        default="", description="Compact repository tree")


class Ambiguity(BaseModel):
    """An ambiguity or missing detail in the task."""

    description: str = Field(description="What is ambiguous or unclear")
    category: AmbiguityCategory = Field(default=AmbiguityCategory.OTHER)
    question: str = Field(
        description="Question that would resolve this ambiguity"
    )


class Risk(BaseModel):
    """An engineering risk identified during task analysis."""

    description: str = Field(description="What the risk is")
    category: RiskCategory = Field(default=RiskCategory.OTHER)
    likelihood: str = Field(
        default="medium", description="low|medium|high"
    )
    impact: str = Field(
        default="medium", description="low|medium|high"
    )
    mitigation: Optional[str] = Field(
        default=None, description="Suggested mitigation"
    )


class Constraint(BaseModel):
    """A constraint on the implementation."""

    description: str = Field(description="What the constraint is")
    category: ConstraintCategory = Field(default=ConstraintCategory.OTHER)
    source: str = Field(
        default="task", description="Where this constraint comes from"
    )


class AffectedArea(BaseModel):
    """A module, component, or area likely affected by the task."""

    path: str = Field(description="Path or identifier of the affected area")
    description: str = Field(
        default="", description="How this area is likely affected")
    confidence: str = Field(
        default="medium", description="low|medium|high")


class StructuredRequirements(BaseModel):
    """Structured output of the Issue Analyzer — before planning.

    The bridge between raw task text and a formal ImplementationPlan.
    """

    objective: str = Field(
        description="Concise statement of what must be achieved"
    )

    requirements: List[Requirement] = Field(
        default_factory=list,
        description="Individual actionable requirements extracted from the task",
    )
    constraints: List[Constraint] = Field(
        default_factory=list,
        description="Constraints that must be respected",
    )
    likely_affected_areas: List[AffectedArea] = Field(
        default_factory=list,
        description="Areas/modules likely needing changes",
    )
    ambiguities: List[Ambiguity] = Field(
        default_factory=list,
        description="Missing or unclear information",
    )
    risks: List[Risk] = Field(
        default_factory=list,
        description="Engineering risks identified",
    )
    assumptions: List[str] = Field(
        default_factory=list,
        description="Assumptions made during analysis",
    )

    # Metadata
    confidence: str = Field(
        default="medium",
        description="Overall confidence in analysis: low|medium|high",
    )
    error: Optional[str] = Field(
        default=None,
        description="Error message if analysis failed",
    )


class ImplementationStep(BaseModel):
    """A single step in an ImplementationPlan."""

    id: str = Field(description="Unique step identifier (e.g. STEP-001)")
    title: str = Field(description="Short step title")
    description: str = Field(description="Detailed description of what to do")
    affected_areas: List[str] = Field(
        default_factory=list,
        description="Files, modules, or components this step touches",
    )
    depends_on: List[str] = Field(
        default_factory=list,
        description="IDs of steps that must precede this one",
    )
    expected_changes: str = Field(
        default="", description="Summary of expected code/config changes")
    validation: str = Field(
        default="", description="How to validate this step succeeded")
    risk: Optional[str] = Field(
        default=None, description="Specific risk for this step")
    effort_estimate: Optional[str] = Field(
        default=None, description="trivial|small|medium|large|xlarge")


class ImplementationPlan(BaseModel):
    """A structured implementation plan produced by the Planner Agent."""

    summary: str = Field(
        description="High-level summary of the entire plan"
    )
    objective: str = Field(
        description="The objective this plan addresses"
    )
    steps: List[ImplementationStep] = Field(
        description="Ordered implementation steps"
    )
    test_strategy: str = Field(
        default="", description="How testing should be approached"
    )
    documentation_impact: str = Field(
        default="", description="Documentation that needs updating"
    )
    risks: List[Risk] = Field(
        default_factory=list,
        description="Overall plan-level risks",
    )
    assumptions: List[str] = Field(
        default_factory=list,
        description="Assumptions this plan is based on",
    )

    # Optional metadata
    requirements_coverage: Dict[str, List[str]] = Field(
        default_factory=dict,
        description="Maps requirement identifiers to step IDs they address",
    )
    error: Optional[str] = Field(
        default=None,
        description="Error message if planning failed",
    )


class PlanValidationResult(BaseModel):
    """Result of validating an ImplementationPlan."""

    is_valid: bool = Field(description="Whether the plan passed validation")
    errors: List[str] = Field(
        default_factory=list,
        description="Validation errors (if invalid)",
    )
    warnings: List[str] = Field(
        default_factory=list,
        description="Validation warnings (non-blocking)",
    )
    checked_step_count: int = Field(default=0)
    checked_dependency_count: int = Field(default=0)
    has_cycles: bool = Field(default=False)
    has_self_dependencies: bool = Field(default=False)
    has_missing_dependencies: bool = Field(default=False)
    has_duplicate_ids: bool = Field(default=False)


# ── Existing Models (Phase 1) — unchanged ───────────────────────


class IssueAnalysisInput(BaseModel):
    """Input payload for the Issue Analyzer agent."""

    issue_url: Optional[str] = Field(
        default=None,
        description="GitHub issue URL (e.g. https://github.com/owner/repo/issues/123)",
    )
    title: Optional[str] = Field(
        default=None,
        description="Issue title (used with body for inline input)",
    )
    body: Optional[str] = Field(
        default=None,
        description="Issue body / description text",
    )
    repo_context: Optional[str] = Field(
        default=None,
        description="Optional repository analysis context to improve component detection",
    )


class IssueAnalysisOutput(BaseModel):
    """Structured output from the Issue Analyzer agent."""

    title: str = Field(description="Normalized issue title")
    summary: str = Field(description="One-paragraph summary of the issue")
    issue_type: IssueType = Field(
        default=IssueType.OTHER, description="Classified issue type"
    )
    severity: Severity = Field(
        default=Severity.MEDIUM, description="Assessed severity"
    )
    priority_score: int = Field(
        default=5, ge=1, le=10, description="Priority score 1 (lowest) to 10 (highest)"
    )
    affected_components: List[str] = Field(
        default_factory=list,
        description="Components, modules, or areas likely affected",
    )
    requirements: List[Requirement] = Field(
        default_factory=list,
        description="Extracted requirements from the issue",
    )
    acceptance_criteria: List[str] = Field(
        default_factory=list,
        description="Concrete conditions that must be met for this issue to be resolved",
    )
    suggested_labels: List[str] = Field(
        default_factory=list,
        description="Suggested GitHub labels for this issue",
    )
    estimated_effort: EstimatedEffort = Field(
        default=EstimatedEffort.UNCERTAIN,
        description="Rough effort estimate",
    )
    related_files: List[str] = Field(
        default_factory=list,
        description="Files or file patterns likely needing changes",
    )
    needs_more_info: bool = Field(
        default=False,
        description="Whether the issue lacks sufficient detail",
    )
    missing_info_questions: List[str] = Field(
        default_factory=list,
        description="Questions to ask if more info is needed",
    )
    error: Optional[str] = Field(
        default=None,
        description="Error message if analysis failed",
    )
