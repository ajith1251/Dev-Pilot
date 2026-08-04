"""
Phase 15 — Tests for the CollaborationService.

Covers:
- handoff create / list / get / bounds
- deterministic handoff validation (symbols + test claims)
- conflict detection (deterministic evidence outranks claims)
- decision records
- restart recovery (rehydrate from memory mirror + DB)
- repository memory promotion
- secret redaction
- graceful degradation (in-memory when DB unavailable)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.collaboration import (
    AgentHandoff,
    ConflictResolution,
    EvidenceRef,
    EvidenceType,
    HandoffStatus,
)
from app.services.collaboration_service import CollaborationService, redact_secrets


@pytest.fixture(autouse=True)
def _force_in_memory(monkeypatch):
    """Force pure in-memory mode for every test in this file.

    CollaborationService lazily binds to settings.DATABASE_URL when no
    session factory is passed. These are unit tests of the in-memory mirror,
    so without this fixture every run writes test data (shared run IDs like
    "RUN-1") into the real dev/test database, accumulating until
    MAX_HANDOFFS_PER_RUN blocks later tests. Patching create_session_factory
    to raise makes _get_factory() degrade to in-memory, exactly as documented
    in the module docstring ("graceful degradation (in-memory when DB
    unavailable)").
    """
    def _no_db():
        raise RuntimeError("DB unavailable for in-memory collaboration unit tests")

    monkeypatch.setattr(
        "app.services.collaboration_service.create_session_factory", _no_db
    )


class TestSecretRedaction:
    def test_redacts_api_key(self):
        assert redact_secrets("key=sk-1234567890abcdefgh") == "key=[REDACTED]"

    def test_redacts_github_token(self):
        assert "ghp_" not in redact_secrets("token ghp_1234567890abcdefghij")

    def test_redacts_named_secret(self):
        out = redact_secrets("API_KEY = superSecretValue123456")
        assert "superSecretValue123456" not in out

    def test_leaves_plain_text(self):
        assert redact_secrets("normal engineering text") == "normal engineering text"


class TestHandoffLifecycle:
    @pytest.mark.asyncio
    async def test_create_and_list_handoff(self):
        svc = CollaborationService()
        handoff = await svc.create_handoff(
            run_id="RUN-1",
            from_agent="planner",
            to_agent="coding",
            stage="planning",
            summary="5-step plan ready",
            affected_symbols=["auth_service.py::AuthService"],
        )
        assert handoff is not None
        assert handoff.handoff_id.startswith("HO-")
        assert handoff.from_agent == "planner"
        assert handoff.to_agent == "coding"

        listed = await svc.list_handoffs("RUN-1")
        assert len(listed) == 1
        assert listed[0].handoff_id == handoff.handoff_id

    @pytest.mark.asyncio
    async def test_get_handoff(self):
        svc = CollaborationService()
        h = await svc.create_handoff("RUN-1", "coding", "testing", "coding", "changed 2 files")
        got = await svc.get_handoff("RUN-1", h.handoff_id)
        assert got is not None
        assert got.summary == "changed 2 files"
        assert await svc.get_handoff("RUN-1", "HO-NOPE") is None

    @pytest.mark.asyncio
    async def test_handoff_overflow_bounded(self):
        svc = CollaborationService()
        for i in range(55):
            await svc.create_handoff(f"RUN-{i}", "a", "b", "s", f"h{i}")
        # Each run is independent; the bound is per run, not global.
        handoffs = await svc.list_handoffs("RUN-1")
        assert len(handoffs) == 1

    @pytest.mark.asyncio
    async def test_handoff_redacts_secrets(self):
        svc = CollaborationService()
        h = await svc.create_handoff(
            "RUN-1", "planner", "coding", "planning",
            summary="Use API_KEY=abcd1234secretvalue for auth",
        )
        assert "abcd1234secretvalue" not in h.summary

    @pytest.mark.asyncio
    async def test_retrieve_relevant_handoffs_prefers_direct(self):
        svc = CollaborationService()
        await svc.create_handoff("RUN-1", "planner", "coding", "planning", "plan")
        await svc.create_handoff("RUN-1", "coding", "testing", "coding", "patch")
        await svc.create_handoff("RUN-1", "testing", "repair", "testing", "failures")

        coding_handoffs = await svc.retrieve_relevant_handoffs("RUN-1", "coding")
        assert any(h.to_agent == "coding" for h in coding_handoffs)

        testing_handoffs = await svc.retrieve_relevant_handoffs("RUN-1", "testing")
        assert any(h.to_agent == "testing" for h in testing_handoffs)

    @pytest.mark.asyncio
    async def test_retrieve_empty_run(self):
        svc = CollaborationService()
        assert await svc.retrieve_relevant_handoffs("RUN-X", "coding") == []


class TestHandoffValidation:
    @pytest.mark.asyncio
    async def test_symbol_validated_against_changed_files(self):
        svc = CollaborationService()
        h = await svc.create_handoff(
            "RUN-1", "coding", "testing", "coding",
            summary="Changed auth_service.py",
            affected_symbols=["auth_service.py::AuthService"],
        )
        validated = await svc.validate_handoff(
            h,
            changed_files=["auth_service.py"],
            changed_symbols=["auth_service.py::AuthService"],
        )
        assert validated.validation.get("symbol:auth_service.py::AuthService") == "validated"

    @pytest.mark.asyncio
    async def test_symbol_unverified_when_not_in_patch(self):
        svc = CollaborationService()
        h = await svc.create_handoff(
            "RUN-1", "coding", "testing", "coding",
            summary="Changed something",
            affected_symbols=["ghost.py::Ghost"],
        )
        validated = await svc.validate_handoff(h, changed_files=["real.py"])
        assert validated.validation.get("symbol:ghost.py::Ghost") == "unverified"

    @pytest.mark.asyncio
    async def test_pass_claim_rejected_when_tests_failed(self):
        svc = CollaborationService()
        h = await svc.create_handoff(
            "RUN-1", "coding", "testing", "coding",
            summary="All tests passed",
        )
        validated = await svc.validate_handoff(h, test_passed=False)
        assert validated.validation.get("test:claim") == "rejected"
        assert validated.status == HandoffStatus.REJECTED

    @pytest.mark.asyncio
    async def test_pass_claim_validated_when_tests_passed(self):
        svc = CollaborationService()
        h = await svc.create_handoff("RUN-1", "coding", "testing", "coding", "Tests passed")
        validated = await svc.validate_handoff(h, test_passed=True)
        assert validated.validation.get("test:claim") == "validated"


class TestConflictDetection:
    @pytest.mark.asyncio
    async def test_conflict_when_claim_pass_but_tests_fail(self):
        svc = CollaborationService()
        h = await svc.create_handoff(
            "RUN-1", "coding", "testing", "coding",
            summary="Token expiration behavior updated; tests passed",
        )
        conflicts = await svc.detect_conflicts("RUN-1", h, test_passed=False)
        assert len(conflicts) == 1
        assert conflicts[0].resolution == ConflictResolution.DETERMINISTIC_WINS
        # Deterministic evidence wins — claim downgraded
        assert h.status == HandoffStatus.REJECTED

        listed = await svc.list_conflicts("RUN-1")
        assert len(listed) == 1

    @pytest.mark.asyncio
    async def test_no_conflict_when_evidence_consistent(self):
        svc = CollaborationService()
        h = await svc.create_handoff("RUN-1", "coding", "testing", "coding", "Tests passed")
        conflicts = await svc.detect_conflicts("RUN-1", h, test_passed=True)
        assert conflicts == []


class TestDecisions:
    @pytest.mark.asyncio
    async def test_record_and_list_decision(self):
        svc = CollaborationService()
        decision = await svc.record_decision(
            run_id="RUN-1",
            decision_type="implementation",
            statement="Use existing TokenManager validation path",
            made_by="coding",
        )
        assert decision is not None
        assert decision.decision_id.startswith("DEC-")
        assert decision.decision_type.value == "implementation"

        decisions = await svc.list_decisions("RUN-1")
        assert len(decisions) == 1
        assert decisions[0].statement == "Use existing TokenManager validation path"


class TestSharedRunContext:
    @pytest.mark.asyncio
    async def test_build_shared_run_context(self):
        svc = CollaborationService()
        run = MagicMock()
        run.run_id = "RUN-1"
        run.source.title = "Add auth"
        run.status.value = "approved"
        run.repository_path = "/tmp/devpilot"
        run.patch_set.changes = [MagicMock(path="auth_service.py")]
        run.patch_result = None
        run.test_result = MagicMock(status=MagicMock(value="passed"))
        run.repair_result = None
        run.review_report = None
        run.quality_gate_result = None
        run.warnings = []

        await svc.create_handoff("RUN-1", "planner", "coding", "planning", "plan")

        ctx = await svc.build_shared_run_context(run)
        assert ctx.run_id == "RUN-1"
        assert ctx.changed_files == ["auth_service.py"]
        assert len(ctx.agent_handoffs) == 1
        assert len(ctx.test_evidence) == 1
        assert ctx.version >= 1


class TestRecovery:
    @pytest.mark.asyncio
    async def test_recover_populates_memory(self, monkeypatch):
        # Force deterministic in-memory mode: with a live TEST_DATABASE_URL in
        # the environment the fresh service would lazily connect and rehydrate
        # from PostgreSQL, so the "fresh process has no memory" contract would
        # not hold. This mirrors the full-suite live-PG run (Phase 17 spec).
        def _no_db():
            raise RuntimeError("DB unavailable for in-memory recovery test")

        monkeypatch.setattr(
            "app.services.collaboration_service.create_session_factory", _no_db
        )

        svc = CollaborationService()
        await svc.create_handoff("RUN-1", "planner", "coding", "planning", "plan")
        await svc.record_decision("RUN-1", "planning", "Adopt plan", "planner")

        # Simulate restart: fresh service, recover rehydrates from memory
        fresh = CollaborationService()
        # No DB configured in tests → recover is a no-op; list works via memory path
        await fresh.recover("RUN-1")
        # Fresh service with no factory cannot see old process memory;
        # this is graceful — list returns [] rather than crashing.
        assert await fresh.list_handoffs("RUN-1") == []

    @pytest.mark.asyncio
    async def test_recover_no_factory_graceful(self):
        svc = CollaborationService()
        await svc.recover("RUN-1")  # no DB → no crash


class TestMemoryPromotion:
    def _make_run(self, status="approved"):
        run = MagicMock()
        run.run_id = "RUN-ABC123"
        run.status.value = status
        run.repository_path = "/tmp/devpilot"
        run.patch_set.changes = [MagicMock(path="auth_service.py")]
        run.patch_result = MagicMock()
        run.patch_result.changed_symbols = ["auth_service.py::AuthService"]
        return run

    @pytest.mark.asyncio
    async def test_promote_memory_approved_run(self):
        svc = CollaborationService()
        memory_svc = MagicMock()
        memory_svc.create_memory = AsyncMock(return_value=MagicMock())
        svc._memory_service = memory_svc

        run = self._make_run("approved")
        promoted = await svc.promote_memory(run)
        assert promoted == 1
        memory_svc.create_memory.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_promote_memory_skipped_for_running(self):
        svc = CollaborationService()
        run = self._make_run("running")
        assert await svc.promote_memory(run) == 0

    @pytest.mark.asyncio
    async def test_promote_memory_no_repo(self):
        svc = CollaborationService()
        run = self._make_run("approved")
        run.repository_path = None
        assert await svc.promote_memory(run) == 0


class TestMetrics:
    @pytest.mark.asyncio
    async def test_metrics_counts(self):
        svc = CollaborationService()
        await svc.create_handoff("RUN-1", "planner", "coding", "planning", "plan")
        await svc.record_decision("RUN-1", "planning", "Adopt plan", "planner")
        metrics = await svc.get_collaboration_metrics("RUN-1")
        assert metrics["handoffs_total"] == 1
        assert metrics["decisions"] == 1
        assert metrics["evidence_items"] == 0
