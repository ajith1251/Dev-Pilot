"""
Phase 20 — Tests for slice A1+A2: multi-repository autonomous runs.

Covers:
- RepositorySpec model + RunSource.repositories (backwards compatible).
- OrchestrationService._materialize_auxiliary_repositories: local auxiliary
  repos are registered as org-graph namespaces, explicit cross-repository
  edges are linked, and the run records the namespaces + an
  AUXILIARY_REPOSITORIES_ACQUIRED event.
- execute_run wiring: a local primary repo + auxiliary repos completes
  APPROVED and surfaces auxiliary_repositories on the result; an auxiliary
  acquisition failure fails the run deterministically.

All tests are deterministic and offline (source=local only).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.coding import PatchSet
from app.models.issues import (
    ImplementationPlan,
    ImplementationStep,
    Requirement,
    StructuredRequirements,
)
from app.models.orchestration import (
    EventType,
    RepositorySpec,
    RunSource,
    RunSourceType,
    RunStatus,
    StageType,
)
from app.services.orchestration_service import OrchestrationService
from app.services.organization_graph_service import OrganizationKnowledgeGraphService


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


def _mock_stage(target: StageType):
    async def _fn(run, *args, **kwargs):
        run.current_stage = target
        return True
    return _fn


def _mock_approve():
    async def _fn(run, *args, **kwargs):
        run.current_stage = StageType.QUALITY_GATE
        run.status = RunStatus.APPROVED
        return True
    return _fn


# ── A1: Model surface ─────────────────────────────────────────────


class TestRepositorySpecModel:
    def test_repository_spec_roundtrip(self):
        spec = RepositorySpec(
            repository_id="repo-be",
            name="Backend",
            source="local",
            path="/tmp/be",
            relationships=[
                {"target_repository_id": "repo-fe", "relationship": "references_shared_component"}
            ],
        )
        raw = spec.model_dump()
        revived = RepositorySpec.model_validate(raw)
        assert revived.repository_id == "repo-be"
        assert revived.relationships[0]["target_repository_id"] == "repo-fe"
        assert revived.relationships[0]["relationship"] == "references_shared_component"

    def test_run_source_repositories_optional_backwards_compatible(self):
        source = RunSource(source_type=RunSourceType.USER_TASK, title="T")
        assert source.repositories is None
        assert source.model_dump().get("repositories") is None

    def test_run_source_parses_repositories(self):
        source = RunSource(
            source_type=RunSourceType.USER_TASK,
            title="T",
            repositories=[
                {"repository_id": "repo-a", "path": "/tmp/a"},
                {"repository_id": "repo-b", "path": "/tmp/b"},
            ],
        )
        assert source.repositories is not None
        assert [r.repository_id for r in source.repositories] == ["repo-a", "repo-b"]


# ── A2: materialization (unit) ────────────────────────────────────


class TestMaterializeAuxiliaryRepositories:
    async def _make_run(self, specs):
        orch = OrchestrationService()
        source = RunSource(
            source_type=RunSourceType.USER_TASK,
            title="Multi-repo",
            repository_path="/primary",
            repositories=specs,
        )
        run = await orch.create_run(source)
        # Mirror execute_run: acquisition is first advanced to
        # ACQUIRING_REPOSITORY before auxiliary materialization runs.
        run.current_stage = StageType.ACQUIRING_REPOSITORY
        await orch._store.update(run)
        return orch, run

    @pytest.mark.asyncio
    async def test_materializes_local_repos_links_edges_records_namespaces(self, tmp_path):
        fe = _make_repo(tmp_path, "repo-fe", {"src/index.ts": "fe"})
        be = _make_repo(tmp_path, "repo-be", {"app.py": "be"})
        specs = [
            RepositorySpec(repository_id="repo-fe", path=fe),
            RepositorySpec(
                repository_id="repo-be",
                path=be,
                relationships=[{
                    "target_repository_id": "repo-fe",
                    "relationship": "references_shared_component",
                }],
            ),
        ]
        orch, run = await self._make_run(specs)
        org = OrganizationKnowledgeGraphService()
        with patch("app.services.orchestration_service._get_org_service",
                   return_value=org):
            ok = await orch._materialize_auxiliary_repositories(run)

        assert ok is True
        assert [n["repository_id"] for n in run.auxiliary_repositories] == ["repo-fe", "repo-be"]
        assert [r.repository_id for r in org.repositories()] == ["repo-fe", "repo-be"]
        assert len(org.cross_edges()) == 1
        assert org.cross_edges()[0].relationship.value == "references_shared_component"
        assert EventType.AUXILIARY_REPOSITORIES_ACQUIRED in {e.event_type for e in run.events}
        # Primary repo untouched: materialization only records aux namespaces.
        assert run.repository_path is None
        assert run.source.repository_path == "/primary"
        fresh = await orch._store.get(run.run_id)
        assert fresh.source.repository_path == "/primary"
        assert [n["repository_id"] for n in fresh.auxiliary_repositories] == ["repo-fe", "repo-be"]

    @pytest.mark.asyncio
    async def test_no_repositories_is_noop(self):
        orch, run = await self._make_run(None)
        ok = await orch._materialize_auxiliary_repositories(run)
        assert ok is True
        assert run.auxiliary_repositories == []
        assert EventType.AUXILIARY_REPOSITORIES_ACQUIRED not in {e.event_type for e in run.events}

    @pytest.mark.asyncio
    async def test_empty_repository_list_is_noop(self):
        orch, run = await self._make_run([])
        ok = await orch._materialize_auxiliary_repositories(run)
        assert ok is True
        assert run.auxiliary_repositories == []

    @pytest.mark.asyncio
    async def test_invalid_local_path_fails_run(self, tmp_path):
        specs = [RepositorySpec(repository_id="repo-missing", path=str(tmp_path / "nope"))]
        orch, run = await self._make_run(specs)
        org = OrganizationKnowledgeGraphService()
        with patch("app.services.orchestration_service._get_org_service",
                   return_value=org):
            ok = await orch._materialize_auxiliary_repositories(run)

        assert ok is False
        assert run.status == RunStatus.FAILED
        fresh = await orch._store.get(run.run_id)
        assert fresh.status == RunStatus.FAILED
        assert "Auxiliary repository materialization failed" in fresh.failure.message

    @pytest.mark.asyncio
    async def test_org_service_unavailable_fails_run(self, tmp_path):
        d = _make_repo(tmp_path, "repo-x", {"a.py": "x"})
        specs = [RepositorySpec(repository_id="repo-x", path=d)]
        orch, run = await self._make_run(specs)
        with patch("app.services.orchestration_service._get_org_service",
                   return_value=None):
            ok = await orch._materialize_auxiliary_repositories(run)

        assert ok is False
        assert run.status == RunStatus.FAILED
        assert "organization graph service is unavailable" in run.failure.message


# ── A2: execute_run wiring (integration) ──────────────────────────


class TestExecuteRunAuxRepositories:
    async def _prepare(self, orch, source, primary):
        run = await orch.create_run(source)
        run.repository_profile = MagicMock()
        run.requirements = _make_reqs()
        run.plan = _make_plan()
        run.retrieved_context = MagicMock()
        run.patch_set = PatchSet(patch_id="p", changes=[])
        run.patch_result = MagicMock()
        await orch._store.update(run)
        return run

    @pytest.mark.asyncio
    async def test_execute_run_local_with_aux_repos_completes(self, tmp_path):
        primary = _make_repo(tmp_path, "primary", {"main.py": "p"})
        fe = _make_repo(tmp_path, "repo-fe", {"src/index.ts": "fe"})
        be = _make_repo(tmp_path, "repo-be", {"app.py": "be"})
        specs = [
            RepositorySpec(repository_id="repo-fe", path=fe),
            RepositorySpec(
                repository_id="repo-be",
                path=be,
                relationships=[{
                    "target_repository_id": "repo-fe",
                    "relationship": "references_shared_component",
                }],
            ),
        ]
        orch = OrchestrationService()
        source = RunSource(
            source_type=RunSourceType.USER_TASK,
            title="Multi-repo task",
            repository_path=primary,
            repositories=specs,
        )
        run = await self._prepare(orch, source, primary)

        with patch.object(OrchestrationService, "_stage_patch_validation",
                          new_callable=AsyncMock,
                          side_effect=_mock_stage(StageType.VALIDATING_PATCH)), \
             patch.object(OrchestrationService, "_stage_testing",
                          new_callable=AsyncMock, side_effect=_mock_stage(StageType.TESTING)), \
             patch.object(OrchestrationService, "_stage_review",
                          new_callable=AsyncMock, side_effect=_mock_stage(StageType.REVIEWING)), \
             patch.object(OrchestrationService, "_stage_quality_gate",
                          new_callable=AsyncMock, side_effect=_mock_approve()):
            result = await orch.execute_run(run.run_id, workspace_root=primary)

        assert result.status == RunStatus.APPROVED
        assert [n["repository_id"] for n in result.auxiliary_repositories] == ["repo-fe", "repo-be"]
        fresh = await orch._store.get(run.run_id)
        assert EventType.AUXILIARY_REPOSITORIES_ACQUIRED in {e.event_type for e in fresh.events}

    @pytest.mark.asyncio
    async def test_execute_run_aux_failure_fails_run(self, tmp_path):
        primary = _make_repo(tmp_path, "primary", {"main.py": "p"})
        specs = [RepositorySpec(repository_id="repo-missing", path=str(tmp_path / "nope"))]
        orch = OrchestrationService()
        source = RunSource(
            source_type=RunSourceType.USER_TASK,
            title="Multi-repo task",
            repository_path=primary,
            repositories=specs,
        )
        run = await self._prepare(orch, source, primary)

        result = await orch.execute_run(run.run_id, workspace_root=primary)

        assert result.status == RunStatus.FAILED
        assert result.failure is not None
        assert "Auxiliary repository materialization failed" in result.failure.message
        fresh = await orch._store.get(run.run_id)
        assert fresh.status == RunStatus.FAILED
