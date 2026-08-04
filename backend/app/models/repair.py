"""
Phase 8 — Fix Agent & Bounded Repair Loop models.

Defines data types for:
- Failure diagnosis and classification (FailureDiagnosis, Repairability)
- Repair proposals (RepairProposal)
- Repair attempt tracking (RepairAttempt, RepairSession)
- Final repair result (RepairResult)
- Repair policy (RepairPolicyConfig)
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.models.coding import PatchApplicationResult, PatchSet
from app.models.testing import (
    ExecutionStatus,
    FailureCategory,
    TestFailure,
    TestRunResult,
)


# ── Repairability ──────────────────────────────────────────────


class Repairability(str, Enum):
    """Whether and how a failure can be repaired automatically."""

    REPAIRABLE = "repairable"
    POSSIBLY_REPAIRABLE = "possibly_repairable"
    NOT_REPAIRABLE = "not_repairable"
    ENVIRONMENTAL = "environmental"
    INSUFFICIENT_CONTEXT = "insufficient_context"


# ── Failure Diagnosis ─────────────────────────────────────────


class FailureDiagnosis(BaseModel):
    """Structured diagnosis of test failures.

    Produced by FailureDiagnosisService from TestRunResult evidence.
    Does not expose internal chain-of-thought — likely_cause is an
    engineering conclusion supported by evidence.
    """

    diagnosis_id: str = Field(description="Unique diagnosis identifier")
    run_id: str = Field(description="TestRunResult run_id this diagnosis is based on")
    failure_ids: List[str] = Field(
        default_factory=list, description="TestFailure IDs covered by this diagnosis"
    )
    category: FailureCategory = Field(
        default=FailureCategory.UNKNOWN, description="Dominant failure category"
    )
    summary: str = Field(default="", description="Concise diagnosis summary")
    likely_cause: str = Field(
        default="", description="Engineering conclusion about the root cause"
    )
    confidence: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Confidence in the diagnosis"
    )
    repairability: Repairability = Field(
        default=Repairability.POSSIBLY_REPAIRABLE,
        description="Whether this failure can be automatically repaired",
    )
    affected_files: List[str] = Field(
        default_factory=list,
        description="Source files likely related to the failure",
    )
    affected_symbols: List[str] = Field(
        default_factory=list,
        description="Symbols (function/class names) related to the failure",
    )
    related_plan_steps: List[str] = Field(
        default_factory=list, description="ImplementationPlan step IDs"
    )
    related_patch_changes: List[str] = Field(
        default_factory=list, description="Patch change IDs (CHANGE-XXX)"
    )
    additional_context_needed: List[str] = Field(
        default_factory=list,
        description="Context that would improve diagnosis",
    )
    evidence: List[str] = Field(
        default_factory=list,
        description="Supporting evidence (failure messages, file references)",
    )
    related_to_patch: bool = Field(
        default=True,
        description="Whether this failure appears related to the Phase 6 patch",
    )
    pre_existing_status: str = Field(
        default="UNKNOWN",
        description="PRE_EXISTING | INTRODUCED_BY_PATCH | UNKNOWN | PRE_EXISTING_STATUS_UNKNOWN",
    )
    warnings: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


# ── Repair Proposal ────────────────────────────────────────────


class RepairProposalStatus(str, Enum):
    """Status of a repair proposal."""

    PROPOSED = "proposed"
    NO_REPAIR = "no_repair"
    INSUFFICIENT_CONTEXT = "insufficient_context"
    ENVIRONMENTAL = "environmental"
    REJECTED = "rejected"


class RepairProposal(BaseModel):
    """A structured repair proposal from the Fix Agent.

    Contains either a PatchSet to apply or a decision not to repair.
    """

    proposal_id: str = Field(description="Unique proposal identifier")
    status: RepairProposalStatus = Field(
        default=RepairProposalStatus.PROPOSED
    )
    diagnosis_id: str = Field(default="")
    attempt_number: int = Field(default=1, ge=1)
    target_failure_ids: List[str] = Field(default_factory=list)
    patch: Optional[PatchSet] = Field(
        default=None,
        description="Repair patch (compatible with Phase 6 PatchSet)",
    )
    reason: str = Field(
        default="", description="Why this repair was chosen (or why not)"
    )
    expected_effect: str = Field(
        default="", description="What the repair should achieve"
    )
    context_used: List[str] = Field(
        default_factory=list,
        description="Context sources consulted for this repair",
    )
    warnings: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


# ── Repair Attempt ─────────────────────────────────────────────


class RepairAttemptStatus(str, Enum):
    """Status of a single repair attempt."""

    PENDING = "pending"
    PROPOSED = "proposed"
    APPLIED = "applied"
    VALIDATED = "validated"
    TESTING = "testing"
    PASSED = "passed"
    FAILED = "failed"
    REJECTED = "rejected"
    ROLLED_BACK = "rolled_back"
    ERROR = "error"
    SKIPPED = "skipped"


class RepairAttempt(BaseModel):
    """A single repair attempt within a repair session."""

    attempt_id: str = Field(description="Unique attempt identifier")
    attempt_number: int = Field(default=1, ge=1)
    diagnosis: Optional[FailureDiagnosis] = Field(default=None)
    proposal: Optional[RepairProposal] = Field(default=None)
    patch_application: Optional[PatchApplicationResult] = Field(default=None)
    test_result: Optional[TestRunResult] = Field(default=None)
    status: RepairAttemptStatus = Field(default=RepairAttemptStatus.PENDING)
    started_at: Optional[str] = Field(default=None)
    finished_at: Optional[str] = Field(default=None)
    duration_seconds: float = Field(default=0.0)
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


# ── Repair Session ─────────────────────────────────────────────


class RepairSessionStatus(str, Enum):
    """Overall status of a repair session."""

    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    NO_REPAIR = "no_repair"
    ENVIRONMENTAL = "environmental"
    MAX_ATTEMPTS = "max_attempts"
    NO_PROGRESS = "no_progress"
    REPEATED_PATCH = "repeated_patch"
    UNSAFE_REPAIR = "unsafe_repair"
    ERROR = "error"


class RepairSession(BaseModel):
    """Tracks an entire bounded repair session.

    Contains the full history of attempts and the final outcome.
    """

    session_id: str = Field(description="Unique session identifier")
    workspace_id: str = Field(description="Workspace being repaired")
    initial_test_result: Optional[TestRunResult] = Field(default=None)
    attempts: List[RepairAttempt] = Field(default_factory=list)
    best_attempt: Optional[RepairAttempt] = Field(default=None)
    final_test_result: Optional[TestRunResult] = Field(default=None)
    status: RepairSessionStatus = Field(default=RepairSessionStatus.RUNNING)
    stop_reason: str = Field(default="")
    started_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    finished_at: Optional[str] = Field(default=None)
    duration_seconds: float = Field(default=0.0)
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


# ── Repair Result ──────────────────────────────────────────────


class RepairResult(BaseModel):
    """Final structured result of a Phase 8 repair operation.

    This is the primary output consumed by Phase 9 (Reviewer Agent).
    """

    session: RepairSession = Field(description="The full repair session")
    status: RepairSessionStatus = Field(description="Overall status")
    initial_test_result: Optional[TestRunResult] = Field(default=None)
    final_test_result: Optional[TestRunResult] = Field(default=None)
    attempts: int = Field(default=0)
    best_attempt: Optional[RepairAttempt] = Field(default=None)
    stop_reason: str = Field(default="")
    remaining_failures: List[TestFailure] = Field(default_factory=list)
    workspace_id: str = Field(default="")
    summary: str = Field(default="")
    duration_seconds: float = Field(default=0.0)
    warnings: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


# ── Repair Capabilities ────────────────────────────────────────


class RepairCapabilities(BaseModel):
    """Reported capabilities of the Phase 8 repair system."""

    max_repair_attempts: int = Field(default=3)
    supported_frameworks: List[str] = Field(
        default_factory=lambda: ["pytest"]
    )
    diagnosis_categories: List[str] = Field(
        default_factory=lambda: [c.value for c in FailureCategory]
    )
    repairability_classes: List[str] = Field(
        default_factory=lambda: [r.value for r in Repairability]
    )
    test_tampering_protection: bool = Field(default=True)
    config_weakening_protection: bool = Field(default=True)
    rollback_supported: bool = Field(default=True)
    llm_required: bool = Field(default=True)


# ── Failure Fingerprint ────────────────────────────────────────


def fingerprint_failure(failure: TestFailure) -> str:
    """Create a deterministic failure fingerprint.

    Used to detect repeated failure states across repair attempts.
    Excludes volatile values (timestamps, temp paths, memory addresses).
    """
    import hashlib

    parts = [
        failure.failure_type.value if hasattr(failure.failure_type, "value") else str(failure.failure_type),
        failure.test_name or "",
        failure.file_path or "",
        _normalize_message(failure.message),
    ]
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _normalize_message(msg: str) -> str:
    """Normalize a failure message to exclude volatile values."""
    import re

    # Remove hex addresses
    msg = re.sub(r"0x[0-9a-fA-F]+", "0x...", msg)
    # Remove temp paths
    msg = re.sub(r"/tmp/[^\s]+", "/tmp/...", msg)
    # Remove absolute paths on Windows
    msg = re.sub(r"[A-Z]:\\[^\s]+", "C:\\...", msg)
    # Remove timestamps
    msg = re.sub(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", "<timestamp>", msg)
    # Take first 200 chars to avoid excessive variability
    return msg[:200]


# ── Patch Fingerprint ──────────────────────────────────────────


def fingerprint_patch(proposal: RepairProposal) -> str:
    """Create a deterministic fingerprint of a repair patch proposal.

    Used to detect repeated (identical) patch proposals.
    """
    import hashlib

    if not proposal.patch:
        return "no_patch"

    parts = []
    for change in proposal.patch.changes:
        op = change.operation.value if hasattr(change.operation, "value") else str(change.operation)
        path = change.path
        content_hash = ""
        if change.new_content:
            content_hash = hashlib.sha256(
                change.new_content.encode("utf-8")
            ).hexdigest()[:16]
        parts.append(f"{op}:{path}:{content_hash}")

    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
