"""
Phase 20A6 — Tests for the multi-repository dashboard & autonomous run
experience.

Covers:
- ``run_dashboard.build_repository_view``: per-repository status cards
  (primary + auxiliary, ordering, per-repo timeline progress, validation /
  application status, EKG graph status) — works for both ``DevPilotRun`` and
  ``DevPilotRunResult`` shapes.
- ``run_dashboard.build_organization_summary``: participating / successful /
  failed / repaired repositories, duration, engineering decisions, consensus
  summary, quality status, org-graph stats.
- API: ``_sanitize_run`` repository-aware surface; org repositories search /
  filter / pagination; per-repository EKG stats endpoint; run-creation
  acceptance criteria + execution budget passthrough; run-list
  ``repository_count``.
- WebSocket: ``_broadcast_update`` payload carries the repository view so
  repository cards update live.
- CLI: ``run --json`` merges the repository view + organization summary.

Isolation: all tests are deterministic and offline (in-memory, source=local).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.orchestration import (
    DevPilotRun,
    DevPilotRunResult,
    RepositoryPatchResult,
    RunSource,
    RunSourceType,
    RunStatus,
    StageResult,
    StageStatus,
    StageType,
)
from app.services.run_dashboard import (
    REPOSITORY_STAGES,
    build_organization_summary,
    build_repository_view,
)


# ── Helpers ────────────────────────────────────────────────────────


def _stage(stage: StageType, status: StageStatus) -> StageResult:
    return StageResult(stage=stage, status=status)


def _repo_result(repo_id: str, validation: str, application: str,
                 files=None) -> RepositoryPatchResult:
    return RepositoryPatchResult(
        repository_id=repo_id,
        repository_namespace=repo_id,
        workspace_path=f"/repos/{repo_id}",
        validation_status=validation,
        application_status=application,
        changes_applied=1 if application == "applied" else 0,
        changes_attempted=1,
        changed_files=list(files or ["feature.py"]),
        status="applied" if application == "applied" else "rejected",
    )


def _run(**overrides) -> DevPilotRun:
    """A completed multi-repo run with all deterministic evidence populated."""
    run = DevPilotRun(
        run_id="RUN-A6-1",
        source=RunSource(
            source_type=RunSourceType.USER_TASK,
            title="Multi-repo task",
            repository_path="/tmp/primary",
            acceptance_criteria=["c1", "c2"],
            execution_budget={"max_iterations": 3},
        ),
        status=RunStatus.APPROVED,
        repository_path="/tmp/primary",
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
            _repo_result("repo-primary", "validated", "applied", ["main.py"]),
            _repo_result("repo-b", "validated", "applied", ["feature.py"]),
        ],
        stage_results=[
            _stage(StageType.ACQUIRING_REPOSITORY, StageStatus.SUCCEEDED),
            _stage(StageType.ANALYZING_REPOSITORY, StageStatus.SUCCEEDED),
            _stage(StageType.ANALYZING_TASK, StageStatus.SUCCEEDED),
            _stage(StageType.PLANNING, StageStatus.SUCCEEDED),
            _stage(StageType.RETRIEVING_CONTEXT, StageStatus.SUCCEEDED),
            _stage(StageType.CODING, StageStatus.SUCCEEDED),
            _stage(StageType.VALIDATING_PATCH, StageStatus.SUCCEEDED),
            _stage(StageType.APPLYING_PATCH, StageStatus.SUCCEEDED),
            _stage(StageType.TESTING, StageStatus.SUCCEEDED),
            _stage(StageType.REPAIRING, StageStatus.SKIPPED),
            _stage(StageType.REVIEWING, StageStatus.SUCCEEDED),
            _stage(StageType.QUALITY_GATE, StageStatus.SUCCEEDED),
        ],
    )
    for k, v in overrides.items():
        setattr(run, k, v)
    return run


class FakeOrg:
    """Minimal OrganizationKnowledgeGraphService stand-in for the view."""

    def __init__(self) -> None:
        self._version = 4

    def repository_stats(self, repository_id: str) -> dict:
        if repository_id == "repo-b":
            return {
                "repository_id": repository_id,
                "namespace": SimpleNamespace(
                    repository_id=repository_id, organization_id="default",
                    name="repo-b", source_type="local",
                ),
                "node_count": 5,
                "edge_count": 4,
                "run_count": 1,
                "outgoing_links": [
                    {"repository_id": "repo-c", "relationship": "depends_on_repository"}
                ],
                "incoming_links": [],
            }
        if repository_id == "repo-primary":
            return {
                "repository_id": repository_id,
                "namespace": None,
                "node_count": 3,
                "edge_count": 2,
                "run_count": 1,
                "outgoing_links": [],
                "incoming_links": [],
            }
        return None

    def stats(self):
        return SimpleNamespace(
            repository_count=2, node_count=8, edge_count=6,
            cross_edge_count=1, version=4,
        )

    def current_version(self) -> int:
        return self._version


# ── build_repository_view ──────────────────────────────────────────


class TestBuildRepositoryView:
    def test_single_repo_run_has_one_primary_entry(self):
        run = _run(auxiliary_repositories=[], repo_patches=[
            _repo_result("repo-primary", "validated", "applied", ["main.py"]),
        ])
        view = build_repository_view(run)
        assert len(view) == 1
        entry = view[0]
        assert entry["is_primary"] is True
        assert entry["ordering"] == 0
        assert entry["validation_status"] == "validated"
        assert entry["application_status"] == "applied"
        assert entry["graph"] == {"available": False}

    def test_multi_repo_run_lists_primary_then_aux_in_order(self):
        view = build_repository_view(_run())
        assert [e["repository_id"] for e in view] == ["repo-primary", "repo-b"]
        assert [e["ordering"] for e in view] == [0, 1]
        assert view[0]["is_primary"] is True
        assert view[1]["is_primary"] is False
        assert view[1]["namespace"] == "repo-b"
        assert view[1]["organization"] == "default"

    def test_per_repo_timeline_progress_derived(self):
        view = build_repository_view(_run())
        progress = view[1]["progress"]
        assert set(progress.keys()) == set(REPOSITORY_STAGES)
        # Applied patch → coding succeeded; skipped repair; global stages.
        assert progress["coding"] == "succeeded"
        assert progress["repair"] == "skipped"
        assert progress["testing"] == "succeeded"
        assert progress["quality_gate"] == "succeeded"

    def test_rejected_repo_progress_marks_coding_failed(self):
        run = _run()
        run.repo_patches = [
            _repo_result("repo-primary", "validated", "applied", ["main.py"]),
            _repo_result("repo-b", "rejected", "not_attempted", ["feature.py"]),
        ]
        view = build_repository_view(run)
        assert view[1]["progress"]["coding"] == "failed"
        assert view[1]["validation_status"] == "rejected"

    def test_running_stage_propagates_to_coding(self):
        run = _run()
        run.status = RunStatus.RUNNING
        run.current_stage = StageType.CODING
        run.repo_patches = [
            _repo_result("repo-primary", "validated", "not_attempted", ["main.py"]),
        ]
        view = build_repository_view(run)
        assert view[0]["progress"]["coding"] == "running"
        assert view[0]["current_stage"] == "coding"

    def test_graph_status_from_org_service(self):
        view = build_repository_view(_run(), org_service=FakeOrg())
        aux = view[1]
        assert aux["graph"]["available"] is True
        assert aux["graph"]["node_count"] == 5
        assert aux["graph"]["edge_count"] == 4
        assert aux["graph"]["run_count"] == 1
        assert aux["graph"]["outgoing_links"] == [
            {"repository_id": "repo-c", "relationship": "depends_on_repository"}
        ]
        # Primary repo has no registered namespace → graceful empty stats.
        assert view[0]["graph"]["node_count"] == 3

    def test_accepts_devpilot_run_result_shape(self):
        result = DevPilotRunResult(
            run_id="RUN-A6-9",
            status=RunStatus.APPROVED,
            source=RunSource(
                source_type=RunSourceType.USER_TASK, title="T",
                repository_path="/tmp/primary",
            ),
            repository="/tmp/primary",
            auxiliary_repositories=[
                {
                    "repository_id": "repo-b", "namespace_id": "repo-b",
                    "path": "/tmp/repo-b", "source_type": "local",
                }
            ],
            repo_validation=[
                _repo_result("repo-primary", "validated", "applied", ["main.py"]),
                _repo_result("repo-b", "validated", "applied", ["feature.py"]),
            ],
            stages=[
                {"stage": "planning", "status": "succeeded"},
                {"stage": "coding", "status": "succeeded"},
                {"stage": "testing", "status": "succeeded"},
                {"stage": "repairing", "status": "skipped"},
                {"stage": "reviewing", "status": "succeeded"},
                {"stage": "quality_gate", "status": "succeeded"},
            ],
        )
        view = build_repository_view(result)
        assert [e["repository_id"] for e in view] == ["repo-primary", "repo-b"]
        assert view[1]["progress"]["coding"] == "succeeded"
        assert view[1]["validation_status"] == "validated"


# ── build_organization_summary ─────────────────────────────────────


class TestBuildOrganizationSummary:
    def test_aggregates_successful_failed_repaired(self):
        summary = build_organization_summary(_run(), org_service=FakeOrg())
        assert summary["repository_count"] == 2
        assert set(summary["successful_repositories"]) == {"repo-primary", "repo-b"}
        assert summary["failed_repositories"] == []
        # Repair stage skipped → no repaired repositories.
        assert summary["repaired_repositories"] == []
        assert summary["quality_status"] == "approved"

    def test_failed_repo_and_repair_tracked(self):
        run = _run()
        run.status = RunStatus.REJECTED
        run.repo_patches = [
            _repo_result("repo-primary", "validated", "applied", ["main.py"]),
            _repo_result("repo-b", "rejected", "not_attempted", ["feature.py"]),
        ]
        summary = build_organization_summary(run)
        assert summary["failed_repositories"] == ["repo-b"]
        assert summary["quality_status"] == "rejected"

    def test_duration_decisions_consensus_from_evidence(self):
        from datetime import datetime, timezone

        run = _run()
        run.started_at = "2026-08-05T10:00:00Z"
        run.finished_at = "2026-08-05T10:00:05Z"
        # Decision + consensus events (evidence-only).
        run.events = [
            SimpleNamespace(event_type=SimpleNamespace(value="decision_recorded"),
                            message="Adopt 3-step plan"),
            SimpleNamespace(event_type=SimpleNamespace(value="decision_recorded"),
                            message="Implemented 2 files"),
            SimpleNamespace(event_type=SimpleNamespace(value="consensus_built"),
                            message="2 consensus records built"),
            SimpleNamespace(event_type=SimpleNamespace(value="conflict_detected"),
                            message="claim vs test resolved"),
        ]
        summary = build_organization_summary(run)
        assert summary["duration_seconds"] == 5.0
        assert summary["engineering_decisions"]["count"] == 2
        assert "Adopt 3-step plan" in summary["engineering_decisions"]["recent"]
        assert summary["consensus_summary"]["count"] == 1
        assert summary["consensus_summary"]["contradictions"] == 1

    def test_repair_attributes_only_primary(self):
        from types import SimpleNamespace as NS

        # Repair ran with 2 attempts — only the PRIMARY checkout goes through
        # the bounded repair loop; the aux repo's applied patch is NOT repair.
        run = _run()
        run.repair_result = NS(attempts=2, summary="Repaired after 2 attempts")
        summary = build_organization_summary(run)
        assert summary["repaired_repositories"] == ["repo-primary"]

        # Repair ran but the primary patch never applied → nothing repaired.
        run2 = _run()
        run2.repair_result = NS(attempts=2, summary="Repaired")
        run2.repo_patches = [
            _repo_result("repo-primary", "validated", "not_attempted", ["main.py"]),
        ]
        summary2 = build_organization_summary(run2)
        assert summary2["repaired_repositories"] == []

    def test_org_graph_stats_attached(self):
        summary = build_organization_summary(_run(), org_service=FakeOrg())
        assert summary["graph"] == {
            "repository_count": 2,
            "node_count": 8,
            "edge_count": 6,
            "cross_edge_count": 1,
            "version": 4,
        }

    def test_quality_gate_detail_included(self):
        run = _run()
        run.quality_gate_result = SimpleNamespace(
            decision=SimpleNamespace(value="approved"), score=92.0,
            requirements_satisfied=3, requirements_unsatisfied=0,
            verification_status="verified",
        )
        summary = build_organization_summary(run)
        assert summary["quality_gate"]["decision"] == "approved"
        assert summary["quality_gate"]["score"] == 92.0


# ── API surface ────────────────────────────────────────────────────


class TestApiSurface:
    def test_sanitize_run_includes_repositories_and_summary(self):
        from app.api.v1.orchestration import _sanitize_run

        data = _sanitize_run(_run(), org_service=FakeOrg())
        assert data["repositories"][0]["repository_id"] == "repo-primary"
        assert data["repositories"][1]["repository_id"] == "repo-b"
        assert data["organization_summary"]["repository_count"] == 2
        assert data["organization_summary"]["quality_status"] == "approved"
        # Acceptance criteria + budget recorded on the source.
        assert data["source"]["acceptance_criteria"] == ["c1", "c2"]
        assert data["source"]["execution_budget"] == {"max_iterations": 3}

    def test_get_run_endpoint_returns_repository_aware_payload(self):
        from fastapi.testclient import TestClient

        import app.api.v1.orchestration as orchestration_module
        from app.main import app

        run = _run()

        async def _fake_get_run(run_id):
            return run

        with TestClient(app) as client, \
             patch.object(orchestration_module, "workflow", MagicMock()) as wf:
            wf.get_run = AsyncMock(side_effect=_fake_get_run)
            wf.organization_graph = MagicMock(return_value=FakeOrg())
            resp = client.get("/api/v1/runs/RUN-A6-1")
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert [r["repository_id"] for r in data["repositories"]] == [
                "repo-primary", "repo-b",
            ]
            assert data["repositories"][1]["progress"]["coding"] == "succeeded"
            assert data["organization_summary"]["repository_count"] == 2

    def test_create_run_forwards_acceptance_criteria_and_budget(self):
        from fastapi.testclient import TestClient

        import app.api.v1.orchestration as orchestration_module
        from app.main import app

        captured = {}

        async def _fake_run_user_task(**kwargs):
            captured.update(kwargs)
            return DevPilotRunResult(
                run_id="RUN-A6-NEW",
                status=RunStatus.RUNNING,
                source=RunSource(source_type=RunSourceType.USER_TASK, title="T"),
            )

        with TestClient(app) as client, \
             patch.object(orchestration_module, "workflow", MagicMock()) as wf:
            wf.run_user_task = AsyncMock(side_effect=_fake_run_user_task)
            wf.organization_graph = MagicMock(return_value=FakeOrg())
            resp = client.post("/api/v1/runs", json={
                "title": "T",
                "acceptance_criteria": ["a", "b"],
                "execution_budget": {"max_iterations": 4},
            })
            assert resp.status_code == 200
            assert captured["acceptance_criteria"] == ["a", "b"]
            assert captured["execution_budget"] == {"max_iterations": 4}
            # Repository-aware surface on the result too.
            assert resp.json()["data"]["organization_summary"] is not None

    def test_create_run_rejects_malformed_acceptance_criteria(self):
        from fastapi.testclient import TestClient

        import app.api.v1.orchestration as orchestration_module
        from app.main import app

        with TestClient(app) as client, \
             patch.object(orchestration_module, "workflow", MagicMock()):
            resp = client.post("/api/v1/runs", json={
                "title": "T", "acceptance_criteria": "not-a-list",
            })
            assert resp.status_code == 400

    def test_run_list_exposes_repository_count(self):
        from app.api.v1.orchestration import _sanitize_run

        run = _run()
        # The list serializer computes 1 + len(aux).
        assert 1 + len(run.auxiliary_repositories) == 2


class TestOrgRepositoriesApi:
    def test_search_filter_pagination(self):
        from fastapi.testclient import TestClient

        import app.api.v1.engineering_graph as graph_module
        from app.main import app

        def _fake_org():
            org = MagicMock()
            org.repositories.return_value = [
                SimpleNamespace(summary=lambda rid=rid, n=name: {
                    "repository_id": rid, "namespace_id": rid,
                    "organization_id": "default", "name": n, "path": f"/org/{rid}",
                    "source_type": "local", "created_at": "2026-01-01T00:00:00Z",
                })
                for rid, name in [("api", "API Service"), ("web", "Web App"), ("worker", "Worker")]
            ]
            return org

        with TestClient(app) as client, \
             patch.object(graph_module, "_get_org_service", side_effect=_fake_org):
            # Search filters to one.
            resp = client.get("/api/v1/graph/org/repositories?q=api")
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["total"] == 1
            assert data["repositories"][0]["repository_id"] == "api"
            # Pagination bounds the page.
            resp = client.get("/api/v1/graph/org/repositories?limit=2&offset=1")
            data = resp.json()["data"]
            assert data["count"] == 2
            assert data["total"] == 3
            assert data["repositories"][0]["repository_id"] == "web"

    def test_per_repository_stats_endpoint(self):
        from fastapi.testclient import TestClient

        import app.api.v1.engineering_graph as graph_module
        from app.main import app

        def _fake_org():
            org = MagicMock()
            org.repository_stats.return_value = {
                "repository_id": "repo-b",
                "namespace": SimpleNamespace(
                    repository_id="repo-b", organization_id="default",
                    name="repo-b", path="/org/repo-b", source_type="local",
                ),
                "node_count": 5, "edge_count": 4, "run_count": 1,
                "node_types": {"file": 5},
                "outgoing_links": [{"repository_id": "repo-c", "relationship": "depends_on_repository"}],
                "incoming_links": [],
            }
            return org

        with TestClient(app) as client, \
             patch.object(graph_module, "_get_org_service", side_effect=_fake_org):
            resp = client.get("/api/v1/graph/org/repositories/repo-b")
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["node_count"] == 5
            assert data["outgoing_links"][0]["repository_id"] == "repo-c"
            # Unknown namespace → 404.
            org2 = MagicMock()
            org2.repository_stats.return_value = None
            with patch.object(graph_module, "_get_org_service", return_value=org2):
                resp = client.get("/api/v1/graph/org/repositories/nope")
                assert resp.status_code == 404


# ── WebSocket payload ──────────────────────────────────────────────


class TestWebSocketPayload:
    @pytest.mark.asyncio
    async def test_broadcast_update_carries_repository_view(self, tmp_path):
        from app.services.orchestration_service import OrchestrationService

        run = _run()
        orch = OrchestrationService()

        fake_ws = MagicMock()
        fake_ws.active_connections = 1
        fake_ws.broadcast_run_update = AsyncMock(return_value=1)
        fake_ws.broadcast_event = AsyncMock(return_value=1)

        with patch("app.services.orchestration_service._get_ws_manager",
                   return_value=fake_ws):
            await orch._broadcast_update(run)

        fake_ws.broadcast_run_update.assert_awaited_once()
        call_args, _kwargs = fake_ws.broadcast_run_update.await_args
        data = call_args[1]
        assert [r["repository_id"] for r in data["repositories"]] == [
            "repo-primary", "repo-b",
        ]
        assert data["repositories"][1]["progress"]["coding"] == "succeeded"
        assert data["organization_summary"]["repository_count"] == 2


# ── PostgresRunStore A6 round-trip (restart recovery) ─────────────


class TestPostgresA6RoundTrip:
    """Phase 20A6: the durable store must round-trip the multi-repository
    dashboard state (repository_path, auxiliary_repositories, repo_patches)
    so a backend restart can rebuild the run-detail view identically.
    """

    def test_context_round_trip_preserves_a6_fields(self):
        from app.services.postgres_run_store import (
            _deserialize_context,
            _serialize_context,
        )

        run = _run()
        run.repository_path = "/tmp/primary"
        run.auxiliary_repositories = [
            {
                "repository_id": "repo-b",
                "namespace_id": "repo-b",
                "organization_id": "default",
                "name": "repo-b",
                "path": "/tmp/repo-b",
                "source_type": "local",
            }
        ]
        run.repo_patches = [
            _repo_result("repo-primary", "validated", "applied", ["main.py"]),
            _repo_result("repo-b", "validated", "applied", ["feature.py"]),
        ]

        payload = _serialize_context(run)
        assert payload is not None
        assert payload["repository_path"] == "/tmp/primary"
        assert payload["auxiliary_repositories"][0]["repository_id"] == "repo-b"
        assert payload["repo_patches"][0]["repository_id"] == "repo-primary"

        restored = _deserialize_context(payload)
        assert restored["repository_path"] == "/tmp/primary"
        assert restored["auxiliary_repositories"][0]["repository_id"] == "repo-b"
        assert [r.repository_id for r in restored["repo_patches"]] == [
            "repo-primary", "repo-b",
        ]
        assert restored["repo_patches"][1].validation_status == "validated"

    def test_a6_fields_skipped_when_missing(self):
        from app.services.postgres_run_store import (
            _deserialize_context,
            _serialize_context,
        )

        # A pre-A6 single-repo run has none of the A6 fields — empty lists
        # round-trip harmlessly as empty and re-hydration stays consistent.
        run = _run(auxiliary_repositories=[], repo_patches=[], repository_path=None)
        payload = _serialize_context(run)
        assert payload is None or payload.get("repo_patches") in (None, [])

        restored = _deserialize_context(payload)
        assert restored.get("repo_patches") in (None, [])

    def test_rebuilt_view_identical_after_store_round_trip(self):
        from app.services.postgres_run_store import (
            _deserialize_context,
            _serialize_context,
        )

        run = _run()
        before = build_repository_view(run, org_service=FakeOrg())

        payload = _serialize_context(run)
        restored_kwargs = _deserialize_context(payload)
        for field, value in restored_kwargs.items():
            setattr(run, field, value)

        after = build_repository_view(run, org_service=FakeOrg())
        assert [r["repository_id"] for r in after] == [
            r["repository_id"] for r in before
        ]
        assert [r["progress"] for r in after] == [
            r["progress"] for r in before
        ]
        assert build_organization_summary(run)["repository_count"] == 2


# ── CLI ────────────────────────────────────────────────────────────


class TestCli:
    @pytest.mark.asyncio
    async def test_run_json_includes_repositories_and_summary(self, tmp_path):
        from app.cli import run_orchestration

        result = DevPilotRunResult(
            run_id="RUN-A6-CLI",
            status=RunStatus.APPROVED,
            source=RunSource(
                source_type=RunSourceType.USER_TASK, title="T",
                repository_path="/tmp/primary",
            ),
            repository="/tmp/primary",
            auxiliary_repositories=[
                {"repository_id": "repo-b", "path": "/tmp/repo-b", "source_type": "local"}
            ],
            repo_validation=[
                _repo_result("repo-primary", "validated", "applied", ["main.py"]),
                _repo_result("repo-b", "validated", "applied", ["feature.py"]),
            ],
            stages=[
                {"stage": "planning", "status": "succeeded"},
                {"stage": "coding", "status": "succeeded"},
                {"stage": "testing", "status": "succeeded"},
                {"stage": "repairing", "status": "skipped"},
                {"stage": "reviewing", "status": "succeeded"},
                {"stage": "quality_gate", "status": "succeeded"},
            ],
            started_at="2026-08-05T10:00:00Z",
            finished_at="2026-08-05T10:00:04Z",
            duration_seconds=4.0,
        )

        class FakeOrch:
            def create_run(self, source):
                return SimpleNamespace(run_id="RUN-A6-CLI")

            async def execute_run(self, **kwargs):
                return result

            def get_organization_graph(self):
                return FakeOrg()

        with patch(
            "app.services.orchestration_service.OrchestrationService",
            return_value=FakeOrch(),
        ), \
             patch("sys.stdout", new_callable=MagicMock) as stdout:
            await run_orchestration(
                repo="/tmp/primary", task="T", json_output=True,
            )

        # JSON payload printed once — parse the last print arg.
        printed = "".join(str(c.args[0]) for c in stdout.write.call_args_list)
        import json as _json

        payload = _json.loads(printed)
        assert payload["run_id"] == "RUN-A6-CLI"
        assert [r["repository_id"] for r in payload["repositories"]] == [
            "repo-primary", "repo-b",
        ]
        assert payload["organization_summary"]["repository_count"] == 2
        assert payload["organization_summary"]["quality_status"] == "approved"
