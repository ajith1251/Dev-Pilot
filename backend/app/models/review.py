"""
Phase 9 — Reviewer Agent & Deterministic Quality Gate models.

Defines data types for:
- ReviewInput / ReviewContext: structured review input
- RequirementCoverage: requirement-to-implementation mapping
- ReviewFinding: individual review findings
- ReviewReport: complete report with all findings
- QualityGateResult: final deterministic gate decision
- DeterministicReviewResult: output of deterministic checks
- ChangedFileSummary: final change summary
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.models.coding import PatchApplicationResult, PatchSet
from app.models.issues import ImplementationPlan, StructuredRequirements
from app.models.profile import RepositoryProfile
from app.models.rag import RetrievedContext
from app.models.repair import RepairResult
from app.models.repair import RepairSessionStatus
from app.models.testing import TestRunResult


# ── Quality Gate Decision ───────────────────────────────────────


class QualityGateDecision(str, Enum):
    """Final decision of the quality gate."""

    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_HUMAN_REVIEW = "needs_human_review"
    INCOMPLETE = "incomplete"


# ── Reason Codes ────────────────────────────────────────────────


class ReasonCode(str, Enum):
    """Machine-readable reason codes for quality gate decisions."""

    # Approval
    REVIEW_PASSED = "review_passed"

    # Rejections
    TESTS_FAILED = "tests_failed"
    REQUIREMENT_UNSATISFIED = "requirement_unsatisfied"
    SECURITY_BLOCKER = "security_blocker"
    TEST_TAMPERING = "test_tampering"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    UNRESOLVED_REPAIR = "unresolved_repair"
    SCOPE_VIOLATION = "scope_violation"
    CRITICAL_FINDING = "critical_finding"
    REPAIR_FAILED = "repair_failed"
    ENVIRONMENTAL_FAILURE = "environmental_failure"
    MISSING_VERIFICATION = "missing_verification"

    # Human review
    AMBIGUOUS_REQUIREMENTS = "ambiguous_requirements"
    LLM_REVIEW_UNAVAILABLE = "llm_review_unavailable"
    CONFLICTING_EVIDENCE = "conflicting_evidence"
    INCOMPLETE_REVIEW = "incomplete_review"


# ── Finding Severity & Categories ───────────────────────────────


class FindingSeverity(str, Enum):
    """Severity of a review finding."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class FindingCategory(str, Enum):
    """Category of a review finding."""

    REQUIREMENT = "requirement"
    CORRECTNESS = "correctness"
    TESTING = "testing"
    SECURITY = "security"
    ARCHITECTURE = "architecture"
    MAINTAINABILITY = "maintainability"
    SCOPE = "scope"
    REGRESSION = "regression"
    DOCUMENTATION = "documentation"
    QUALITY = "quality"
    TAMPERING = "tampering"


# ── Requirement Coverage ────────────────────────────────────────


class RequirementStatus(str, Enum):
    """Status of a requirement in the review."""

    SATISFIED = "satisfied"
    PARTIALLY_SATISFIED = "partially_satisfied"
    UNSATISFIED = "unsatisfied"
    UNVERIFIED = "unverified"
    NOT_APPLICABLE = "not_applicable"


class RequirementCoverage(BaseModel):
    """A single requirement's coverage assessment."""

    requirement_id: str = Field(description="Requirement identifier (e.g. REQ-001)")
    requirement_description: str = Field(description="The requirement text")
    status: RequirementStatus = Field(default=RequirementStatus.UNVERIFIED)
    plan_steps: List[str] = Field(
        default_factory=list,
        description="Plan step IDs that address this requirement",
    )
    changed_files: List[str] = Field(
        default_factory=list,
        description="Files changed to implement this requirement",
    )
    evidence: List[str] = Field(
        default_factory=list,
        description="Evidence supporting the status assessment",
    )
    tests: List[str] = Field(
        default_factory=list,
        description="Test identifiers that verify this requirement",
    )
    notes: str = Field(
        default="",
        description="Additional notes about this requirement",
    )


# ── Plan Assessment ─────────────────────────────────────────────


class PlanStepStatus(str, Enum):
    """Status of a plan step in the review."""

    IMPLEMENTED = "implemented"
    PARTIAL = "partial"
    MISSING = "missing"
    SUPERSEDED = "superseded"
    NOT_APPLICABLE = "not_applicable"


