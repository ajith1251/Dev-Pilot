"""
Phase 20 — Tests for slice A5: per-repository EKG ingestion.

Covers:
- RepositoryPatchResult.changed_files: model round-trip + orchestrator
  population during per-repo patch validation.
- OrganizationKnowledgeGraphService.record_run_across_namespaces: a
  cross-repository run's shared evidence lands in the org graph while each
  per-repository patch result is ingested into the OWNING repository's
  namespace (RUN/REPOSITORY/PATCH/FILE nodes + REFERENCES/MODIFIES edges),
  and the org graph links the run across namespaces via REFERENCES edges to
  each involved REPOSITORY node.
- Unregistered namespaces degrade gracefully (evidence-only, never fatal).
- Auxiliary repositories are linked even without a per-repo patch result.
- Idempotency: re-ingesting the same run upserts nodes and dedups edges.
- OrchestrationService._ingest_into_graph routing: multi-repo runs go
  through the org graph; single-repo runs keep the engineering graph path.

All tests are deterministic and offline (in-memory graph, source=local only).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.coding import (
    FileChange,
    FileOperation,
    PatchSet,
)
from app.models.engineering_graph import (
    EKNodeType,
    EKRelationshipType,
)
from app.models.issues import (
    ImplementationPlan,
    ImplementationStep,
    Requirement,
    StructuredRequirements,
)
from app.models.orchestration import (
    DevPilotRun,
    RepositoryPatchInput,
    RepositoryPatchResult,
    RepositorySpec,
    RunSource,
    RunSourceType,
    RunStatus,
    StageType,
)
from app.services.organization_graph_service import OrganizationKnowledgeGraphService
from app.services.orchestration_service import OrchestrationService


# ── Helpers ────────────────────────────────────────────────────────


def _make_repo(tmp_path, rid: str, files: dict) -> str:
    d = tmp_path / rid
    d.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_text(content)
    return str(d)


def _change(change_id: str, operation: FileOperation, path: str, content: str | None = None) -> FileChange:
    return FileChange(
        change_id=change_id, operation=operation, path=path, new_content=content,
    )


def _patch(patch_id: str, changes, repository_id: str | None = None) -> PatchSet:
    return PatchSet(patch_id=patch_id, changes=changes, repository_id=repository_id)


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


def _repo_result(
    repo_id: str,
    files: list[str] | None = None,
    patch_id: str = "paux",
) -> RepositoryPatchResult:
    return RepositoryPatchResult(
        repository_id=repo_id,
        repository_namespace=repo_id,
        workspace_path=f"/repos/{repo_id}",
        patch_id=patch_id,
        validation_status="validated",
        application_status="applied",
        changes_applied=len(files or []),
        changes_attempted=len(files or []),
        changed_files=list(files or ["src/lib.py", "tests/test_lib.py"]),
        status="applied",
    )


def _stub_run(
    run_id: str = "RUN-A5-1",
    repo: str = "/primary",
    repo_patches: list | None = None,
    auxiliary_repositories: list | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        run_id=run_id,
        repository_path=repo,
        status="approved",
        source=SimpleNamespace(title="A5 task"),
        requirements=None,
        plan=None,
        patch_set=None,
        test_result=None,
        repair_result=None,
        review_report=None,
        quality_gate_result=None,
        repo_patches=repo_patches or [],
        auxiliary_repositories=auxiliary_repositories or [],
    )


# ── changed_files model + orchestrator population ───────────────────


class TestChangedFilesEvidence:
    def test_repository_patch_result_changed_files_roundtrip(self):
        res = RepositoryPatchResult(
            repository_id="repo-b",
            workspace_path="/repos/repo-b",
            changed_files=["a.py", "pkg/b.py"],
        )
        raw = res.model_dump(mode="json")
        assert raw["changed_files"] == ["a.py", "pkg/b.py"]
        revived = RepositoryPatchResult.model_validate(raw)
        assert revived.changed_files == ["a.py", "pkg/b.py"]

    def test_primary_result_summary_includes_changed_files(self):
        res = RepositoryPatchResult(
            repository_id="repo-primary", workspace_path="/primary",
            changed_files=["main.py", "lib.py"],
        )
        assert res.summary()["changed_files"] == ["main.py", "lib.py"]

    @pytest.mark.asyncio
    async def test_validate_single_repo_patch_populates_changed_files(self, tmp_path):
        primary = _make_repo(tmp_path, "primary", {"main.py": "p1\n"})
        aux_b = _make_repo(tmp_path, "repo-b", {"app.py": "b1\n"})
        inp = RepositoryPatchInput(
            repository_id="repo-b", repository_namespace="repo-b",
            workspace_path=aux_b,
            patch=_patch("paux", [
                _change("C-1", FileOperation.CREATE, "feature.py", "f1\n"),
                _change("C-2", FileOperation.MODIFY, "app.py", "b2\n"),
            ], repository_id="repo-b"),
        )
        orch = OrchestrationService()
        source = RunSource(
            source_type=RunSourceType.USER_TASK, title="T",
            repository_path=primary, repo_patches=[inp],
        )
        run = await orch.create_run(source)
        run.current_stage = StageType.CODING
        run.repository_path = primary
        await orch._store.update(run)

        assert await orch._stage_patch_validation(run, primary) is True

        res = run.repo_patches[0]
        assert res.repository_id == "repo-b"
        assert res.validation_status == "validated"
        assert res.changed_files == ["feature.py", "app.py"]


# ── OrganizationKnowledgeGraphService.record_run_across_namespaces ──


class TestRecordRunAcrossNamespaces:
    @pytest.mark.asyncio
    async def test_ingests_patch_into_owning_repository_namespace(self, tmp_path):
        aux_b = _make_repo(tmp_path, "repo-b", {"app.py": "b1\n"})
        org = OrganizationKnowledgeGraphService()
        org.register_repository("repo-b", name="repo-b", path=aux_b, source_type="local")

        run = _stub_run(run_id="RUN-A5-1", repo_patches=[
            _repo_result("repo-b", files=["feature.py", "src/lib.py"]),
        ])

        version = await org.record_run_across_namespaces(run)

        assert version.version > 1
        graph_b = org.get_graph("repo-b")
        assert graph_b is not None
        # Run node + PATCH + REPOSITORY + 2 FILE nodes.
        run_nodes = graph_b.find_nodes(node_type=EKNodeType.RUN, source_ref="RUN-A5-1", repository_id="repo-b")
        assert len(run_nodes) == 1
        patch_nodes = graph_b.find_nodes(node_type=EKNodeType.PATCH, source_ref="RUN-A5-1", repository_id="repo-b")
        assert len(patch_nodes) == 1
        patch_node = patch_nodes[0]
        assert patch_node.payload["files_changed"] == 2
        assert set(patch_node.payload["files"]) == {"feature.py", "src/lib.py"}
        assert patch_node.payload["validation_status"] == "validated"
        assert patch_node.payload["application_status"] == "applied"

        files = graph_b.find_nodes(node_type=EKNodeType.FILE, repository_id="repo-b")
        fpaths = {n.source_ref for n in files}
        assert {"feature.py", "src/lib.py"} <= fpaths
        # PATCH -> FILE MODIFIES edges exist for both changed files.
        modifies = {
            e.target_id
            for e in graph_b.get_edges(patch_node.node_id)
            if e.relationship == EKRelationshipType.MODIFIES
        }
        file_ids = {n.node_id for n in files if n.source_ref in {"feature.py", "src/lib.py"}}
        assert modifies == file_ids

    @pytest.mark.asyncio
    async def test_run_linked_across_namespaces_in_org_graph(self, tmp_path):
        aux_b = _make_repo(tmp_path, "repo-b", {"app.py": "b1\n"})
        org = OrganizationKnowledgeGraphService()
        org.register_repository("repo-b", name="repo-b", path=aux_b, source_type="local")

        run = _stub_run(run_id="RUN-A5-2", repo_patches=[
            _repo_result("repo-b", files=["feature.py"]),
        ])

        await org.record_run_across_namespaces(run)

        # Shared run evidence lives in the org (default) namespace.
        run_node = org._org_graph._find_run_node("RUN-A5-2", EKNodeType.RUN)
        assert run_node is not None
        assert run_node.repository_id == "default"
        # The org graph links the run to the involved repo's REPOSITORY node.
        edges = org._org_graph.get_edges(run_node.node_id)
        assert any(
            e.relationship == EKRelationshipType.REFERENCES
            and e.target_id == "REPO::repo-b"
            for e in edges
        )

    @pytest.mark.asyncio
    async def test_unregistered_namespace_skipped_gracefully(self, tmp_path):
        org = OrganizationKnowledgeGraphService()

        run = _stub_run(run_id="RUN-A5-3", repo_patches=[
            _repo_result("repo-unknown", files=["x.py"]),
        ])

        version = await org.record_run_across_namespaces(run)

        assert version.version > 1
        # The org graph still recorded the shared run evidence.
        assert org._org_graph._find_run_node("RUN-A5-3", EKNodeType.RUN) is not None
        # No per-repo graph exists for the unknown namespace.
        assert org.get_graph("repo-unknown") is None

    @pytest.mark.asyncio
    async def test_auxiliary_repositories_linked_without_patch_result(self, tmp_path):
        aux_b = _make_repo(tmp_path, "repo-b", {"app.py": "b1\n"})
        org = OrganizationKnowledgeGraphService()
        org.register_repository("repo-b", name="repo-b", path=aux_b, source_type="local")

        run = _stub_run(
            run_id="RUN-A5-4",
            auxiliary_repositories=[{"repository_id": "repo-b", "namespace_id": "repo-b", "path": aux_b}],
        )

        await org.record_run_across_namespaces(run)

        run_node = org._org_graph._find_run_node("RUN-A5-4", EKNodeType.RUN)
        assert run_node is not None
        edges = org._org_graph.get_edges(run_node.node_id)
        assert any(
            e.relationship == EKRelationshipType.REFERENCES
            and e.target_id == "REPO::repo-b"
            for e in edges
        )
        # No per-repo PATCH evidence was injected (no patch result to ingest).
        assert org.get_graph("repo-b").find_nodes(node_type=EKNodeType.PATCH) == []

    @pytest.mark.asyncio
    async def test_reingest_is_idempotent(self, tmp_path):
        aux_b = _make_repo(tmp_path, "repo-b", {"app.py": "b1\n"})
        org = OrganizationKnowledgeGraphService()
        org.register_repository("repo-b", name="repo-b", path=aux_b, source_type="local")

        run = _stub_run(run_id="RUN-A5-5", repo_patches=[
            _repo_result("repo-b", files=["feature.py"]),
        ])

        await org.record_run_across_namespaces(run)
        graph_b = org.get_graph("repo-b")
        nodes_after_first = len(graph_b.all_nodes(limit=10_000))
        edges_after_first = sum(len(tgt) for src in graph_b._edges.values() for tgt in src.values())

        await org.record_run_across_namespaces(run)

        assert len(graph_b.all_nodes(limit=10_000)) == nodes_after_first
        assert sum(len(tgt) for src in graph_b._edges.values() for tgt in src.values()) == edges_after_first

    @pytest.mark.asyncio
    async def test_empty_run_id_is_noop(self):
        org = OrganizationKnowledgeGraphService()
        before = org.current_version()
        await org.record_run_across_namespaces(_stub_run(run_id=""))
        assert org.current_version() == before


# ── OrchestrationService._ingest_into_graph routing ─────────────────


class TestIngestIntoGraphRouting:
    @pytest.mark.asyncio
    async def test_multi_repo_run_routes_to_org_graph(self, tmp_path):
        primary = _make_repo(tmp_path, "primary", {"main.py": "p1\n"})
        orch = OrchestrationService()
        run = await orch.create_run(RunSource(
            source_type=RunSourceType.USER_TASK, title="T", repository_path=primary,
        ))
        run.repo_patches = [_repo_result("repo-b", files=["feature.py"])]

        org = MagicMock()
        org.record_run_across_namespaces = AsyncMock(return_value=None)
        graph = MagicMock()
        graph.record_run = AsyncMock(return_value=None)

        with patch.object(orch, "_get_org_graph", return_value=org), \
             patch.object(orch, "_get_engineering_graph", return_value=graph):
            await orch._ingest_into_graph(run, reasoning_outcome={"consensus": []})

        org.record_run_across_namespaces.assert_awaited_once_with(
            run, reasoning_outcome={"consensus": []},
        )
        graph.record_run.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_single_repo_run_keeps_engineering_graph(self, tmp_path):
        primary = _make_repo(tmp_path, "primary", {"main.py": "p1\n"})
        orch = OrchestrationService()
        run = await orch.create_run(RunSource(
            source_type=RunSourceType.USER_TASK, title="T", repository_path=primary,
        ))

        org = MagicMock()
        org.record_run_across_namespaces = AsyncMock(return_value=None)
        graph = MagicMock()
        graph.record_run = AsyncMock(return_value=None)

        with patch.object(orch, "_get_org_graph", return_value=org), \
             patch.object(orch, "_get_engineering_graph", return_value=graph):
            await orch._ingest_into_graph(run, reasoning_outcome=None)

        graph.record_run.assert_awaited_once_with(run, reasoning_outcome=None)
        org.record_run_across_namespaces.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_org_graph_failure_falls_back_to_engineering_graph(self, tmp_path):
        primary = _make_repo(tmp_path, "primary", {"main.py": "p1\n"})
        orch = OrchestrationService()
        run = await orch.create_run(RunSource(
            source_type=RunSourceType.USER_TASK, title="T", repository_path=primary,
        ))
        run.auxiliary_repositories = [{"repository_id": "repo-b", "path": str(tmp_path / "repo-b")}]

        org = MagicMock()
        org.record_run_across_namespaces = AsyncMock(side_effect=RuntimeError("boom"))
        graph = MagicMock()
        graph.record_run = AsyncMock(return_value=None)

        with patch.object(orch, "_get_org_graph", return_value=org), \
             patch.object(orch, "_get_engineering_graph", return_value=graph):
            await orch._ingest_into_graph(run)

        org.record_run_across_namespaces.assert_awaited_once()
        graph.record_run.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_end_to_end_execute_run_ingests_per_repo_evidence(self, tmp_path):
        primary = _make_repo(tmp_path, "primary", {"main.py": "p1\n"})
        aux_b = _make_repo(tmp_path, "repo-b", {"app.py": "b1\n"})
        inp = RepositoryPatchInput(
            repository_id="repo-b", repository_namespace="repo-b",
            workspace_path=aux_b,
            patch=_patch("paux", [_change("C-1", FileOperation.CREATE, "feature.py", "f1\n")], repository_id="repo-b"),
        )
        source = RunSource(
            source_type=RunSourceType.USER_TASK, title="Multi-repo task",
            repository_path=primary,
            repositories=[RepositorySpec(repository_id="repo-b", path=aux_b)],
            repo_patches=[inp],
        )
        orch = OrchestrationService()
        run = await orch.create_run(source)
        # All upstream stages are pre-populated (guards skip them) and every
        # stage method calls _transition_to directly, so current_stage must be
        # pre-advanced to CODING for the real validation transition to be valid.
        run.current_stage = StageType.CODING
        run.repository_path = primary
        run.repository_profile = MagicMock()
        run.requirements = _make_reqs()
        run.plan = _make_plan()
        run.retrieved_context = MagicMock()
        run.patch_set = _patch("primary", [_change("C-0", FileOperation.MODIFY, "main.py", "p2\n")])
        await orch._store.update(run)

        with patch.object(orch, "_stage_testing", new_callable=AsyncMock, return_value=True), \
             patch.object(orch, "_stage_review", new_callable=AsyncMock) as review, \
             patch.object(orch, "_stage_quality_gate", new_callable=AsyncMock) as gate:

            async def _review(run_, *a, **k):
                run_.current_stage = StageType.REVIEWING
                return True

            async def _gate(run_, *a, **k):
                run_.current_stage = StageType.QUALITY_GATE
                run_.status = RunStatus.APPROVED
                return True

            review.side_effect = _review
            gate.side_effect = _gate

            result = await orch.execute_run(run.run_id, workspace_root=primary)

        assert result.status == RunStatus.APPROVED
        assert [r.repository_id for r in result.repo_validation] == ["repo-primary", "repo-b"]

        # The shared org graph (lazily created by this instance) registered
        # repo-b during materialization AND ingested the per-repo evidence:
        # repo-b's own namespace now carries RUN/PATCH/FILE evidence.
        org = orch._get_org_graph()
        assert org is not None
        assert org.get_namespace("repo-b") is not None
        graph_b = org.get_graph("repo-b")
        assert graph_b is not None
        run_nodes = graph_b.find_nodes(node_type=EKNodeType.RUN, source_ref=run.run_id, repository_id="repo-b")
        assert len(run_nodes) == 1
        patch_nodes = graph_b.find_nodes(node_type=EKNodeType.PATCH, source_ref=run.run_id, repository_id="repo-b")
        assert len(patch_nodes) == 1
        assert patch_nodes[0].payload["files"] == ["feature.py"]
        files = graph_b.find_nodes(node_type=EKNodeType.FILE, repository_id="repo-b")
        assert any(n.source_ref == "feature.py" for n in files)

        # The org graph linked the run across namespaces.
        run_node = org._org_graph._find_run_node(run.run_id, EKNodeType.RUN)
        assert run_node is not None
        assert any(
            e.relationship == EKRelationshipType.REFERENCES
            and e.target_id == "REPO::repo-b"
            for e in org._org_graph.get_edges(run_node.node_id)
        )


class TestRunDetailApiSurface:
    """Phase 20 slice A6 — the run-detail API exposes the multi-repo surface
    (`auxiliary_repositories` + per-repo `repo_validation`) so the dashboard can
    render it."""

    def _run(self) -> DevPilotRun:
        return DevPilotRun(
            run_id="RUN-A6-1",
            source=RunSource(
                source_type=RunSourceType.USER_TASK,
                title="A6 task",
                repository_path="/tmp/primary",
            ),
            auxiliary_repositories=[
                {
                    "repository_id": "repo-b",
                    "namespace_id": "repo-b",
                    "organization_id": "default",
                    "name": "repo-b",
                    "path": "/tmp/repo-b",
                    "source_type": "local",
                }
            ],
            repo_patches=[
                RepositoryPatchResult(
                    repository_id="repo-b",
                    repository_namespace="repo-b",
                    workspace_path="/tmp/repo-b",
                    validation_status="validated",
                    application_status="applied",
                    changes_applied=1,
                    changes_attempted=1,
                    changed_files=["feature.py"],
                )
            ],
        )

    def test_sanitize_run_exposes_auxiliary_repositories_and_repo_validation(self):
        from app.api.v1.orchestration import _sanitize_run

        data = _sanitize_run(self._run())
        assert data["auxiliary_repositories"] == [
            {
                "repository_id": "repo-b",
                "namespace_id": "repo-b",
                "organization_id": "default",
                "name": "repo-b",
                "path": "/tmp/repo-b",
                "source_type": "local",
            }
        ]
        assert data["repo_validation"][0]["repository_id"] == "repo-b"
        assert data["repo_validation"][0]["validation_status"] == "validated"
        assert data["repo_validation"][0]["changed_files"] == ["feature.py"]

    def test_get_run_endpoint_returns_phase20_surface(self):
        from fastapi.testclient import TestClient

        import app.api.v1.orchestration as orchestration_module
        from app.main import app

        run = self._run()

        async def _fake_get_run(run_id):
            return run

        with TestClient(app) as client, \
             patch.object(orchestration_module, "workflow", MagicMock()) as wf:
            wf.get_run = AsyncMock(side_effect=_fake_get_run)
            resp = client.get("/api/v1/runs/RUN-A6-1")
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["auxiliary_repositories"][0]["repository_id"] == "repo-b"
            assert data["repo_validation"][0]["repository_id"] == "repo-b"
            assert data["repo_validation"][0]["changed_files"] == ["feature.py"]
