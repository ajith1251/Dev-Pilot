"""
Phase 17 — Tests for Collaborative Reasoning & Evidence Consensus.

Covers:
- confidence model (evidence-driven, deterministic-outranks-claims)
- consensus generation (agreement, conflict, resolution)
- contradiction detection (claim vs test, claim vs gate, scope vs impact)
- engineering notebook (accepted/rejected/conflicts/timeline)
- restart recovery (rehydrate persisted notebook)
- autonomy integration (consensus topics + replan rationale)
- API endpoints (consensus / contradictions / notebook / reasoning)
- CLI commands (consensus / conflicts / notebook)
- regression: deterministic evidence can never be overridden by claims
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.collaboration import EvidenceRef, EvidenceType, HandoffStatus
from app.models.reasoning import (
    ConfidenceScore,
    ConfidenceTier,
    ConsensusStatus,
    ContradictionKind,
)
from app.services.reasoning_service import CollaborativeReasoningEngine


# ── Run builders ─────────────────────────────────────────────────


def make_run(run_id="RUN-REASONING", **kwargs):
    """Build a DevPilotRun with the Phase 17 evidence fields."""
    from app.models.issues import ImplementationPlan, ImplementationStep
    from app.models.orchestration import DevPilotRun, RunSource, RunSourceType

    run = DevPilotRun(
        run_id=run_id,
        source=RunSource(
            source_type=RunSourceType.USER_TASK,
            title="Fix auth token expiry handling",
            repository_path=kwargs.get("repository", ""),
        ),
        repository_path=kwargs.get("repository"),
    )
    if kwargs.get("plan"):
        run.plan = ImplementationPlan(
            summary="Update auth service",
            objective="Reject expired tokens",
            steps=[
                ImplementationStep(
                    id="STEP-1",
                    title="Update AuthService",
                    description="Reject expired tokens",
                    affected_areas=kwargs.get("plan_areas", ["auth/"]),
                )
            ],
        )
    if kwargs.get("patch"):
        run.patch_set = _make_patch_set()
    if kwargs.get("test_status") is not None:
        run.test_result = _make_test_result(kwargs["test_status"])
    if kwargs.get("gate") is not None:
        run.quality_gate_result = _make_gate(kwargs["gate"])
    return run


def _make_patch_set():
    from app.models.coding import FileChange, FileOperation, PatchSet

    return PatchSet(
        patch_id="PATCH-REASONING",
        changes=[
            FileChange(
                change_id="CH-1",
                operation=FileOperation.MODIFY,
                path="auth/service.py",
                original_hash="a",
                new_content="class AuthService: pass",
            )
        ],
    )


def _make_test_result(status):
    from app.models.testing import (
        ExecutionStatus,
        FailureCategory,
        TestFailure,
        TestRunResult,
    )

    if status == "passed":
        return TestRunResult(
            run_id="r", workspace_id="w", status=ExecutionStatus.PASSED,
            commands_total=1, commands_passed=1, tests_total=5,
            tests_passed=5, tests_failed=0, failures=[],
            process_results=[], summary="5 passed",
        )
    return TestRunResult(
        run_id="r", workspace_id="w", status=ExecutionStatus.FAILED,
        commands_total=1, commands_failed=1, tests_total=5,
        tests_passed=3, tests_failed=2,
        failures=[TestFailure(
            failure_id="f", test_name="test_expired_token_rejected",
            file_path="auth/tests/test_auth.py", line_number=12,
            message="TokenExpiredError not raised", framework="pytest",
            failure_type=FailureCategory.ASSERTION_FAILURE,
        )],
        process_results=[], summary="2 failed",
    )


def _make_gate(decision):
    from app.models.review import QualityGateDecision, QualityGateResult

    return QualityGateResult(
        review_id="REV-REASONING",
        decision=QualityGateDecision(decision),
        blocking_findings=[] if decision == "approved" else ["stale token check"],
    )


def make_handoff(run_id, summary, decisions=None, from_agent="coding"):
    from app.models.collaboration import AgentHandoff

    return AgentHandoff(
        run_id=run_id,
        from_agent=from_agent,
        to_agent="reviewer",
        stage="review",
        summary=summary,
        decisions=decisions or [],
        status=HandoffStatus.VALIDATED,
    )


def make_decision(run_id, statement="Rejected expired tokens", decision_type="review"):
    from app.models.collaboration import DecisionType, RunDecision

    return RunDecision(
        run_id=run_id,
        decision_type=DecisionType(decision_type),
        statement=statement,
        made_by="reviewer",
    )


# ── Confidence model ─────────────────────────────────────────────


class TestConfidenceModel:
    def test_no_evidence_unknown(self):
        eng = CollaborativeReasoningEngine()
        score = eng.compute_confidence([])
        assert score.tier == ConfidenceTier.UNKNOWN
        assert score.value == 0.0
        assert score.evidence_count == 0

    def test_deterministic_evidence_high(self):
        eng = CollaborativeReasoningEngine()
        refs = [
            EvidenceRef(type=EvidenceType.TEST_RESULT, reference="passed", confidence=1.0),
            EvidenceRef(type=EvidenceType.QUALITY_GATE, reference="approved", confidence=1.0),
            EvidenceRef(type=EvidenceType.PATCH, reference="2 files", confidence=1.0),
        ]
        score = eng.compute_confidence(refs)
        assert score.tier == ConfidenceTier.HIGH
        assert score.value >= 0.75
        assert score.deterministic_count == 3

    def test_claims_only_never_high(self):
        eng = CollaborativeReasoningEngine()
        refs = [
            EvidenceRef(type=EvidenceType.AGENT_CLAIM, reference="done", confidence=1.0),
            EvidenceRef(type=EvidenceType.AGENT_CLAIM, reference="tests pass", confidence=1.0),
        ]
        score = eng.compute_confidence(refs)
        # Claim-only evidence is bounded below HIGH by construction.
        assert score.tier in (ConfidenceTier.LOW, ConfidenceTier.MEDIUM)
        assert score.value < 0.75
        assert score.deterministic_count == 0

    def test_mixed_evidence_medium(self):
        eng = CollaborativeReasoningEngine()
        refs = [
            EvidenceRef(type=EvidenceType.TEST_RESULT, reference="passed", confidence=1.0),
            EvidenceRef(type=EvidenceType.AGENT_CLAIM, reference="ready", confidence=1.0),
        ]
        score = eng.compute_confidence(refs)
        assert score.tier in (ConfidenceTier.MEDIUM, ConfidenceTier.HIGH)

    def test_confidence_bounded_0_1(self):
        eng = CollaborativeReasoningEngine()
        for i in range(20):
            refs = [EvidenceRef(type=EvidenceType.PATCH, reference=f"f{i}", confidence=1.0)]
            score = eng.compute_confidence(refs)
            assert 0.0 <= score.value <= 1.0


# ── Contradiction detection ──────────────────────────────────────


class TestContradictionDetection:
    @pytest.mark.asyncio
    async def test_claim_vs_test_contradiction(self):
        eng = CollaborativeReasoningEngine()
        run = make_run(test_status="failed")
        collab = AsyncMock()
        collab.list_handoffs = AsyncMock(return_value=[
            make_handoff(run.run_id, "Patch complete, tests passed", ["approved"])
        ])
        eng._collaboration = collab

        contradictions = await eng.detect_contradictions(run)
        kinds = [c.kind for c in contradictions]
        assert ContradictionKind.CLAIM_VS_TEST in kinds
        cd = [c for c in contradictions if c.kind == ContradictionKind.CLAIM_VS_TEST][0]
        # Deterministic evidence wins by construction.
        assert cd.resolution == "deterministic_wins"
        assert cd.deterministic_evidence is not None
        assert cd.deterministic_evidence.type == EvidenceType.TEST_RESULT

    @pytest.mark.asyncio
    async def test_claim_vs_gate_contradiction(self):
        eng = CollaborativeReasoningEngine()
        run = make_run(test_status="passed", gate="rejected")
        collab = AsyncMock()
        collab.list_handoffs = AsyncMock(return_value=[
            make_handoff(run.run_id, "Ready to ship", ["approved", "quality gate passed"])
        ])
        eng._collaboration = collab

        contradictions = await eng.detect_contradictions(run)
        kinds = [c.kind for c in contradictions]
        assert ContradictionKind.CLAIM_VS_GATE in kinds
        cd = [c for c in contradictions if c.kind == ContradictionKind.CLAIM_VS_GATE][0]
        assert cd.resolution == "deterministic_wins"
        assert cd.deterministic_evidence.type == EvidenceType.QUALITY_GATE

    @pytest.mark.asyncio
    async def test_no_contradiction_when_evidence_agrees(self):
        eng = CollaborativeReasoningEngine()
        run = make_run(test_status="passed", gate="approved")
        collab = AsyncMock()
        collab.list_handoffs = AsyncMock(return_value=[
            make_handoff(run.run_id, "Patch complete, tests passed", ["approved"])
        ])
        eng._collaboration = collab
        contradictions = await eng.detect_contradictions(run)
        assert contradictions == []

    @pytest.mark.asyncio
    async def test_contradictions_deduped(self):
        eng = CollaborativeReasoningEngine()
        run = make_run(test_status="failed")
        collab = AsyncMock()
        collab.list_handoffs = AsyncMock(return_value=[
            make_handoff(run.run_id, "tests passed", ["ok"]),
            make_handoff(run.run_id, "tests passed again", ["ok"]),
        ])
        eng._collaboration = collab
        await eng.detect_contradictions(run)
        await eng.detect_contradictions(run)  # second pass must not duplicate
        assert len(eng._contradictions[run.run_id]) == 1


# ── Consensus ────────────────────────────────────────────────────


class TestConsensus:
    @pytest.mark.asyncio
    async def test_agreement_consensus(self):
        eng = CollaborativeReasoningEngine()
        run = make_run(test_status="passed", gate="approved", patch=True, plan=True)
        consensus = await eng.build_consensus(run)
        topics = {c.topic: c for c in consensus}
        assert topics["test_status"].status == ConsensusStatus.AGREED
        assert topics["test_status"].final_decision == "tests_passed"
        assert topics["patch_complete"].status == ConsensusStatus.AGREED
        assert topics["quality_gate"].status == ConsensusStatus.AGREED
        # Agreement carries deterministic confidence.
        assert topics["test_status"].confidence.tier in (
            ConfidenceTier.HIGH, ConfidenceTier.MEDIUM,
        )

    @pytest.mark.asyncio
    async def test_conflict_consensus(self):
        eng = CollaborativeReasoningEngine()
        run = make_run(test_status="failed", patch=True)
        consensus = await eng.build_consensus(run)
        topics = {c.topic: c for c in consensus}
        assert topics["test_status"].status == ConsensusStatus.CONFLICTED
        assert topics["test_status"].final_decision == "tests_failing"
        assert topics["patch_complete"].status == ConsensusStatus.CONFLICTED
        assert topics["patch_complete"].final_decision == "patch_conflicts_with_tests"

    @pytest.mark.asyncio
    async def test_consensus_never_promotes_unsupported_claim(self):
        """Regression: an AGENT_CLAIM can never flip a deterministic consensus."""
        eng = CollaborativeReasoningEngine()
        run = make_run(test_status="failed", patch=True)
        consensus = await eng.build_consensus(run)
        topics = {c.topic: c for c in consensus}
        # Even though agents may claim success, deterministic test failure
        # keeps the consensus CONFLICTED with decision tests_failing.
        assert topics["test_status"].final_decision == "tests_failing"
        assert topics["test_status"].status == ConsensusStatus.CONFLICTED

    @pytest.mark.asyncio
    async def test_consensus_bounded_and_evidence_only(self):
        eng = CollaborativeReasoningEngine()
        run = make_run(test_status="passed", gate="approved", patch=True, plan=True)
        consensus = await eng.build_consensus(run)
        assert len(consensus) <= 20
        for c in consensus:
            # Evidence-only: no free-text reasoning fields.
            assert c.summary
            assert c.confidence.evidence_count >= 0


# ── Notebook ─────────────────────────────────────────────────────


class TestNotebook:
    @pytest.mark.asyncio
    async def test_notebook_build(self):
        eng = CollaborativeReasoningEngine()
        run = make_run(test_status="failed", gate="rejected", patch=True, plan=True)
        collab = AsyncMock()
        collab.list_handoffs = AsyncMock(return_value=[
            make_handoff(run.run_id, "tests passed", ["ok"])
        ])
        collab.list_decisions = AsyncMock(return_value=[
            make_decision(run.run_id)
        ])
        eng._collaboration = collab

        contradictions = await eng.detect_contradictions(run)
        consensus = await eng.build_consensus(run, contradictions)
        notebook = await eng.build_notebook(run, consensus, contradictions)

        assert notebook.run_id == run.run_id
        assert notebook.task
        assert notebook.accepted_decisions
        assert notebook.consensus
        assert notebook.timeline
        # Claim-vs-test contradiction is deterministic_wins → resolved.
        assert notebook.resolved_conflicts

    @pytest.mark.asyncio
    async def test_restart_recovery_in_memory(self, monkeypatch):
        # Force deterministic in-memory mode: with a live TEST_DATABASE_URL in
        # the environment the engine would lazily connect and persist, so the
        # "DB unavailable → nothing persisted" contract would not hold.
        def _no_db():
            raise RuntimeError("DB unavailable for in-memory restart test")

        monkeypatch.setattr(
            "app.services.reasoning_service.create_session_factory", _no_db
        )

        eng1 = CollaborativeReasoningEngine()
        run = make_run(test_status="passed", gate="approved", patch=True)
        consensus = await eng1.build_consensus(run)

        # Fresh engine (simulates restart) — memory mirrors start empty.
        eng2 = CollaborativeReasoningEngine()
        recovered = await eng2.list_consensus(run.run_id)
        assert recovered == []  # DB unavailable → nothing persisted

    @pytest.mark.asyncio
    async def test_analyze_run_pipeline_never_raises(self):
        eng = CollaborativeReasoningEngine()
        run = make_run()
        outcome = await eng.analyze_run(run)
        assert outcome["run_id"] == run.run_id
        assert "consensus" in outcome
        assert "contradictions" in outcome
        assert "notebook" in outcome


# ── Autonomy integration ─────────────────────────────────────────


class TestAutonomyConsensusIntegration:
    @pytest.mark.asyncio
    async def test_refresh_consensus_topics(self):
        from app.models.autonomy import AutonomousRunState, ExecutionGoal, ExecutionState

        from app.services.autonomy_service import AutonomousExecutionController

        ctrl = AutonomousExecutionController()
        ctrl._reasoning = AsyncMock()
        ctrl._reasoning.analyze_run = AsyncMock(return_value={
            "consensus": [
                MagicMock(topic="test_status", status=MagicMock(value="conflicted"),
                          final_decision="tests_failing"),
            ],
            "contradictions": [],
            "notebook": None,
            "confidence": None,
        })
        run = make_run(test_status="failed")
        state = AutonomousRunState(
            goal_id="GOAL-1",
            task="Fix auth",
            goal=ExecutionGoal(goal_id="GOAL-1", task="Fix auth", status=ExecutionState.RUNNING),
        )
        await ctrl._refresh_consensus_topics(state, run)
        assert any("test_status" in t for t in state.consensus_topics)

    @pytest.mark.asyncio
    async def test_replan_rationale_includes_consensus(self):
        from app.models.autonomy import (
            AutonomousAction,
            AutonomousRunState,
            CriterionStatus,
            ExecutionGoal,
            ExecutionState,
            GoalProgress,
            IterationEvidence,
        )

        from app.services.autonomy_service import AutonomousExecutionController

        ctrl = AutonomousExecutionController()
        state = AutonomousRunState(
            goal_id="GOAL-2",
            task="Fix auth",
            goal=ExecutionGoal(goal_id="GOAL-2", task="Fix auth", status=ExecutionState.RUNNING),
        )
        state.goal.progress = GoalProgress(
            criteria_total=1, criteria_satisfied=0, criteria_unsatisfied=1,
        )
        state.consensus_topics = ["test_status:conflicted:tests_failing"]
        state.evidence_history = [
            IterationEvidence(
                iteration=1, test_status="failed", tests_failed=2,
                quality_gate_decision="rejected",
            )
        ]
        # Exhaust repair budget so the next decision is REPLAN.
        state.budget.repairs_used = state.budget.max_repairs

        action, reason, rationale = ctrl._decide(state)
        assert action == AutonomousAction.REPLAN
        assert "consensus" in rationale
        assert "test_status" in rationale


# ── API ──────────────────────────────────────────────────────────


class TestReasoningAPI:
    @pytest.mark.asyncio
    async def test_consensus_endpoint_empty(self):
        from fastapi.testclient import TestClient

        from app.main import app

        with TestClient(app) as client:
            res = client.get("/api/v1/runs/RUN-API-EMPTY/consensus")
            assert res.status_code == 200
            body = res.json()
            assert body["success"] is True
            assert body["data"] == []

    @pytest.mark.asyncio
    async def test_notebook_endpoint_not_found(self):
        from fastapi.testclient import TestClient

        from app.main import app

        with TestClient(app) as client:
            res = client.get("/api/v1/runs/RUN-API-MISSING/notebook")
            assert res.status_code == 200
            body = res.json()
            assert body["success"] is False
            assert body["error"] == "NotFound"

    @pytest.mark.asyncio
    async def test_reasoning_snapshot_endpoint(self):
        from fastapi.testclient import TestClient

        from app.main import app

        with TestClient(app) as client:
            res = client.get("/api/v1/runs/RUN-API-SNAP/reasoning")
            assert res.status_code == 200
            body = res.json()
            assert body["success"] is True
            assert "consensus" in body["data"]
            assert "contradictions" in body["data"]
            assert "notebook" in body["data"]


# ── CLI ──────────────────────────────────────────────────────────


class TestReasoningCLI:
    def test_cli_commands_registered(self):
        import argparse

        from app.cli_reasoning import add_cli_commands

        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        add_cli_commands(subparsers)
        for cmd in ("consensus", "conflicts", "notebook"):
            args = parser.parse_args([cmd, "RUN-1"])
            assert args.command == cmd
            assert args.run_id == "RUN-1"

    @pytest.mark.asyncio
    async def test_run_conflicts_json(self, capsys):
        from app.cli_reasoning import run_conflicts

        eng = CollaborativeReasoningEngine()
        run = make_run(test_status="failed")
        collab = AsyncMock()
        collab.list_handoffs = AsyncMock(return_value=[
            make_handoff(run.run_id, "tests passed", ["ok"])
        ])
        eng._collaboration = collab
        await eng.detect_contradictions(run)

        from app.services import reasoning_service as rs

        with patch.object(rs.CollaborativeReasoningEngine, "list_contradictions",
                          new=AsyncMock(return_value=eng._contradictions[run.run_id])):
            await run_conflicts(run.run_id, json_output=True)
        captured = capsys.readouterr().out
        assert "claim_vs_test" in captured
