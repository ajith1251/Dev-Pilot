"""
Phase 21 — Tests for Run Replay & Deterministic Reproduction.

Covers:
- manifest building (stage classification, decisions, hashes, fingerprint)
- EXACT replay (MATCH / DRIFT / INVALID, no LLM calls)
- DETERMINISTIC replay (workspace fingerprint, application outcome, tests)
- COMPARE mode (identical → MATCH, divergent → DRIFT, stage-set → INCOMPLETE)
- deterministic re-execution checks (patch, gate, consensus, contradictions)
- API endpoints (manifest / replay / compare / audit)
- CLI commands (registered + runnable)
- orchestrator capture integration (REPLAY_MANIFEST_CAPTURED event)

Architecture invariant asserted throughout: replay never calls an LLM —
LLMs PROPOSE, deterministic systems DECIDE.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.models.replay import (
    ReplayCheckStatus,
    ReplayMode,
    ReplayStageKind,
    ReplayVerdict,
)
from app.services.replay_service import ReplayService


@pytest.fixture(autouse=True)
def _no_live_db(monkeypatch):
    """Force in-memory persistence for replay unit tests.

    A live TEST_DATABASE_URL in the environment would otherwise make the
    collaboration/reasoning/replay services connect to PostgreSQL. These
    unit tests exercise deterministic in-memory behavior only.
    """

    def _raise():
        raise RuntimeError("DB unavailable for replay unit tests")

    monkeypatch.setattr(
        "app.services.replay_service.create_session_factory", _raise
    )
    monkeypatch.setattr(
        "app.services.collaboration_service.create_session_factory", _raise
    )
    monkeypatch.setattr(
        "app.services.reasoning_service.create_session_factory", _raise
    )


# ── Run builders ─────────────────────────────────────────────────


def _make_plan():
    from app.models.issues import ImplementationPlan, ImplementationStep

    return ImplementationPlan(
        summary="Add health endpoint",
        objective="Expose /health returning ok",
        steps=[
            ImplementationStep(
                id="STEP-1",
                title="Add health endpoint",
                description="Return ok",
                affected_areas=["src"],
            )
        ],
        requirements_coverage={"REQ-001": ["STEP-1"]},
    )


def _make_patch():
    from app.models.coding import FileChange, FileOperation, PatchSet

    return PatchSet(
        patch_id="PATCH-REPLAY",
        changes=[
            FileChange(
                change_id="CH-1",
                operation=FileOperation.MODIFY,
                path="src/app.py",
                original_hash="deadbeef",
                new_content="def app():\n    return 'ok'\n",
            )
        ],
    )


def _make_patch_result():
    from app.models.coding import PatchApplicationResult, PatchStatus

    return PatchApplicationResult(
        patch_id="PATCH-REPLAY",
        status=PatchStatus.APPLIED,
        changes_applied=1,
        changed_symbols=["src/app.py::app"],
    )


def _make_test_result(status="passed"):
    from app.models.testing import ExecutionStatus, TestRunResult

    if status == "passed":
        return TestRunResult(
            run_id="r", workspace_id="w", status=ExecutionStatus.PASSED,
            commands_total=1, commands_passed=1, tests_total=1,
            tests_passed=1, tests_failed=0, failures=[], process_results=[],
            summary="1 passed",
        )
    return TestRunResult(
        run_id="r", workspace_id="w", status=ExecutionStatus.FAILED,
        commands_total=1, commands_failed=1, tests_total=1,
        tests_passed=0, tests_failed=1, failures=[], process_results=[],
        summary="1 failed",
    )


def _make_review_report():
    from app.models.review import (
        RequirementCoverage,
        RequirementStatus,
        ReviewReport,
        SecuritySummary,
        TestSummary,
    )

    return ReviewReport(
        review_id="REV-REPLAY",
        requirement_coverage=[
            RequirementCoverage(
                requirement_id="REQ-001",
                requirement_description="Health endpoint exists",
                status=RequirementStatus.SATISFIED,
            )
        ],
        test_summary=TestSummary(
            executed=True, status="passed", tests_passed=1, tests_failed=0,
        ),
        security_summary=SecuritySummary(passed=True),
        findings=[],
    )


def _compute_gate(run):
    """Deterministically compute the quality gate decision (same pipeline the
    orchestrator uses) so recorded == recomputed by construction."""
    from app.models.review import ReviewInput
    from app.services.deterministic_review import DeterministicReview
    from app.services.quality_gate import QualityGate

    inp = ReviewInput(
        workspace_id=run.run_id,
        requirements=run.requirements,
        implementation_plan=run.plan,
        original_patch=run.patch_set,
        repair_result=run.repair_result,
        test_result=run.test_result,
        changed_files=[c.path for c in run.patch_set.changes] if run.patch_set else [],
    )
    det_result = DeterministicReview().run(inp)
    return QualityGate().decide(
        report=run.review_report,
        deterministic_result=det_result,
        test_result=run.test_result,
    )


def make_full_run(run_id="RUN-REPLAY", test_status="passed"):
    """A complete run with every stage recorded (no repository on disk)."""
    from app.models.orchestration import (
        DevPilotRun,
        RunSource,
        RunSourceType,
        StageResult,
        StageStatus,
        StageType,
    )

    run = DevPilotRun(
        run_id=run_id,
        source=RunSource(
            source_type=RunSourceType.USER_TASK,
            title="Add health endpoint",
        ),
        repository_path="",
    )
    run.plan = _make_plan()
    run.patch_set = _make_patch()
    run.patch_result = _make_patch_result()
    run.test_result = _make_test_result(test_status)
    run.review_report = _make_review_report()
    run.quality_gate_result = _compute_gate(run)

    order = [
        StageType.ACQUIRING_REPOSITORY,
        StageType.ANALYZING_REPOSITORY,
        StageType.ANALYZING_TASK,
        StageType.PLANNING,
        StageType.RETRIEVING_CONTEXT,
        StageType.CODING,
        StageType.VALIDATING_PATCH,
        StageType.APPLYING_PATCH,
        StageType.TESTING,
        StageType.REVIEWING,
        StageType.QUALITY_GATE,
    ]
    for stage in order:
        run.stage_results.append(StageResult(
            stage=stage, status=StageStatus.SUCCEEDED,
        ))
    return run


def make_bare_run(run_id="RUN-BARE"):
    """A run with no stage outputs (skipped stages only)."""
    from app.models.orchestration import (
        DevPilotRun,
        RunSource,
        RunSourceType,
        StageResult,
        StageStatus,
        StageType,
    )

    run = DevPilotRun(
        run_id=run_id,
        source=RunSource(
            source_type=RunSourceType.USER_TASK,
            title="No-repo run",
        ),
        repository_path="",
    )
    run.stage_results.append(StageResult(
        stage=StageType.ACQUIRING_REPOSITORY, status=StageStatus.SKIPPED,
    ))
    run.stage_results.append(StageResult(
        stage=StageType.ANALYZING_REPOSITORY, status=StageStatus.SKIPPED,
    ))
    return run


async def _in_memory_store(runs):
    from app.services.run_store import InMemoryRunStore

    store = InMemoryRunStore()
    for r in runs:
        await store.create(r)
    return store


# ── Manifest building ───────────────────────────────────────────


class TestManifestBuilding:
    @pytest.mark.asyncio
    async def test_build_manifest_classifies_stages(self):
        run = make_full_run()
        svc = ReplayService(run_store=await _in_memory_store([run]))
        manifest = await svc.build_manifest(run)

        kinds = {s.stage: s.kind for s in manifest.stages}
        assert kinds["validating_patch"] == ReplayStageKind.DETERMINISTIC
        assert kinds["applying_patch"] == ReplayStageKind.DETERMINISTIC
        assert kinds["testing"] == ReplayStageKind.DETERMINISTIC
        assert kinds["quality_gate"] == ReplayStageKind.DETERMINISTIC
        assert kinds["planning"] == ReplayStageKind.LLM_PROPOSED
        assert kinds["coding"] == ReplayStageKind.LLM_PROPOSED
        assert kinds["analyzing_repository"] == ReplayStageKind.OBSERVATIONAL

    @pytest.mark.asyncio
    async def test_build_manifest_collects_decisions(self):
        run = make_full_run()
        svc = ReplayService(run_store=await _in_memory_store([run]))
        manifest = await svc.build_manifest(run)

        types = [d.decision_type for d in manifest.deterministic_decisions]
        assert "quality_gate" in types
        assert "patch_validation" in types
        assert "testing" in types
        gate = next(d for d in manifest.deterministic_decisions
                    if d.decision_type == "quality_gate")
        assert gate.value == "approved"
        assert gate.replayable is True

    @pytest.mark.asyncio
    async def test_content_hash_stable_and_tamper_sensitive(self):
        run = make_full_run()
        svc = ReplayService(run_store=await _in_memory_store([run]))
        m1 = await svc.build_manifest(run)
        m2 = await svc.build_manifest(run)
        assert m1.content_hash() == m2.content_hash()

        run.quality_gate_result.decision = "rejected"  # tamper
        m3 = await svc.build_manifest(run)
        assert m1.content_hash() != m3.content_hash()

    @pytest.mark.asyncio
    async def test_repository_fingerprint_captured(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "a.py").write_text("x = 1", encoding="utf-8")
        (repo / "b.py").write_text("y = 2", encoding="utf-8")

        run = make_bare_run()
        run.repository_path = str(repo)
        svc = ReplayService(run_store=await _in_memory_store([run]))
        manifest = await svc.build_manifest(run)
        assert manifest.repository_state.file_count == 2
        assert manifest.repository_state.fingerprint

        # Adding a file changes the fingerprint (tamper detection).
        (repo / "c.py").write_text("z = 3", encoding="utf-8")
        manifest2 = await svc.build_manifest(run)
        assert manifest2.repository_state.fingerprint != manifest.repository_state.fingerprint


# ── EXACT replay ────────────────────────────────────────────────


class TestExactReplay:
    @pytest.mark.asyncio
    async def test_exact_match_on_intact_run(self):
        run = make_full_run()
        svc = ReplayService(run_store=await _in_memory_store([run]))
        result = await svc.replay(run.run_id, ReplayMode.EXACT)

        assert result.verdict == ReplayVerdict.MATCH
        assert result.mode == ReplayMode.EXACT
        checks = {c.check: c.status for c in result.checks}
        assert checks.get("quality_gate") == ReplayCheckStatus.PASSED
        assert checks.get("patch_structure") == ReplayCheckStatus.PASSED
        assert checks.get("pipeline_sequence") == ReplayCheckStatus.PASSED

    @pytest.mark.asyncio
    async def test_exact_drift_on_tampered_gate(self):
        run = make_full_run()
        run.quality_gate_result.decision = "rejected"  # recorded no longer matches
        svc = ReplayService(run_store=await _in_memory_store([run]))
        result = await svc.replay(run.run_id, ReplayMode.EXACT)

        assert result.verdict == ReplayVerdict.DRIFT
        gate_check = next(c for c in result.checks if c.check == "quality_gate")
        assert gate_check.status == ReplayCheckStatus.FAILED
        assert any("quality_gate" in d for d in result.divergences)

    @pytest.mark.asyncio
    async def test_exact_invalid_on_missing_run(self):
        svc = ReplayService(run_store=await _in_memory_store([]))
        result = await svc.replay("RUN-MISSING", ReplayMode.EXACT)
        assert result.verdict == ReplayVerdict.INVALID

    @pytest.mark.asyncio
    async def test_exact_never_calls_llm(self):
        """Replay must not invoke any provider — LLMs PROPOSE, replay decides."""
        run = make_full_run()
        svc = ReplayService(run_store=await _in_memory_store([run]))

        with patch(
            "app.services.replay_service.ReplayService.get_run",
            new=AsyncMock(return_value=run),
        ), patch(
            "app.llm.router.ProviderRouter.chat",
            new=AsyncMock(side_effect=AssertionError("LLM called during replay")),
        ):
            result = await svc.replay(run.run_id, ReplayMode.EXACT)
        assert result.verdict in (ReplayVerdict.MATCH, ReplayVerdict.INCOMPLETE)

    @pytest.mark.asyncio
    async def test_exact_match_on_bare_run(self):
        run = make_bare_run()
        svc = ReplayService(run_store=await _in_memory_store([run]))
        result = await svc.replay(run.run_id, ReplayMode.EXACT)
        assert result.verdict == ReplayVerdict.MATCH


# ── DETERMINISTIC replay ────────────────────────────────────────


class TestDeterministicReplay:
    @pytest.mark.asyncio
    async def test_fingerprint_match_and_drift(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "src").mkdir()
        (repo / "src" / "app.py").write_text(
            "def app():\n    return 'ok'\n", encoding="utf-8"
        )

        run = make_full_run()
        run.repository_path = str(repo)
        svc = ReplayService(run_store=await _in_memory_store([run]))

        # Matching workspace → fingerprint check passes.
        result = await svc.replay(
            run.run_id, ReplayMode.DETERMINISTIC, workspace=str(repo),
        )
        fp_check = next(
            c for c in result.checks if c.check == "repository_fingerprint"
        )
        assert fp_check.status == ReplayCheckStatus.PASSED

        # A different workspace → DRIFT (different code base).
        other = tmp_path / "other"
        other.mkdir()
        (other / "src").mkdir()
        (other / "src" / "app.py").write_text("different", encoding="utf-8")
        result2 = await svc.replay(
            run.run_id, ReplayMode.DETERMINISTIC, workspace=str(other),
        )
        fp_check2 = next(
            c for c in result2.checks if c.check == "repository_fingerprint"
        )
        assert fp_check2.status == ReplayCheckStatus.FAILED
        assert result2.verdict == ReplayVerdict.DRIFT

    @pytest.mark.asyncio
    async def test_application_outcome_reproduced(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "src").mkdir()
        # Workspace already reflects the recorded patch (post-application).
        (repo / "src" / "app.py").write_text(
            "def app():\n    return 'ok'\n", encoding="utf-8"
        )

        run = make_full_run()
        run.repository_path = str(repo)
        svc = ReplayService(run_store=await _in_memory_store([run]))
        result = await svc.replay(
            run.run_id, ReplayMode.DETERMINISTIC, workspace=str(repo),
        )
        app_check = next(
            c for c in result.checks if c.check == "application_outcome"
        )
        assert app_check.status == ReplayCheckStatus.PASSED

    @pytest.mark.asyncio
    async def test_application_outcome_drift_on_foreign_content(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "src").mkdir()
        (repo / "src" / "app.py").write_text(
            "def app(): return 'HACKED'", encoding="utf-8"
        )

        run = make_full_run()
        run.repository_path = str(repo)
        svc = ReplayService(run_store=await _in_memory_store([run]))
        result = await svc.replay(
            run.run_id, ReplayMode.DETERMINISTIC, workspace=str(repo),
        )
        app_check = next(
            c for c in result.checks if c.check == "application_outcome"
        )
        assert app_check.status == ReplayCheckStatus.FAILED

    @pytest.mark.asyncio
    async def test_no_workspace_marks_testing_not_replayable(self):
        run = make_full_run()
        svc = ReplayService(run_store=await _in_memory_store([run]))
        result = await svc.replay(
            run.run_id, ReplayMode.DETERMINISTIC, workspace=None,
        )
        # No workspace → testing/fingerprint/application cannot be re-executed,
        # which classifies the replay INCOMPLETE (not MATCH).
        assert result.verdict == ReplayVerdict.INCOMPLETE
        assert any(
            c.status == ReplayCheckStatus.NOT_REPLAYABLE for c in result.checks
        )

    @pytest.mark.asyncio
    async def test_test_re_execution_matches(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "src").mkdir()
        (repo / "src" / "app.py").write_text(
            "def app():\n    return 'ok'\n", encoding="utf-8"
        )
        # A genuinely passing pytest so re-execution reproduces the outcome.
        (repo / "test_app.py").write_text(
            "def test_ok():\n    assert True\n", encoding="utf-8"
        )

        from app.models.testing import (
            ExecutionStatus,
            ProcessExecutionResult,
            TestRunResult,
        )

        run = make_full_run()
        run.repository_path = str(repo)
        run.test_result = TestRunResult(
            run_id="r", workspace_id="w", status=ExecutionStatus.PASSED,
            commands_total=1, commands_passed=1, tests_total=1,
            tests_passed=1, tests_failed=0, failures=[], summary="1 passed",
            process_results=[ProcessExecutionResult(
                step_id="S1", command="python -m pytest -q",
                category="test", status=ExecutionStatus.PASSED, exit_code=0,
            )],
        )
        svc = ReplayService(run_store=await _in_memory_store([run]))
        result = await svc.replay(
            run.run_id, ReplayMode.DETERMINISTIC, workspace=str(repo),
        )
        testing_check = next(c for c in result.checks if c.check == "testing")
        assert testing_check.status == ReplayCheckStatus.PASSED


# ── COMPARE replay ──────────────────────────────────────────────


class TestCompareReplay:
    @pytest.mark.asyncio
    async def test_compare_identical_runs_match(self):
        run_a = make_full_run("RUN-A")
        run_b = make_full_run("RUN-B")
        store = await _in_memory_store([run_a, run_b])
        svc = ReplayService(run_store=store)
        result = await svc.replay(
            run_a.run_id, ReplayMode.COMPARE, other_run_id=run_b.run_id,
        )
        assert result.verdict == ReplayVerdict.MATCH
        assert all(c.matched is not False for c in result.stage_comparisons)

    @pytest.mark.asyncio
    async def test_compare_divergent_runs_drift(self):
        run_a = make_full_run("RUN-A")
        run_b = make_full_run("RUN-B")
        run_b.quality_gate_result.decision = "rejected"
        store = await _in_memory_store([run_a, run_b])
        svc = ReplayService(run_store=store)
        result = await svc.replay(
            run_a.run_id, ReplayMode.COMPARE, other_run_id=run_b.run_id,
        )
        assert result.verdict == ReplayVerdict.DRIFT
        assert any(c.matched is False for c in result.stage_comparisons)
        # The diverging decision is named.
        assert any("quality_gate" in d or "decision" in d for d in result.divergences)

    @pytest.mark.asyncio
    async def test_compare_requires_second_run(self):
        run = make_full_run()
        svc = ReplayService(run_store=await _in_memory_store([run]))
        result = await svc.replay(run.run_id, ReplayMode.COMPARE)
        assert result.verdict == ReplayVerdict.INVALID

    @pytest.mark.asyncio
    async def test_compare_different_stage_sets_incomplete(self):
        run_a = make_full_run("RUN-A")
        run_b = make_bare_run("RUN-B")
        store = await _in_memory_store([run_a, run_b])
        svc = ReplayService(run_store=store)
        result = await svc.replay(
            run_a.run_id, ReplayMode.COMPARE, other_run_id=run_b.run_id,
        )
        assert result.verdict == ReplayVerdict.INCOMPLETE


# ── Capture + orchestrator integration ──────────────────────────


class TestCaptureIntegration:
    @pytest.mark.asyncio
    async def test_capture_persists_manifest(self):
        run = make_full_run()
        svc = ReplayService(run_store=await _in_memory_store([run]))
        manifest = await svc.capture(run)
        assert manifest.run_id == run.run_id
        assert manifest.source_run_status == run.status.value
        fetched = await svc.get_manifest(run.run_id)
        assert fetched is not None
        assert fetched.manifest_id == manifest.manifest_id

    @pytest.mark.asyncio
    async def test_capture_never_raises(self):
        run = make_full_run()
        svc = ReplayService(run_store=await _in_memory_store([run]))
        with patch.object(
            svc, "build_manifest",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            manifest = await svc.capture(run)
        assert manifest.source_run_status == "capture_failed"

    @pytest.mark.asyncio
    async def test_orchestrator_capture_emits_event(self):
        from app.models.orchestration import EventType, StageType
        from app.services.orchestration_service import OrchestrationService

        run = make_full_run()
        store = await _in_memory_store([run])
        orch = OrchestrationService(run_store=store)
        await orch._capture_replay_manifest(run)
        assert any(
            e.event_type == EventType.REPLAY_MANIFEST_CAPTURED for e in run.events
        )


# ── Audit ───────────────────────────────────────────────────────


class TestAudit:
    @pytest.mark.asyncio
    async def test_audit_report(self):
        run = make_full_run()
        svc = ReplayService(run_store=await _in_memory_store([run]))
        audit = await svc.audit(run.run_id)
        assert audit["available"] is True
        assert audit["verdict"] == ReplayVerdict.MATCH.value
        assert "manifest" in audit
        assert "checks" in audit
        assert any(
            d["matched"] is True for d in audit["deterministic_decisions"]
        )

    @pytest.mark.asyncio
    async def test_audit_missing_run(self):
        svc = ReplayService(run_store=await _in_memory_store([]))
        audit = await svc.audit("RUN-NOPE")
        assert audit["available"] is False


# ── API ─────────────────────────────────────────────────────────


class TestReplayAPI:
    @pytest.mark.asyncio
    async def test_manifest_endpoint(self):
        from fastapi.testclient import TestClient

        from app.main import app

        run = make_full_run("RUN-API-MANIFEST")
        store = await _in_memory_store([run])
        svc = ReplayService(run_store=store)

        with patch(
            "app.api.v1.replay._get_service", return_value=svc,
        ):
            with TestClient(app) as client:
                res = client.get("/api/v1/runs/RUN-API-MANIFEST/replay/manifest")
        assert res.status_code == 200
        body = res.json()
        assert body["success"] is True
        assert body["data"]["exists"] is True
        assert body["data"]["stage_count"] == len(run.stage_results)

    @pytest.mark.asyncio
    async def test_replay_endpoint(self):
        from fastapi.testclient import TestClient

        from app.main import app

        run = make_full_run("RUN-API-REPLAY")
        svc = ReplayService(run_store=await _in_memory_store([run]))

        with patch(
            "app.api.v1.replay._get_service", return_value=svc,
        ):
            with TestClient(app) as client:
                res = client.post(
                    "/api/v1/runs/RUN-API-REPLAY/replay",
                    json={"mode": "exact"},
                )
        assert res.status_code == 200
        body = res.json()
        assert body["success"] is True
        assert body["data"]["verdict"] == "match"
        assert "checks" in body["data"]

    @pytest.mark.asyncio
    async def test_replay_endpoint_bad_mode(self):
        from fastapi.testclient import TestClient

        from app.main import app

        run = make_full_run("RUN-API-BADMODE")
        svc = ReplayService(run_store=await _in_memory_store([run]))

        with patch(
            "app.api.v1.replay._get_service", return_value=svc,
        ):
            with TestClient(app) as client:
                res = client.post(
                    "/api/v1/runs/RUN-API-BADMODE/replay",
                    json={"mode": "teleport"},
                )
        assert res.status_code == 200
        body = res.json()
        assert body["success"] is False
        assert body["error"] == "InvalidMode"

    @pytest.mark.asyncio
    async def test_compare_endpoint(self):
        from fastapi.testclient import TestClient

        from app.main import app

        run_a = make_full_run("RUN-A-API")
        run_b = make_full_run("RUN-B-API")
        svc = ReplayService(run_store=await _in_memory_store([run_a, run_b]))

        with patch(
            "app.api.v1.replay._get_service", return_value=svc,
        ):
            with TestClient(app) as client:
                res = client.get(
                    "/api/v1/runs/RUN-A-API/replay/compare/RUN-B-API"
                )
        assert res.status_code == 200
        body = res.json()
        assert body["success"] is True
        assert body["data"]["verdict"] == "match"

    @pytest.mark.asyncio
    async def test_audit_endpoint(self):
        from fastapi.testclient import TestClient

        from app.main import app

        run = make_full_run("RUN-AUDIT-API")
        svc = ReplayService(run_store=await _in_memory_store([run]))

        with patch(
            "app.api.v1.replay._get_service", return_value=svc,
        ):
            with TestClient(app) as client:
                res = client.get("/api/v1/runs/RUN-AUDIT-API/replay/audit")
        assert res.status_code == 200
        body = res.json()
        assert body["success"] is True
        assert body["data"]["verdict"] == "match"

    @pytest.mark.asyncio
    async def test_manifest_endpoint_missing_run(self):
        from fastapi.testclient import TestClient

        from app.main import app

        svc = ReplayService(run_store=await _in_memory_store([]))

        with patch(
            "app.api.v1.replay._get_service", return_value=svc,
        ):
            with TestClient(app) as client:
                res = client.get("/api/v1/runs/RUN-NOPE/replay/manifest")
        assert res.status_code == 200
        body = res.json()
        assert body["success"] is False
        assert body["error"] == "NotFound"


# ── CLI ─────────────────────────────────────────────────────────


class TestReplayCLI:
    def test_cli_commands_registered(self):
        import argparse

        from app.cli_replay import add_cli_commands

        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        add_cli_commands(subparsers)
        for cmd, args_list in [
            ("replay-manifest", ["RUN-1"]),
            ("replay", ["RUN-1", "--mode", "exact"]),
            ("replay-compare", ["RUN-1", "RUN-2"]),
            ("replay-audit", ["RUN-1"]),
            ("replays", ["RUN-1"]),
        ]:
            args = parser.parse_args([cmd] + args_list)
            assert args.command == cmd

    def test_verdict_exit_codes(self):
        from app.cli_replay import _verdict_exit_code

        assert _verdict_exit_code("match") == 0
        assert _verdict_exit_code("drift") == 1
        assert _verdict_exit_code("incomplete") == 1
        assert _verdict_exit_code("invalid") == 2

    @pytest.mark.asyncio
    async def test_run_replay_json_output(self, capsys):
        from app.cli_replay import run_replay

        run = make_full_run("RUN-CLI-REPLAY")
        svc = ReplayService(run_store=await _in_memory_store([run]))

        with patch(
            "app.services.replay_service.ReplayService",
            return_value=svc,
        ) as _:
            exit_code = await run_replay(
                "RUN-CLI-REPLAY", "exact", None, json_output=True
            )
        captured = capsys.readouterr().out
        payload = json.loads(captured)
        assert payload["verdict"] == "match"
        assert payload["run_id"] == "RUN-CLI-REPLAY"
        # MATCH must exit 0 (CI gate passes on identical reproduction).
        assert exit_code == 0

    @pytest.mark.asyncio
    async def test_run_replay_drift_exit_code(self, capsys):
        from app.cli_replay import run_replay

        run = make_full_run("RUN-CLI-DRIFT")
        # Unknown run → INVALID verdict → exit code 2.
        svc = ReplayService(run_store=await _in_memory_store([run]))

        with patch(
            "app.services.replay_service.ReplayService",
            return_value=svc,
        ) as _:
            exit_code = await run_replay(
                "RUN-DOES-NOT-EXIST", "exact", None, json_output=True
            )
        assert exit_code == 2

    @pytest.mark.asyncio
    async def test_run_replay_manifest_json(self, capsys):
        from app.cli_replay import run_replay_manifest

        run = make_full_run("RUN-CLI-MANIFEST")
        svc = ReplayService(run_store=await _in_memory_store([run]))

        with patch(
            "app.services.replay_service.ReplayService",
            return_value=svc,
        ) as _:
            await run_replay_manifest("RUN-CLI-MANIFEST", json_output=True)
        captured = capsys.readouterr().out
        payload = json.loads(captured)
        assert payload["run_id"] == "RUN-CLI-MANIFEST"
        assert payload["stages"]
