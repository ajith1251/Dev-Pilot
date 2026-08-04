"""
Phase 15 — Multi-Agent Collaboration models.

Shared Run Intelligence: agents exchange *structured evidence, decisions
and artifacts* — never private chain-of-thought. These models define the
durable collaboration records persisted via the RunStore:

- EvidenceRef        — a single piece of verifiable evidence
- AgentHandoff       — a bounded handoff from one agent to the next
- RunDecision        — a lightweight engineering decision record
- EvidenceConflict   — a detected contradiction between evidence sources
- SharedRunContext   — the authoritative per-run collaboration summary

All text fields are treated as untrusted content (repository code, task
descriptions, agent output). They are never injected as system
instructions; they are validated deterministically where possible.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.models.base import new_id


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Bounds (Performance & Safety) ───────────────────────────────
# Collaboration history must never grow prompts or storage unboundedly.

MAX_HANDOFFS_PER_RUN = 50          # hard cap on handoffs persisted per run
MAX_EVIDENCE_PER_HANDOFF = 20      # evidence refs per handoff
MAX_DECISIONS_PER_RUN = 100        # hard cap on decision records per run
MAX_HANDOFFS_SELECTED = 8          # max handoffs injected into agent context
MAX_CONFLICTS_PER_RUN = 50         # hard cap on stored conflicts
SUMMARY_MAX_LEN = 500              # bounded handoff summary
CLAIM_MAX_LEN = 300                # bounded individual claim / decision


# ── Evidence ────────────────────────────────────────────────────


class EvidenceType(str, Enum):
    """Canonical evidence kinds shared across agents."""

    SOURCE_CODE = "source_code"
    GRAPH_RELATIONSHIP = "graph_relationship"
    RETRIEVAL = "retrieval"
    PLAN = "plan"
    PATCH = "patch"
    TEST_RESULT = "test_result"
    FAILURE = "failure"
    REPAIR = "repair"
    REVIEW_FINDING = "review_finding"
    QUALITY_GATE = "quality_gate"
    HISTORICAL_MEMORY = "historical_memory"
    AGENT_CLAIM = "agent_claim"


class EvidenceRef(BaseModel):
    """A single reference to verifiable evidence."""

    evidence_id: str = Field(default_factory=lambda: f"ev-{new_id()[:8]}")
    type: EvidenceType = Field(description="Canonical evidence kind")
    reference: str = Field(
        default="", description="Stable reference (symbol_id, test name, artifact id, file path)"
    )
    detail: str = Field(default="", max_length=CLAIM_MAX_LEN, description="Short human description")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    created_at: str = Field(default_factory=_utcnow_iso)
    provenance: Dict[str, Any] = Field(
        default_factory=dict, description="Optional provenance chain (phase 14 compatible)"
    )


# ── Handoff ─────────────────────────────────────────────────────


class HandoffStatus(str, Enum):
    """Validation status of a handoff's claims."""

    VALIDATED = "validated"          # claims matched deterministic evidence
    PARTIAL = "partial"              # some claims verified, some not
    UNVERIFIED = "unverified"        # no deterministic evidence available
    REJECTED = "rejected"            # claims contradicted deterministic evidence


class AgentHandoff(BaseModel):
    """A structured, bounded handoff between two agents.

    Carries engineering evidence only — never hidden reasoning,
    internal scratchpads, or raw model traces.
    """

    handoff_id: str = Field(default_factory=lambda: f"HO-{new_id().upper()[:8]}")
    run_id: str = Field(description="Owning run")
    from_agent: str = Field(description="e.g. planner")
    to_agent: str = Field(description="e.g. coding")
    stage: str = Field(description="StageType value at handoff creation")

    summary: str = Field(default="", max_length=SUMMARY_MAX_LEN)
    decisions: List[str] = Field(default_factory=list, max_length=8)
    evidence_refs: List[EvidenceRef] = Field(
        default_factory=list, max_length=MAX_EVIDENCE_PER_HANDOFF
    )
    artifact_refs: List[str] = Field(default_factory=list, max_length=8)
    affected_symbols: List[str] = Field(default_factory=list, max_length=20)
    warnings: List[str] = Field(default_factory=list, max_length=5)
    open_questions: List[str] = Field(default_factory=list, max_length=5)

    status: HandoffStatus = Field(default=HandoffStatus.UNVERIFIED)
    validation: Dict[str, str] = Field(
        default_factory=dict,
        description="claim -> validated|rejected|unverified (deterministic check results)",
    )

    created_at: str = Field(default_factory=_utcnow_iso)