class PlanStepAssessment(BaseModel):
    """Assessment of a single plan step."""

    step_id: str = Field(description="Plan step identifier (e.g. STEP-001)")
    step_title: str = Field(description="Step title")
    status: PlanStepStatus = Field(default=PlanStepStatus.MISSING)
    changed_files: List[str] = Field(
        default_factory=list,
        description="Files that implement this step",
    )
    notes: str = Field(default="", description="Assessment notes")


# ── Review Finding ──────────────────────────────────────────────


class ReviewFinding(BaseModel):
    """A single finding from the review process."""

    finding_id: str = Field(description="Unique finding identifier")
    category: FindingCategory = Field(description="Category of the finding")
    severity: FindingSeverity = Field(description="Severity level")
    title: str = Field(description="Short finding title")
    description: str = Field(
        description="Detailed description with observable evidence"
    )
    file_path: Optional[str] = Field(
        default=None, description="File path related to finding"
    )
    line_start: Optional[int] = Field(
        default=None, description="Start line number"
    )
    line_end: Optional[int] = Field(
        default=None, description="End line number"
    )
    symbol: Optional[str] = Field(
        default=None, description="Symbol name related to finding"
    )
    requirement_ids: List[str] = Field(
        default_factory=list,
        description="Related requirement identifiers",
    )
    plan_step_ids: List[str] = Field(
        default_factory=list,
        description="Related plan step identifiers",
    )
    evidence: List[str] = Field(
        default_factory=list,
        description="Supporting evidence for this finding",
    )
    recommendation: str = Field(
        default="", description="Recommended action"
    )
    blocking: bool = Field(
        default=False,
        description="Whether this finding blocks approval",
    )
    confidence: float = Field(
        default=0.8, ge=0.0, le=1.0,
        description="Confidence in this finding",
    )


# ── Changed File Summary ────────────────────────────────────────


class ChangedFileSummary(BaseModel):
    """Summary of a single changed file in the implementation."""

    path: str = Field(description="File path relative to workspace root")
    change_type: str = Field(
        description="create | modify | delete"
    )
    has_original_patch: bool = Field(default=False)
    repair_attempts: int = Field(default=0)
    related_requirements: List[str] = Field(default_factory=list)
    related_tests: List[str] = Field(default_factory=list)
    final_content_preview: str = Field(
        default="", description="Preview of final file content (first 500 chars)"
    )


# ── Test Summary ────────────────────────────────────────────────


class TestSummary(BaseModel):
    """Summary of test evidence for the review."""

    executed: bool = Field(default=False)
    status: str = Field(default="unknown")
    tests_passed: Optional[int] = Field(default=None)
    tests_failed: Optional[int] = Field(default=None)
    tests_skipped: Optional[int] = Field(default=None)
    commands_total: int = Field(default=0)
    commands_passed: int = Field(default=0)
    commands_failed: int = Field(default=0)
    commands_rejected: int = Field(default=0)
    duration_seconds: float = Field(default=0.0)
    has_skipped: bool = Field(default=False)
    has_timeout: bool = Field(default=False)
    environment_ready: bool = Field(default=True)
    warnings: List[str] = Field(default_factory=list)


# ── Repair Summary ──────────────────────────────────────────────


class RepairSummary(BaseModel):
    """Summary of repair evidence for the review."""

    attempted: bool = Field(default=False)
    status: Optional[str] = Field(default=None)
    attempts: int = Field(default=0)
    stop_reason: str = Field(default="")
    remaining_failures: int = Field(default=0)


# ── Security Summary ────────────────────────────────────────────


class SecuritySummary(BaseModel):
    """Summary of security review findings."""

    passed: bool = Field(default=True)
    blocked_patterns: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


# ── Scope Summary ───────────────────────────────────────────────


class ScopeSummary(BaseModel):
    """Summary of scope review findings."""

    in_scope_files: List[str] = Field(default_factory=list)
    out_of_scope_files: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


# ── Metrics ─────────────────────────────────────────────────────


