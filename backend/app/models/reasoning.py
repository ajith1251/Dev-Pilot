"""
Phase 17 — Collaborative Reasoning & Evidence Consensus models.

A reasoning layer ABOVE the Phase 15 collaboration store:

    CollaborationService       = records WHAT agents produced/shared
    CollaborativeReasoningEngine = decides whether the evidence AGREES

These models define durable, bounded reasoning records:

- ConfidenceTier         — bounded evidence-driven confidence bands
- EvidenceConsensus      — a consensus record over one engineering topic
- ContradictionRecord    — a detected contradiction (wider than Phase 15's
                           claim-vs-test conflicts: also scope-vs-impact)
- NotebookEntry          — one engineering-timeline entry
- EngineeringNotebook    — the shared engineering notebook for a run

Security invariant (unchanged since Phase 13-16): these records expose ONLY
evidence, confidence, decisions and consensus — never chain-of-thought,
hidden prompts, or internal reasoning. Deterministic evidence always
outranks agent claims; consensus can never promote an unsupported claim.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.models.base import new_id
from app.models.collaboration import EvidenceRef, EvidenceType


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Bounds (Safety & Performance) ───────────────────────────────
MAX_CONSENSUS_PER_RUN = 50          # hard cap on consensus records per run
MAX_CONTRADICTIONS_PER_RUN = 50     # hard cap on contradiction records
MAX_NOTEBOOK_ENTRIES = 200          # bounded engineering timeline
MAX_EVIDENCE_PER_CONSENSUS = 20     # supporting/conflicting refs each
SUMMARY_MAX_LEN = 500
CLAIM_MAX_LEN = 300


class ConfidenceTier(str, Enum):
    """Bounded, evidence-driven confidence bands (§4)."""

    HIGH = "high"        # deterministic evidence confirms (tests + gate + graph)
    MEDIUM = "medium"    # partial deterministic confirmation
    LOW = "low"          # mostly claims, little deterministic evidence
    UNKNOWN = "unknown"  # no evidence available


class ConsensusStatus(str, Enum):
    """State of a consensus record."""

    AGREED = "agreed"            # all agents + deterministic evidence agree
    CONFLICTED = "conflicted"    # contradiction detected, not yet resolved
    RESOLVED = "resolved"        # contradiction resolved deterministically
    DISAGREED = "disagreed"      # claims disagree, no deterministic tiebreak
    UNKNOWN = "unknown"


class ContradictionKind(str, Enum):
    """Category of a detected contradiction."""

    CLAIM_VS_TEST = "claim_vs_test"             # coding says done, tests fail
    SCOPE_VS_IMPACT = "scope_vs_impact"         # plan scope vs graph impact
    PLAN_VS_REQUIREMENTS = "plan_vs_requirements"
    CLAIM_VS_GATE = "claim_vs_gate"             # agent claim vs quality gate
    MEMORY_VS_EVIDENCE = "memory_vs_evidence"   # memory claim vs current run
    UNKNOWN = "unknown"


# ── Confidence Model (§4) ───────────────────────────────────────
# Bands are derived from the weighted evidence authority (see service).
# HIGH requires deterministic evidence (test/gate/patch); MEDIUM has at
# least one deterministic ref; LOW is claim-dominated.

HIGH_CONFIDENCE_MIN = 0.75
MEDIUM_CONFIDENCE_MIN = 0.5


class ConfidenceScore(BaseModel):
    """Bounded confidence for an engineering conclusion (§4)."""

    value: float = Field(default=0.0, ge=0.0, le=1.0)
    tier: ConfidenceTier = Field(default=ConfidenceTier.UNKNOWN)
    evidence_count: int = Field(default=0, ge=0)
    deterministic_count: int = Field(default=0, ge=0)
    claim_count: int = Field(default=0, ge=0)
    basis: str = Field(
        default="",
        max_length=CLAIM_MAX_LEN,
        description="Short human summary of why this confidence (no CoT)",
    )

    def summary(self) -> Dict[str, Any]:
        return {
            "value": round(self.value, 2),
            "tier": self.tier.value,
            "evidence_count": self.evidence_count,
            "deterministic_count": self.deterministic_count,
            "claim_count": self.claim_count,
            "basis": self.basis[:200],
        }


# ── Consensus ───────────────────────────────────────────────────


class EvidenceConsensus(BaseModel):
    """A consensus record over one engineering topic (§2)."""

    consensus_id: str = Field(default_factory=lambda: f"CS-{new_id().upper()[:8]}")
    run_id: str = Field(description="Owning run")
    topic: str = Field(
        description="Engineering topic (e.g. 'test_status', 'patch_complete', "
                    "'scope_compliance', 'quality_gate')",
        max_length=100,
    )
    summary: str = Field(default="", max_length=SUMMARY_MAX_LEN)
    status: ConsensusStatus = Field(default=ConsensusStatus.UNKNOWN)
    confidence: ConfidenceScore = Field(default_factory=ConfidenceScore)
    supporting_evidence: List[EvidenceRef] = Field(
        default_factory=list, max_length=MAX_EVIDENCE_PER_CONSENSUS
    )
    conflicting_evidence: List[EvidenceRef] = Field(
        default_factory=list, max_length=MAX_EVIDENCE_PER_CONSENSUS
    )
    final_decision: str = Field(default="", max_length=CLAIM_MAX_LEN)
    contributing_agents: List[str] = Field(default_factory=list, max_length=20)
    created_at: str = Field(default_factory=_utcnow_iso)

    def summary(self) -> Dict[str, Any]:
        return {
            "consensus_id": self.consensus_id,
            "topic": self.topic,
            "summary": self.summary[:200],
            "status": self.status.value,
            "confidence": self.confidence.summary(),
            "supporting_evidence": [
                {
                    "type": e.type.value,
                    "reference": e.reference[:100],
                    "confidence": round(float(e.confidence), 2),
                }
                for e in self.supporting_evidence[:10]
            ],
            "conflicting_evidence": [
                {
                    "type": e.type.value,
                    "reference": e.reference[:100],
                    "confidence": round(float(e.confidence), 2),
                }
                for e in self.conflicting_evidence[:10]
            ],
            "final_decision": self.final_decision[:200],
            "contributing_agents": self.contributing_agents[:10],
            "created_at": self.created_at,
        }


# ── Contradictions (§3) ─────────────────────────────────────────


class ContradictionRecord(BaseModel):
    """A detected contradiction between evidence sources (§3).

    Deterministic evidence ALWAYS wins: a contradiction between an agent
    claim and deterministic evidence is resolved as deterministic_wins.
    """

    contradiction_id: str = Field(default_factory=lambda: f"CD-{new_id().upper()[:8]}")
    run_id: str = Field(description="Owning run")
    kind: ContradictionKind = Field(default=ContradictionKind.UNKNOWN)
    description: str = Field(description="What contradicts what", max_length=CLAIM_MAX_LEN)
    claim_evidence: EvidenceRef = Field(description="The claim side")
    deterministic_evidence: Optional[EvidenceRef] = Field(default=None)
    resolution: str = Field(
        default="unresolved",
        description="unresolved | deterministic_wins | claim_rejected | manual",
    )
    created_at: str = Field(default_factory=_utcnow_iso)

    def summary(self) -> Dict[str, Any]:
        return {
            "contradiction_id": self.contradiction_id,
            "kind": self.kind.value,
            "description": self.description[:200],
            "claim_evidence": {
                "type": self.claim_evidence.type.value,
                "reference": self.claim_evidence.reference[:100],
                "detail": self.claim_evidence.detail[:100],
            },
            "deterministic_evidence": (
                {
                    "type": self.deterministic_evidence.type.value,
                    "reference": self.deterministic_evidence.reference[:100],
                    "detail": self.deterministic_evidence.detail[:100],
                }
                if self.deterministic_evidence else None
            ),
            "resolution": self.resolution,
            "created_at": self.created_at,
        }


# ── Engineering Notebook (§5) ───────────────────────────────────


class NotebookEntryType(str, Enum):
    DECISION = "decision"
    CONSENSUS = "consensus"
    CONTRADICTION = "contradiction"
    RESOLUTION = "resolution"
    TIMELINE = "timeline"


class NotebookEntry(BaseModel):
    """One entry in the shared engineering notebook timeline."""

    entry_id: str = Field(default_factory=lambda: f"NE-{new_id().upper()[:8]}")
    run_id: str = Field(description="Owning run")
    entry_type: NotebookEntryType = Field(default=NotebookEntryType.TIMELINE)
    label: str = Field(default="", max_length=100)
    detail: str = Field(default="", max_length=CLAIM_MAX_LEN)
    evidence_refs: List[EvidenceRef] = Field(default_factory=list, max_length=10)
    created_at: str = Field(default_factory=_utcnow_iso)

    def summary(self) -> Dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "entry_type": self.entry_type.value,
            "label": self.label[:100],
            "detail": self.detail[:200],
            "evidence_refs": [
                {
                    "type": e.type.value,
                    "reference": e.reference[:100],
                }
                for e in self.evidence_refs[:5]
            ],
            "created_at": self.created_at,
        }


class EngineeringNotebook(BaseModel):
    """Structured run notebook containing the shared reasoning (§5)."""

    notebook_id: str = Field(default_factory=lambda: f"NB-{new_id().upper()[:8]}")
    run_id: str = Field(description="Owning run")
    task: str = Field(default="", max_length=SUMMARY_MAX_LEN)

    accepted_decisions: List[Dict[str, Any]] = Field(default_factory=list, max_length=50)
    rejected_decisions: List[Dict[str, Any]] = Field(default_factory=list, max_length=50)
    conflicts: List[ContradictionRecord] = Field(
        default_factory=list, max_length=MAX_CONTRADICTIONS_PER_RUN
    )
    resolved_conflicts: List[ContradictionRecord] = Field(
        default_factory=list, max_length=MAX_CONTRADICTIONS_PER_RUN
    )
    consensus: List[EvidenceConsensus] = Field(
        default_factory=list, max_length=MAX_CONSENSUS_PER_RUN
    )
    timeline: List[NotebookEntry] = Field(
        default_factory=list, max_length=MAX_NOTEBOOK_ENTRIES
    )

    version: int = Field(default=1, description="Optimistic-concurrency version")
    created_at: str = Field(default_factory=_utcnow_iso)
    updated_at: str = Field(default_factory=_utcnow_iso)

    def summary(self) -> Dict[str, Any]:
        return {
            "notebook_id": self.notebook_id,
            "run_id": self.run_id,
            "task": self.task[:200],
            "accepted_decisions": len(self.accepted_decisions),
            "rejected_decisions": len(self.rejected_decisions),
            "conflicts": len(self.conflicts),
            "resolved_conflicts": len(self.resolved_conflicts),
            "consensus": len(self.consensus),
            "timeline_entries": len(self.timeline),
            "version": self.version,
            "updated_at": self.updated_at,
        }


# Re-export EvidenceType for convenience (agents may reference it directly).
__all__ = [
    "ConfidenceTier",
    "ConsensusStatus",
    "ContradictionKind",
    "NotebookEntryType",
    "ConfidenceScore",
    "EvidenceConsensus",
    "ContradictionRecord",
    "NotebookEntry",
    "EngineeringNotebook",
    "EvidenceType",
]