# ── Decisions ───────────────────────────────────────────────────


class DecisionType(str, Enum):
    """Category of an engineering decision."""

    PLANNING = "planning"
    IMPLEMENTATION = "implementation"
    TESTING = "testing"
    REPAIR = "repair"
    REVIEW = "review"


class RunDecision(BaseModel):
    """A lightweight engineering decision record."""

    decision_id: str = Field(default_factory=lambda: f"DEC-{new_id().upper()[:8]}")
    run_id: str = Field(description="Owning run")
    decision_type: DecisionType = Field(description="Category")
    statement: str = Field(description="What was decided", max_length=CLAIM_MAX_LEN)
    made_by: str = Field(description="Agent that made the decision")
    evidence_refs: List[EvidenceRef] = Field(default_factory=list)
    created_at: str = Field(default_factory=_utcnow_iso)


# ── Conflicts ───────────────────────────────────────────────────


class ConflictResolution(str, Enum):
    """How a conflict was resolved."""

    DETERMINISTIC_WINS = "deterministic_wins"   # deterministic evidence overrode the claim
    UNRESOLVED = "unresolved"                    # no deterministic evidence — flagged only
    MANUAL = "manual"


class EvidenceConflict(BaseModel):
    """A detected contradiction between evidence sources."""

    conflict_id: str = Field(default_factory=lambda: f"CF-{new_id().upper()[:8]}")
    run_id: str = Field(description="Owning run")
    description: str = Field(description="What contradicts what", max_length=CLAIM_MAX_LEN)
    claim_evidence: EvidenceRef = Field(description="The agent claim")
    deterministic_evidence: Optional[EvidenceRef] = Field(default=None)
    resolution: ConflictResolution = Field(default=ConflictResolution.UNRESOLVED)
    created_at: str = Field(default_factory=_utcnow_iso)


# ── Shared Run Context ──────────────────────────────────────────


class SharedRunContext(BaseModel):
    """Authoritative per-run collaboration summary.

    Stores structured information and references — never giant
    prompt strings. Bounded to prevent unbounded growth.
    """

    run_id: str = Field(description="Owning run")
    task: str = Field(default="", max_length=500)

    requirements_ref: Optional[str] = Field(default=None)
    plan_ref: Optional[str] = Field(default=None)

    repository_evidence: List[EvidenceRef] = Field(default_factory=list)
    graph_evidence: List[EvidenceRef] = Field(default_factory=list)

    agent_handoffs: List[AgentHandoff] = Field(default_factory=list)
    decisions: List[RunDecision] = Field(default_factory=list)
    conflicts: List[EvidenceConflict] = Field(default_factory=list)

    changed_files: List[str] = Field(default_factory=list)
    changed_symbols: List[str] = Field(default_factory=list)

    test_evidence: List[EvidenceRef] = Field(default_factory=list)
    repair_evidence: List[EvidenceRef] = Field(default_factory=list)
    review_evidence: List[EvidenceRef] = Field(default_factory=list)

    warnings: List[str] = Field(default_factory=list, max_length=10)
    version: int = Field(default=1, description="Optimistic-concurrency version")

    def to_summary(self) -> Dict[str, Any]:
        """Compact summary for APIs / CLI (no hidden reasoning)."""
        return {
            "run_id": self.run_id,
            "task": self.task[:200],
            "handoffs": len(self.agent_handoffs),
            "decisions": len(self.decisions),
            "conflicts": len(self.conflicts),
            "changed_files": len(self.changed_files),
            "changed_symbols": len(self.changed_symbols),
            "warnings": self.warnings[:5],
            "version": self.version,
        }