class QualityMetrics(BaseModel):
    """Quality score dimensions (heuristic presentation only)."""

    requirements: float = Field(default=0.0, ge=0.0, le=100.0)
    correctness: float = Field(default=0.0, ge=0.0, le=100.0)
    testing: float = Field(default=0.0, ge=0.0, le=100.0)
    security: float = Field(default=0.0, ge=0.0, le=100.0)
    maintainability: float = Field(default=0.0, ge=0.0, le=100.0)
    architecture: float = Field(default=0.0, ge=0.0, le=100.0)
    scope: float = Field(default=0.0, ge=0.0, le=100.0)

    @property
    def overall(self) -> float:
        """Weighted overall score (not the primary decision mechanism)."""
        weights = {
            "requirements": 0.25,
            "correctness": 0.20,
            "testing": 0.15,
            "security": 0.20,
            "maintainability": 0.05,
            "architecture": 0.05,
            "scope": 0.10,
        }
        score = sum(
            getattr(self, dim) * weights[dim]
            for dim in weights
        )
        return round(score, 1)


# ── Review Report ───────────────────────────────────────────────


class ReviewReport(BaseModel):
    """Complete structured review report.

    This represents evidence collected during review.
    It does NOT itself grant approval — that is the QualityGate's role.
    """

    review_id: str = Field(description="Unique review identifier")
    workspace_id: str = Field(default="", description="Workspace identifier")

    # Requirement coverage
    requirement_coverage: List[RequirementCoverage] = Field(
        default_factory=list,
        description="Per-requirement coverage assessment",
    )

    # Plan assessment
    plan_assessment: List[PlanStepAssessment] = Field(
        default_factory=list,
        description="Per-step plan implementation assessment",
    )

    # Findings
    findings: List[ReviewFinding] = Field(
        default_factory=list,
        description="All review findings",
    )

    # Summaries
    test_summary: TestSummary = Field(default_factory=TestSummary)
    repair_summary: RepairSummary = Field(default_factory=RepairSummary)
    security_summary: SecuritySummary = Field(default_factory=SecuritySummary)
    scope_summary: ScopeSummary = Field(default_factory=ScopeSummary)

    # Agent summary (from ReviewerAgent)
    agent_summary: str = Field(
        default="",
        description="ReviewerAgent narrative summary (non-authoritative)",
    )

    # Quality metrics (heuristic, not decision)
    quality_metrics: Optional[QualityMetrics] = Field(default=None)

    # Metadata
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    duration_seconds: float = Field(default=0.0)
    warnings: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


# ── Quality Gate Result ─────────────────────────────────────────


class QualityGateResult(BaseModel):
    """Final result of the deterministic quality gate.

    This is the authoritative decision — NOT the LLM's opinion.
    """

    review_id: str = Field(description="Corresponding ReviewReport ID")
    decision: QualityGateDecision = Field(
        default=QualityGateDecision.INCOMPLETE,
        description="Final gate decision",
    )
    blocking_findings: List[str] = Field(
        default_factory=list,
        description="Titles of blocking findings",
    )
    warnings: List[str] = Field(default_factory=list)

    # Summary
    requirements_status: RequirementStatus = Field(
        default=RequirementStatus.UNVERIFIED,
    )
    requirements_satisfied: int = Field(default=0)
    requirements_partial: int = Field(default=0)
    requirements_unsatisfied: int = Field(default=0)
    requirements_unverified: int = Field(default=0)

    verification_status: str = Field(
        default="unknown",
        description="PASS | FAIL | SKIPPED | INCONCLUSIVE",
    )
    security_status: str = Field(
        default="unknown",
        description="PASS | FAIL | INCONCLUSIVE",
    )

    # Score (heuristic only — never the primary decision)
    score: Optional[float] = Field(
        default=None, description="Overall quality score (0-100, informational)"
    )

    # Reason codes
    reason_codes: List[str] = Field(
        default_factory=list,
        description="Machine-readable reason codes",
    )
    summary: str = Field(default="", description="Human-readable summary")

    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# ── Deterministic Review Result ─────────────────────────────────


class DeterministicReviewResult(BaseModel):
    """Result of deterministic review checks."""

    passed: bool = Field(default=True)
    findings: List[ReviewFinding] = Field(default_factory=list)
    test_summary: TestSummary = Field(default_factory=TestSummary)
    security_summary: SecuritySummary = Field(default_factory=SecuritySummary)
    scope_summary: ScopeSummary = Field(default_factory=ScopeSummary)
    warnings: List[str] = Field(default_factory=list)


