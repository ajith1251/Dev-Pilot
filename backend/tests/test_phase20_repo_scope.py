"""
Phase 20 — Tests for slice A4: per-repository scope enforcement.

Covers:
- RepositoryScope / RepositoryScopeRegistry: registration, path containment,
  per-patch validation, serialization round-trip.
- SafePatchEngine ownership gate: a patch bound to repository A is never
  validated/applied against repository B's checkout; escaped paths rejected.
- DeterministicReview DET-020: per-repo validation results + scope registry
  produce blocking cross-repository findings; clean results stay clean.
- ScopeController: rejected per-repo results surface as repository scope
  violations on autonomous runs.
- OrchestrationService stage wiring: _stage_patch_validation /
  _stage_patch_application validate + apply each repository's patch against
  ITS OWN checkout only; cross-checkout patches fail the run deterministically.
- execute_run end-to-end: a primary patch + a per-repo auxiliary patch both
  apply to their own checkouts, primary is never touched by aux, and
  repo_validation is aggregated on the result.
- API contract: POST /api/v1/runs parses repo_patches and returns 400 on a
  malformed spec.

All tests are deterministic and offline (source=local only).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.autonomy import IterationEvidence, TaskScope
from app.models.coding import (
    FileChange,
    FileOperation,
    PatchSet,
    PatchStatus,
)
from app.models.issues import (
    ImplementationPlan,
    ImplementationStep,
    Requirement,
    StructuredRequirements,
)
from app.models.orchestration import (
    EventType,
    RepositoryPatchInput,
    RepositoryPatchResult,
    RepositorySpec,
    RunSource,
    RunSourceType,
    RunStatus,
    StageType,
)
from app.models.review import ReviewInput
from app.services.autonomy_service import AutonomousExecutionController, ScopeController
from app.services.deterministic_review import DeterministicReview
from app.services.orchestration_service import OrchestrationService
from app.services.repository_scope import RepositoryScope, RepositoryScopeRegistry
from app.services.safe_patch_engine import SafePatchEngine


def _make_repo(tmp_path, rid: str, files: dict) -> str:
    d = tmp_path / rid
    d.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_text(content)
    return str(d)


def _make_reqs() -> StructuredRequirements:
    return StructuredRequirements(
        objective="T", requirements=[Requirement(id="REQ-001", description="T")]
    )


def _make_plan() -> ImplementationPlan:
    return ImplementationPlan(
        summary="T", objective="T",
        steps=[ImplementationStep(id="STEP-001", title="S", description="D",
                                   affected_areas=["s"])],
        test_strategy="T",
    )


def _change(change_id: str, operation: FileOperation, path: str, content: str | None = None) -> FileChange:
    return FileChange(
        change_id=change_id, operation=operation, path=path, new_content=content,
    )


def _patch(patch_id: str, changes, repository_id: str | None = None) -> PatchSet:
    return PatchSet(patch_id=patch_id, changes=changes, repository_id=repository_id)


def _scope(registry: RepositoryScopeRegistry, repo_id: str, checkout: str) -> None:
    registry.register(RepositoryScope(
        repository_id=repo_id,
        namespace=repo_id,
        checkout_root=checkout,
        owned_paths=[checkout],
    ))


# ── RepositoryScopeRegistry (unit) ────────────────────────────────


class TestRepositoryScopeRegistry:
    def test_register_resolve_check_path(self, tmp_path):
        repo = _make_repo(tmp_path, "a", {"x.py": "x"})
        reg = RepositoryScopeRegistry()
        _scope(reg, "repo-a", repo)

        assert reg.resolve("repo-a").repository_id == "repo-a"
        assert reg.check_path("repo-a", "x.py").is_within is True
        assert reg.check_path("repo-a", "../other.py").is_within is False
        assert reg.check_path("repo-unknown", "x.py").is_within is False

    def test_validate_patch_rejects_cross_repository(self, tmp_path):
        a = _make_repo(tmp_path, "a", {"x.py": "x"})
        b = _make_repo(tmp_path, "b", {"y.py": "y"})
        reg = RepositoryScopeRegistry()
        _scope(reg, "repo-a", a)
        _scope(reg, "repo-b", b)

        # Patch claims repo-a but is validated against repo-b → rejected.
        p = _patch("p", [_change("C-1", FileOperation.CREATE, "z.py", "z")], repository_id="repo-a")
        ok, errors, rejected = reg.validate_patch("repo-b", p)
        assert ok is False
        assert any("cross-repository" in e for e in errors)

    def test_validate_patch_rejects_out_of_checkout_path(self, tmp_path):
        a = _make_repo(tmp_path, "a", {"x.py": "x"})
        reg = RepositoryScopeRegistry()
        _scope(reg, "repo-a", a)

        p = _patch("p", [_change("C-1", FileOperation.CREATE, "../escape.py", "e")], repository_id="repo-a")
        ok, errors, rejected = reg.validate_patch("repo-a", p)
        assert ok is False
        assert "../escape.py" in rejected
        assert any("outside repository" in e for e in errors)

    def test_validate_patch_unattributed_allowed(self, tmp_path):
        a = _make_repo(tmp_path, "a", {"x.py": "x"})
        reg = RepositoryScopeRegistry()
        _scope(reg, "repo-a", a)

        p = _patch("p", [_change("C-1", FileOperation.CREATE, "new.py", "n")])
        ok, errors, rejected = reg.validate_patch("repo-a", p)
        assert ok is True
        assert errors == []
        assert rejected == []

    def test_serialization_roundtrip(self, tmp_path):
        a = _make_repo(tmp_path, "a", {"x.py": "x"})
        reg = RepositoryScopeRegistry()
        _scope(reg, "repo-a", a)

        revived = RepositoryScopeRegistry.from_dicts(reg.to_dicts())
        assert revived.resolve("repo-a") is not None
        assert revived.check_path("repo-a", "x.py").is_within is True


# ── SafePatchEngine ownership gate ────────────────────────────────


class TestSafePatchEngineOwnership:
    def _registry(self, tmp_path):
        a = _make_repo(tmp_path, "a", {"x.py": "a"})
        b = _make_repo(tmp_path, "b", {"y.py": "b"})
        reg = RepositoryScopeRegistry()
        _scope(reg, "repo-a", a)
        _scope(reg, "repo-b", b)
        return a, b, reg

    def test_cross_repo_patch_rejected_in_dry_run_and_apply(self, tmp_path):
        a, b, reg = self._registry(tmp_path)
        p = _patch("p", [_change("C-1", FileOperation.CREATE, "z.py", "z")], repository_id="repo-b")
        engine = SafePatchEngine(workspace_root=a, repository_id="repo-a", scope_registry=reg)

        ok, errors = engine.check_repository_ownership(p)
        assert ok is False
        assert any("cross-repository" in e for e in errors)

        dry = engine.dry_run(p)
        assert dry.status == PatchStatus.REJECTED
        applied = engine.apply(p)
        assert applied.status == PatchStatus.REJECTED
        assert not Path(a, "z.py").exists()

    def test_escaped_path_rejected(self, tmp_path):
        a, b, reg = self._registry(tmp_path)
        p = _patch("p", [_change("C-1", FileOperation.CREATE, "../escape.py", "e")], repository_id="repo-a")
        engine = SafePatchEngine(workspace_root=a, repository_id="repo-a", scope_registry=reg)

        ok, errors = engine.check_repository_ownership(p)
        assert ok is False
        assert not Path(tmp_path, "escape.py").exists()

    def test_unattributed_patch_backwards_compatible(self, tmp_path):
        a, b, reg = self._registry(tmp_path)
        p = _patch("p", [_change("C-1", FileOperation.CREATE, "new.py", "n")])
        engine = SafePatchEngine(workspace_root=a, repository_id="repo-a", scope_registry=reg)

        ok, errors = engine.check_repository_ownership(p)
        assert ok is True
        applied = engine.apply(p)
        assert applied.status == PatchStatus.APPLIED
        assert Path(a, "new.py").read_text() == "n"


# ── DeterministicReview DET-020 ───────────────────────────────────


class TestDet020RepositoryScope:
    def _run(self, extra_context: dict, original_patch: PatchSet | None) -> list:
        inp = ReviewInput(
            workspace_id="ws",
            implementation_plan=_make_plan(),
            original_patch=original_patch,
            extra_context=extra_context,
        )
        return DeterministicReview().run(inp).findings

    def test_precomputed_rejected_result_is_blocking(self, tmp_path):
        a = _make_repo(tmp_path, "a", {"x.py": "x"})
        findings = self._run({
            "repository_patch_results": [{
                "repository_id": "repo-b",
                "rejected_paths": ["../escape.py"],
                "validation_status": "rejected",
            }],
            "repository_scopes": [{
                "repository_id": "repo-a", "namespace": "repo-a",
                "checkout_root": a, "owned_paths": [a],
            }],
            "primary_repository_id": "repo-a",
        }, _patch("p", [_change("C-1", FileOperation.CREATE, "x.py", "x")], repository_id="repo-a"))

        det020 = [f for f in findings if f.finding_id == "DET-020"]
        assert len(det020) == 1
        assert det020[0].blocking is True
        assert "repo-b" in det020[0].title

    def test_escaping_primary_patch_is_blocking(self, tmp_path):
        a = _make_repo(tmp_path, "a", {"x.py": "x"})
        findings = self._run({
            "repository_patch_results": [],
            "repository_scopes": [{
                "repository_id": "repo-a", "namespace": "repo-a",
                "checkout_root": a, "owned_paths": [a],
            }],
            "primary_repository_id": "repo-a",
        }, _patch("p", [_change("C-1", FileOperation.CREATE, "../escape.py", "e")], repository_id="repo-a"))

        det020 = [f for f in findings if f.finding_id == "DET-020"]
        assert len(det020) == 1
        assert det020[0].blocking is True
        assert "escaped" in det020[0].title.lower()

    def test_clean_results_no_det020(self, tmp_path):
        a = _make_repo(tmp_path, "a", {"x.py": "x"})
        findings = self._run({
            "repository_patch_results": [{
                "repository_id": "repo-a",
                "rejected_paths": [],
                "validation_status": "validated",
            }],
            "repository_scopes": [{
                "repository_id": "repo-a", "namespace": "repo-a",
                "checkout_root": a, "owned_paths": [a],
            }],
            "primary_repository_id": "repo-a",
        }, _patch("p", [_change("C-1", FileOperation.CREATE, "x.py", "x")], repository_id="repo-a"))

        assert not [f for f in findings if f.finding_id == "DET-020"]


# ── ScopeController repository gate ───────────────────────────────


class TestScopeControllerRepositoryScope:
    @pytest.mark.asyncio
    async def test_rejected_repo_result_is_scope_violation(self):
        ctrl = AutonomousExecutionController()
        state = await ctrl.create_goal(task="Fix cross-repo tokens", scope=TaskScope())
        ctrl._scope_controller.set_repository_scopes(RepositoryScopeRegistry())

        evidence = IterationEvidence(
            iteration=1, run_id="RUN-1", test_status="passed", plan_summary="P",
            plan_objective="O", plan_step_count=1,
            repository_validation=[
                RepositoryPatchResult(
                    repository_id="repo-b", workspace_path="/x",
                    validation_status="rejected", rejected_paths=["../escape.py"],
                    status="rejected",
                )
            ],
        )
        reason = ctrl._scope_controller.check(state, evidence)
        assert reason is not None
        assert "repo-b" in reason

    @pytest.mark.asyncio
    async def test_clean_repo_results_no_violation(self):
        ctrl = AutonomousExecutionController()
        state = await ctrl.create_goal(task="Fix tokens", scope=TaskScope())
        ctrl._scope_controller.set_repository_scopes(RepositoryScopeRegistry())

        evidence = IterationEvidence(
            iteration=1, run_id="RUN-1", test_status="passed", plan_summary="P",
            plan_objective="O", plan_step_count=1,
            repository_validation=[
                RepositoryPatchResult(
                    repository_id="repo-b", workspace_path="/x",
                    validation_status="validated", status="ok",
                )
            ],
        )
        assert ctrl._scope_controller.check(state, evidence) is None


# ── OrchestrationService per-repo stage wiring ────────────────────


class TestOrchestratorPerRepoStages:
    async def _make_run(self, tmp_path, repo_patches=None, repositories=None, primary_files=None):
        primary = _make_repo(tmp_path, "primary", primary_files or {"main.py": "p1\n"})
        orch = OrchestrationService()
        source = RunSource(
            source_type=RunSourceType.USER_TASK,
            title="Multi-repo task",
            repository_path=primary,
            repositories=repositories,
            repo_patches=repo_patches,
        )
        run = await orch.create_run(source)
        # Stage methods call _transition_to directly; pre-advance to the
        # stage preceding VALIDATING_PATCH so the state machine allows it.
        run.current_stage = StageType.CODING
        run.repository_path = primary
        run.requirements = _make_reqs()
        run.plan = _make_plan()
        await orch._store.update(run)
        return orch, run, primary

    @pytest.mark.asyncio
    async def test_validate_aux_patch_against_own_checkout(self, tmp_path):
        aux_b = _make_repo(tmp_path, "repo-b", {"app.py": "b1\n"})
        inp = RepositoryPatchInput(
            repository_id="repo-b", repository_namespace="repo-b",
            workspace_path=aux_b,
            patch=_patch("paux", [_change("C-1", FileOperation.CREATE, "feature.py", "f1\n")], repository_id="repo-b"),
        )
        orch, run, primary = await self._make_run(tmp_path, repo_patches=[inp])

        ok = await orch._stage_patch_validation(run, primary)

        assert ok is True
        assert [r.repository_id for r in run.repo_patches] == ["repo-b"]
        res = run.repo_patches[0]
        assert res.validation_status == "validated"
        assert res.status == "ok"
        assert res.originating_run_id == run.run_id
        event_types = {e.event_type for e in run.events}
        assert EventType.REPOSITORY_PATCH_VALIDATED in event_types
        assert EventType.REPOSITORY_SCOPE_VIOLATION not in event_types

    @pytest.mark.asyncio
    async def test_apply_aux_patch_touches_only_own_checkout(self, tmp_path):
        aux_b = _make_repo(tmp_path, "repo-b", {"app.py": "b1\n"})
        inp = RepositoryPatchInput(
            repository_id="repo-b", repository_namespace="repo-b",
            workspace_path=aux_b,
            patch=_patch("paux", [_change("C-1", FileOperation.CREATE, "feature.py", "f1\n")], repository_id="repo-b"),
        )
        orch, run, primary = await self._make_run(tmp_path, repo_patches=[inp])

        assert await orch._stage_patch_validation(run, primary) is True
        assert await orch._stage_patch_application(run, primary) is True

        res = run.repo_patches[0]
        assert res.application_status == "applied"
        assert res.status == "applied"
        assert res.changes_applied == 1
        assert Path(aux_b, "feature.py").read_text() == "f1\n"
        # The primary checkout was never touched by the auxiliary patch.
        assert not Path(primary, "feature.py").exists()

    @pytest.mark.asyncio
    async def test_cross_repo_patch_rejected_fails_stage(self, tmp_path):
        aux_b = _make_repo(tmp_path, "repo-b", {"app.py": "b1\n"})
        # Patch claims the primary repository but is submitted under repo-b's
        # workspace — cross-repository application must be rejected.
        inp = RepositoryPatchInput(
            repository_id="repo-b", repository_namespace="repo-b",
            workspace_path=aux_b,
            patch=_patch("pbad", [_change("C-1", FileOperation.CREATE, "evil.py", "e")], repository_id="repo-primary"),
        )
        orch, run, primary = await self._make_run(tmp_path, repo_patches=[inp])

        ok = await orch._stage_patch_validation(run, primary)

        assert ok is False
        assert run.failure is not None
        res = run.repo_patches[0]
        assert res.validation_status == "rejected"
        assert any("cross-repository" in e for e in res.validation_errors)
        event_types = {e.event_type for e in run.events}
        assert EventType.REPOSITORY_SCOPE_VIOLATION in event_types
        assert not Path(aux_b, "evil.py").exists()

    @pytest.mark.asyncio
    async def test_out_of_checkout_path_rejected_fails_stage(self, tmp_path):
        aux_b = _make_repo(tmp_path, "repo-b", {"app.py": "b1\n"})
        inp = RepositoryPatchInput(
            repository_id="repo-b", repository_namespace="repo-b",
            workspace_path=aux_b,
            patch=_patch("pesc", [_change("C-1", FileOperation.CREATE, "../escape.py", "e")], repository_id="repo-b"),
        )
        orch, run, primary = await self._make_run(tmp_path, repo_patches=[inp])

        ok = await orch._stage_patch_validation(run, primary)

        assert ok is False
        res = run.repo_patches[0]
        assert res.validation_status == "rejected"
        assert "../escape.py" in res.rejected_paths
        assert not Path(tmp_path, "escape.py").exists()

    @pytest.mark.asyncio
    async def test_repository_review_context_aggregates_results(self, tmp_path):
        aux_b = _make_repo(tmp_path, "repo-b", {"app.py": "b1\n"})
        inp = RepositoryPatchInput(
            repository_id="repo-b", repository_namespace="repo-b",
            workspace_path=aux_b,
            patch=_patch("paux", [_change("C-1", FileOperation.CREATE, "feature.py", "f1\n")], repository_id="repo-b"),
        )
        orch, run, primary = await self._make_run(tmp_path, repo_patches=[inp])
        await orch._stage_patch_validation(run, primary)

        ctx = orch._repository_review_context(run)
        assert ctx["primary_repository_id"] == "repo-primary"
        assert [r["repository_id"] for r in ctx["repository_patch_results"]] == ["repo-b"]
        assert any(d["repository_id"] == "repo-b" for d in ctx["repository_scopes"])


class TestExecuteRunPerRepoPatches:
    async def _prepare(self, orch, source, primary):
        run = await orch.create_run(source)
        # All upstream stages are pre-populated (guards skip them) and every
        # stage method calls _transition_to directly, so current_stage must be
        # pre-advanced to CODING for the real validation transition to be valid.
        run.current_stage = StageType.CODING
        run.repository_profile = MagicMock()
        run.requirements = _make_reqs()
        run.plan = _make_plan()
        run.retrieved_context = MagicMock()
        run.patch_set = PatchSet(patch_id="primary", changes=[
            _change("C-1", FileOperation.MODIFY, "main.py", "p2\n"),
        ])
        await orch._store.update(run)
        return run

    @pytest.mark.asyncio
    async def test_execute_run_applies_per_repo_patches_to_own_checkouts(self, tmp_path):
        primary = _make_repo(tmp_path, "primary", {"main.py": "p1\n"})
        aux_b = _make_repo(tmp_path, "repo-b", {"app.py": "b1\n"})
        inp = RepositoryPatchInput(
            repository_id="repo-b", repository_namespace="repo-b",
            workspace_path=aux_b,
            patch=_patch("paux", [_change("C-1", FileOperation.CREATE, "feature.py", "f1\n")], repository_id="repo-b"),
        )
        source = RunSource(
            source_type=RunSourceType.USER_TASK,
            title="Multi-repo task",
            repository_path=primary,
            repositories=[RepositorySpec(repository_id="repo-b", path=aux_b)],
            repo_patches=[inp],
        )
        orch = OrchestrationService()
        run = await self._prepare(orch, source, primary)
        run.auxiliary_repositories = [{"repository_id": "repo-b", "path": aux_b, "namespace_id": "repo-b"}]
        await orch._store.update(run)

        with patch.object(OrchestrationService, "_stage_testing",
                          new_callable=AsyncMock, return_value=True), \
             patch.object(OrchestrationService, "_stage_review",
                          new_callable=AsyncMock, side_effect=TestExecuteRunPerRepoPatches._mock_stage(StageType.REVIEWING)), \
             patch.object(OrchestrationService, "_stage_quality_gate",
                          new_callable=AsyncMock, side_effect=TestExecuteRunPerRepoPatches._mock_approve()):
            result = await orch.execute_run(run.run_id, workspace_root=primary)

        assert result.status == RunStatus.APPROVED
        # Primary patch applied to the primary checkout.
        assert Path(primary, "main.py").read_text() == "p2\n"
        # Auxiliary patch applied to its OWN checkout.
        assert Path(aux_b, "feature.py").read_text() == "f1\n"
        # The auxiliary patch never touched the primary checkout.
        assert not Path(primary, "feature.py").exists()

        # repo_validation aggregated on the result (primary + aux).
        assert [r.repository_id for r in result.repo_validation] == ["repo-primary", "repo-b"]
        by_id = {r.repository_id: r for r in result.repo_validation}
        assert by_id["repo-primary"].application_status == "applied"
        assert by_id["repo-b"].validation_status == "validated"
        assert by_id["repo-b"].application_status == "applied"

        fresh = await orch._store.get(run.run_id)
        event_types = {e.event_type for e in fresh.events}
        assert EventType.REPOSITORY_PATCH_VALIDATED in event_types
        assert EventType.REPOSITORY_SCOPE_VIOLATION not in event_types

    @staticmethod
    def _mock_stage(target: StageType):
        async def _fn(run, *args, **kwargs):
            run.current_stage = target
            return True
        return _fn

    @staticmethod
    def _mock_approve():
        async def _fn(run, *args, **kwargs):
            run.current_stage = StageType.QUALITY_GATE
            run.status = RunStatus.APPROVED
            return True
        return _fn


# ── API contract (repo_patches body parsing) ──────────────────────


class TestApiRepoPatchesContract:
    def test_repo_patch_input_roundtrip(self, tmp_path):
        aux_b = _make_repo(tmp_path, "repo-b", {"app.py": "b1\n"})
        inp = RepositoryPatchInput(
            repository_id="repo-b", repository_namespace="repo-b",
            workspace_path=aux_b,
            patch=_patch("paux", [_change("C-1", FileOperation.CREATE, "feature.py", "f1\n")], repository_id="repo-b"),
        )
        raw = inp.model_dump(mode="json")
        revived = RepositoryPatchInput.model_validate(raw)
        assert revived.repository_id == "repo-b"
        assert revived.patch.changes[0].path == "feature.py"

    @pytest.mark.asyncio
    async def test_post_runs_accepts_and_rejects_repo_patches(self, tmp_path):
        from fastapi.testclient import TestClient

        import app.api.v1.orchestration as orchestration_module
        from app.main import app
        from app.models.orchestration import DevPilotRunResult

        aux_b = _make_repo(tmp_path, "repo-b", {"app.py": "b1\n"})
        body = {
            "source": "user_task",
            "title": "T",
            "repository": str(tmp_path / "primary"),
            "repo_patches": [{
                "repository_id": "repo-b",
                "repository_namespace": "repo-b",
                "workspace_path": aux_b,
                "patch": _patch(
                    "paux",
                    [_change("C-1", FileOperation.CREATE, "feature.py", "f1\n")],
                    repository_id="repo-b",
                ).model_dump(mode="json"),
            }],
        }

        result = DevPilotRunResult(
            run_id="RUN-1",
            status=RunStatus.APPROVED,
            source=RunSource(source_type=RunSourceType.USER_TASK, title="T"),
            repository=str(tmp_path / "primary"),
            repo_validation=[
                RepositoryPatchResult(
                    repository_id="repo-b", workspace_path=aux_b,
                    validation_status="validated", application_status="applied",
                    status="applied", changes_applied=1, changes_attempted=1,
                )
            ],
            stages=[], events=[], started_at="", finished_at="", duration_seconds=1.0,
        )

        async def _fake_run_user_task(**kwargs):
            assert kwargs.get("repo_patches") is not None
            assert kwargs["repo_patches"][0].repository_id == "repo-b"
            return result

        with TestClient(app) as client, \
             patch.object(orchestration_module, "workflow", MagicMock()) as wf:
            wf.run_user_task = AsyncMock(side_effect=_fake_run_user_task)
            resp = client.post("/api/v1/runs", json=body)
            assert resp.status_code == 200
            wf.run_user_task.assert_awaited_once()
            data = resp.json()["data"]
            assert data["repo_validation"][0]["repository_id"] == "repo-b"
            assert data["repo_validation"][0]["application_status"] == "applied"

        # Malformed spec → 400, no workflow call.
        bad_body = dict(body)
        bad_body["repo_patches"] = [{"repository_id": "repo-b"}]
        with TestClient(app) as client, \
             patch.object(orchestration_module, "workflow", MagicMock()) as wf:
            resp = client.post("/api/v1/runs", json=bad_body)
            assert resp.status_code == 400
            assert not wf.run_user_task.called
