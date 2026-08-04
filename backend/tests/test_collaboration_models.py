"""
Phase 15 — Tests for the multi-agent collaboration models.

Verifies AgentHandoff / EvidenceRef / RunDecision / EvidenceConflict /
SharedRunContext structure, bounds, and serialization. No DB required.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.collaboration import (
    MAX_EVIDENCE_PER_HANDOFF,
    SUMMARY_MAX_LEN,
    AgentHandoff,
    ConflictResolution,
    DecisionType,
    EvidenceConflict,
    EvidenceRef,
    EvidenceType,
    HandoffStatus,
    RunDecision,
    SharedRunContext,
)


class TestEvidenceRef:
    def test_defaults(self):
        ref = EvidenceRef(type=EvidenceType.TEST_RESULT, reference="passed")
        assert ref.evidence_id
        assert ref.confidence == 0.5
        assert ref.type == EvidenceType.TEST_RESULT
        assert ref.reference == "passed"

    def test_provenance_passthrough(self):
        ref = EvidenceRef(
            type=EvidenceType.GRAPH_RELATIONSHIP,
            reference="sym::id",
            provenance={"source": "graph", "distance": 1},
        )
        assert ref.provenance["source"] == "graph"


class TestAgentHandoff:
    def test_defaults(self):
        h = AgentHandoff(run_id="RUN-1", from_agent="planner", to_agent="coding", stage="planning")
        assert h.handoff_id.startswith("HO-")
        assert h.status == HandoffStatus.UNVERIFIED
        assert h.decisions == []
        assert h.evidence_refs == []

    def test_summary_bounded(self):
        # Pydantic v2 enforces max_length by rejecting oversized input
        with pytest.raises(ValidationError):
            AgentHandoff(
                run_id="RUN-1", from_agent="a", to_agent="b", stage="s",
                summary="x" * 1000,
            )

    def test_summary_at_limit_ok(self):
        h = AgentHandoff(
            run_id="RUN-1", from_agent="a", to_agent="b", stage="s",
            summary="x" * SUMMARY_MAX_LEN,
        )
        assert len(h.summary) == SUMMARY_MAX_LEN

    def test_evidence_bounded(self):
        # The model rejects evidence lists exceeding the per-handoff cap
        refs = [EvidenceRef(type=EvidenceType.PLAN, reference=str(i)) for i in range(30)]
        with pytest.raises(ValidationError):
            AgentHandoff(run_id="R", from_agent="a", to_agent="b", stage="s", evidence_refs=refs)

    def test_evidence_at_limit_ok(self):
        refs = [EvidenceRef(type=EvidenceType.PLAN, reference=str(i)) for i in range(MAX_EVIDENCE_PER_HANDOFF)]
        h = AgentHandoff(run_id="R", from_agent="a", to_agent="b", stage="s", evidence_refs=refs)
        assert len(h.evidence_refs) == MAX_EVIDENCE_PER_HANDOFF


class TestRunDecision:
    def test_decision(self):
        d = RunDecision(
            run_id="RUN-1",
            decision_type=DecisionType.IMPLEMENTATION,
            statement="Use existing TokenManager path",
            made_by="coding",
        )
        assert d.decision_id.startswith("DEC-")
        assert d.decision_type == DecisionType.IMPLEMENTATION


class TestEvidenceConflict:
    def test_conflict_resolution(self):
        c = EvidenceConflict(
            run_id="RUN-1",
            description="Coding claimed pass but tests failed",
            claim_evidence=EvidenceRef(type=EvidenceType.AGENT_CLAIM, reference="HO-1"),
            deterministic_evidence=EvidenceRef(type=EvidenceType.TEST_RESULT, reference="failed"),
            resolution=ConflictResolution.DETERMINISTIC_WINS,
        )
        assert c.resolution == ConflictResolution.DETERMINISTIC_WINS
        assert c.conflict_id.startswith("CF-")


class TestSharedRunContext:
    def test_builds_and_summary(self):
        ctx = SharedRunContext(run_id="RUN-1", task="Add auth")
        ctx.agent_handoffs = [
            AgentHandoff(run_id="RUN-1", from_agent="planner", to_agent="coding", stage="planning")
        ]
        summary = ctx.to_summary()
        assert summary["run_id"] == "RUN-1"
        assert summary["handoffs"] == 1
        assert summary["conflicts"] == 0

    def test_version_increments(self):
        ctx = SharedRunContext(run_id="R")
        assert ctx.version >= 1