# ── Review Input ────────────────────────────────────────────────


class ReviewInput:
    """Structured input to the Phase 9 review pipeline.

    Combines data from Phases 4-8 into a single review request.
    """

    def __init__(
        self,
        workspace_id: str = "",
        workspace_root: str = "",
        requirements: Optional[StructuredRequirements] = None,
        implementation_plan: Optional[ImplementationPlan] = None,
        original_patch: Optional[PatchSet] = None,
        patch_application: Optional[PatchApplicationResult] = None,
        repair_result: Optional[RepairResult] = None,
        test_result: Optional[TestRunResult] = None,
        repository_profile: Optional[RepositoryProfile] = None,
        retrieved_context: Optional[RetrievedContext] = None,
        changed_files: Optional[List[str]] = None,
        final_workspace_metadata: Optional[Dict[str, Any]] = None,
        extra_context: Optional[Dict[str, Any]] = None,
    ):
        self.workspace_id = workspace_id
        self.workspace_root = workspace_root
        self.requirements = requirements
        self.implementation_plan = implementation_plan
        self.original_patch = original_patch
        self.patch_application = patch_application
        self.repair_result = repair_result
        self.test_result = test_result
        self.repository_profile = repository_profile
        self.retrieved_context = retrieved_context
        self.changed_files = changed_files or []
        self.final_workspace_metadata = final_workspace_metadata or {}
        self.extra_context = extra_context or {}


# ── Review Context ──────────────────────────────────────────────


class ReviewContext:
    """Bounded review context built from ReviewInput.

    This is what ReviewerAgent receives — NOT the entire repository.
    Priority: requirements > final changed code > relevant tests >
    final test evidence > implementation plan > repair history >
    relevant architecture context > original patch metadata.
    """

    def __init__(
        self,
        requirements_text: str = "",
        plan_text: str = "",
        changed_files_summaries: Optional[List[ChangedFileSummary]] = None,
        changed_files_content: str = "",
        test_evidence: str = "",
        repair_history: str = "",
        original_patch_summary: str = "",
        architecture_context: str = "",
        warnings: Optional[List[str]] = None,
    ):
        self.requirements_text = requirements_text
        self.plan_text = plan_text
        self.changed_files_summaries = changed_files_summaries or []
        self.changed_files_content = changed_files_content
        self.test_evidence = test_evidence
        self.repair_history = repair_history
        self.original_patch_summary = original_patch_summary
        self.architecture_context = architecture_context
        self.warnings = warnings or []


# ── Capabilities ────────────────────────────────────────────────


class ReviewCapabilities(BaseModel):
    """Reported capabilities of the Phase 9 review system."""

    supported_categories: List[str] = Field(
        default_factory=lambda: [c.value for c in FindingCategory]
    )
    supported_severities: List[str] = Field(
        default_factory=lambda: [s.value for s in FindingSeverity]
    )
    requirement_statuses: List[str] = Field(
        default_factory=lambda: [s.value for s in RequirementStatus]
    )
    plan_step_statuses: List[str] = Field(
        default_factory=lambda: [s.value for s in PlanStepStatus]
    )
    gate_decisions: List[str] = Field(
        default_factory=lambda: [d.value for d in QualityGateDecision]
    )
    deterministic_checks: List[str] = Field(
        default_factory=lambda: [
            "final_verification_status",
            "remaining_failures",
            "requirement_coverage_completeness",
            "unresolved_repair_result",
            "changed_file_scope",
            "repository_scope_violation",
            "test_deletion_detection",
            "skip_xfail_introduction",
            "protected_security_invariants",
            "workspace_integrity",
        ]
    )
    llm_review_available: bool = Field(default=False)
    evidence_validation: bool = Field(default=True)
    read_only: bool = Field(default=True)


# ── Agent Review Output ─────────────────────────────────────────


class AgentReview(BaseModel):
    """Output from the ReviewerAgent (non-authoritative)."""

    findings: List[ReviewFinding] = Field(default_factory=list)
    requirement_assessments: List[RequirementCoverage] = Field(
        default_factory=list
    )
    summary: str = Field(default="")
    warnings: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
