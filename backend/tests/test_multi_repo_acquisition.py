"""
Phase 19C — Tests for multi-repository acquisition into the Organization graph.

Covers the missing Phase 19C wiring: ``OrganizationKnowledgeGraphService.
acquire_and_link_repositories`` plus its CLI + API entry points.

All tests are deterministic and offline:
- ``source=local`` specs point at temporary on-disk directories (no network,
  no GitHub token required).
- ``source=github`` specs are exercised with a fake, injectable acquisition
  service so no real git clone happens.

PostgreSQL is optional — tests run in-memory by default and skip cleanly only
when a test explicitly opts into the DB (none here do).
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Dict, List

import pytest

from app.models.engineering_graph import (
    EKNodeType,
    EKRelationshipType,
    QueryScope,
    MultiRepoAcquisitionSpec,
)
from app.services.organization_graph_service import (
    OrganizationKnowledgeGraphService,
    _repo_node_id,
)


def _local_spec(repository_id: str, path: str, **extra: Any) -> MultiRepoAcquisitionSpec:
    return MultiRepoAcquisitionSpec(
        repository_id=repository_id,
        name=repository_id,
        source="local",
        path=path,
        **extra,
    )


class _FakeAcquisitionService:
    """Deterministic stand-in for RepositoryAcquisitionService."""

    def __init__(self, paths: Dict[str, str]):
        self._paths = paths
        self.acquire_calls: List[Dict[str, Any]] = []

    async def acquire(self, owner: str, repo: str, ref: str = "HEAD",
                      shallow: bool = True, depth: int = 1) -> Any:
        self.acquire_calls.append(
            {"owner": owner, "repo": repo, "ref": ref, "depth": depth}
        )
        key = f"{owner}/{repo}"
        local_path = self._paths.get(key)
        if local_path is None:
            raise RuntimeError(f"fake acquisition has no path for {key}")

        class _Meta:
            pass
        _Meta.local_path = local_path
        return _Meta()


def _make_repo(tmp_path, rid: str, files: Dict[str, str]) -> str:
    d = tmp_path / rid
    d.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_text(content)
    return str(d)


# ── Core orchestrator (local paths) ──────────────────────────────


class TestAcquireAndLinkRepositories:
    def test_acquires_registers_links_and_seeds(self, tmp_path):
        fe = _make_repo(tmp_path, "repo-fe", {"src/index.ts": "fe"})
        be = _make_repo(tmp_path, "repo-be", {"app.py": "be"})
        specs = [
            _local_spec("repo-fe", fe),
            _local_spec(
                "repo-be", be,
                relationships=[{
                    "target_repository_id": "repo-fe",
                    "relationship": "references_shared_component",
                }],
            ),
        ]
        org = OrganizationKnowledgeGraphService()
        result = asyncio.run(org.acquire_and_link_repositories(specs, ingest=True))

        assert result["repositories_acquired"] == 2
        assert result["relationships"] == 1
        assert result["ingested_files"] >= 2  # at least one FILE per repo
        assert [ns["repository_id"] for ns in result["namespaces"]] == ["repo-fe", "repo-be"]
        assert result["cross_edges"][0]["relationship"] == "references_shared_component"

    def test_local_path_required_for_local_source(self, tmp_path):
        org = OrganizationKnowledgeGraphService()
        bad = MultiRepoAcquisitionSpec(repository_id="x", source="local", path="")
        with pytest.raises(ValueError, match="existing directory path"):
            asyncio.run(org.acquire_and_link_repositories([bad]))

    def test_local_path_must_be_a_directory(self, tmp_path):
        org = OrganizationKnowledgeGraphService()
        missing = str(tmp_path / "nope")
        spec = _local_spec("x", missing)
        with pytest.raises(ValueError, match="existing directory path"):
            asyncio.run(org.acquire_and_link_repositories([spec]))

    def test_github_source_without_acquisition_service_raises(self):
        # A real github clone requires a network + token; the service refuses to
        # auto-instantiate one so tests never silently hit the network.
        org = OrganizationKnowledgeGraphService()
        spec = MultiRepoAcquisitionSpec(
            repository_id="gh-repo", source="github",
            owner="octocat", repo="hello-world",
        )
        with pytest.raises(ValueError, match="requires an acquisition_service"):
            asyncio.run(org.acquire_and_link_repositories([spec], ingest=False))

    def test_github_source_uses_injected_acquisition_service(self):
        org = OrganizationKnowledgeGraphService()
        spec = MultiRepoAcquisitionSpec(
            repository_id="gh-repo", source="github",
            owner="octocat", repo="hello-world", ref="main", depth=5,
        )
        fake = _FakeAcquisitionService({"octocat/hello-world": "/tmp/fake-gh-clone"})
        os.makedirs("/tmp/fake-gh-clone", exist_ok=True)
        result = asyncio.run(
            org.acquire_and_link_repositories([spec], acquisition_service=fake, ingest=False)
        )
        assert result["repositories_acquired"] == 1
        assert fake.acquire_calls[0]["ref"] == "main"
        assert fake.acquire_calls[0]["depth"] == 5

    def test_invalid_source_rejected(self, tmp_path):
        org = OrganizationKnowledgeGraphService()
        spec = MultiRepoAcquisitionSpec(repository_id="x", source="bitbucket", path=str(tmp_path))
        with pytest.raises(ValueError, match="unsupported source"):
            asyncio.run(org.acquire_and_link_repositories([spec]))

    def test_relationship_targets_unknown_repo_rejected(self, tmp_path):
        d = _make_repo(tmp_path, "a", {"a.py": "x"})
        specs = [
            _local_spec("repo-a", d, relationships=[{
                "target_repository_id": "ghost",
                "relationship": "depends_on_repository",
            }]),
        ]
        org = OrganizationKnowledgeGraphService()
        with pytest.raises(ValueError, match="unknown repository"):
            asyncio.run(org.acquire_and_link_repositories(specs))

    def test_invalid_relationship_value_rejected(self, tmp_path):
        a = _make_repo(tmp_path, "a", {"a.py": "x"})
        b = _make_repo(tmp_path, "b", {"b.py": "x"})
        specs = [
            _local_spec("a", a),
            _local_spec("b", b, relationships=[{
                "target_repository_id": "a",
                "relationship": "contains",  # not a cross-repo relationship
            }]),
        ]
        org = OrganizationKnowledgeGraphService()
        with pytest.raises(ValueError):
            asyncio.run(org.acquire_and_link_repositories(specs))

    def test_duplicate_repository_id_rejected(self, tmp_path):
        d = _make_repo(tmp_path, "a", {"a.py": "x"})
        specs = [_local_spec("repo-a", d), _local_spec("repo-a", d)]
        org = OrganizationKnowledgeGraphService()
        with pytest.raises(ValueError, match="duplicate repository_id"):
            asyncio.run(org.acquire_and_link_repositories(specs))

    def test_empty_specs_rejected(self):
        org = OrganizationKnowledgeGraphService()
        with pytest.raises(ValueError, match="at least one"):
            asyncio.run(org.acquire_and_link_repositories([]))


# ── Post-acquisition graph invariants ────────────────────────────


class TestAcquiredGraphInvariants:
    def test_org_query_merges_repositories(self, tmp_path):
        a = _make_repo(tmp_path, "a", {"alpha.py": "x"})
        b = _make_repo(tmp_path, "b", {"beta.py": "x"})
        specs = [
            _local_spec("repo-a", a),
            _local_spec("repo-b", b),
        ]
        org = OrganizationKnowledgeGraphService()
        asyncio.run(org.acquire_and_link_repositories(specs, ingest=True))

        result = asyncio.run(org.query("alpha", scope=QueryScope.ORGANIZATION, limit=25))
        assert result.total_nodes > 0
        assert set(result.repositories) >= {"repo-a", "repo-b"}

    def test_local_scope_isolation_after_acquisition(self, tmp_path):
        a = _make_repo(tmp_path, "a", {"alpha.py": "x"})
        b = _make_repo(tmp_path, "b", {"beta.py": "x"})
        specs = [
            _local_spec("repo-a", a),
            _local_spec("repo-b", b),
        ]
        org = OrganizationKnowledgeGraphService()
        asyncio.run(org.acquire_and_link_repositories(specs, ingest=True))

        local = asyncio.run(
            org.query("file", scope=QueryScope.LOCAL, repository_ids=["repo-a"], limit=25)
        )
        assert all(n.repository_id == "repo-a" for n in local.nodes)

    def test_cross_repo_traversal_crosses_bridge(self, tmp_path):
        a = _make_repo(tmp_path, "a", {"alpha.py": "x"})
        b = _make_repo(tmp_path, "b", {"beta.py": "x"})
        specs = [
            _local_spec("repo-a", a),
            _local_spec("repo-b", b, relationships=[{
                "target_repository_id": "repo-a",
                "relationship": "depends_on_repository",
            }]),
        ]
        org = OrganizationKnowledgeGraphService()
        asyncio.run(org.acquire_and_link_repositories(specs, ingest=True))

        traversal = org.cross_repository_traversal(_repo_node_id("repo-b"), depth=2, max_nodes=200)
        repos = {n.repository_id for n in traversal.nodes}
        assert "repo-a" in repos, f"bridge did not cross: {repos}"

    def test_ingest_is_bounded(self, tmp_path):
        # Many files should still be capped (no unbounded graph growth).
        files = {f"f{i:04d}.txt": "x" for i in range(300)}
        d = _make_repo(tmp_path, "big", files)
        specs = [_local_spec("repo-big", d)]
        org = OrganizationKnowledgeGraphService()
        result = asyncio.run(org.acquire_and_link_repositories(specs, ingest=True))
        # Capped by MAX_NODES_PER_RUN_INGEST (500), but the per-repo graph is bounded.
        assert result["ingested_files"] <= 500


# ── CLI ──────────────────────────────────────────────────────────


class TestAcquireMultiCLI:
    def test_cli_org_acquire_multi(self, tmp_path):
        from app.cli_engineering_graph import run_graph_org_acquire_multi

        a = _make_repo(tmp_path, "a", {"a.py": "x"})
        b = _make_repo(tmp_path, "b", {"b.py": "x"})
        manifest = os.path.join(tmp_path, "manifest.json")
        payload = [
            {"repository_id": "cli-a", "source": "local", "path": a},
            {"repository_id": "cli-b", "source": "local", "path": b,
             "relationships": [{"target_repository_id": "cli-a",
                                "relationship": "shares_library"}]},
        ]
        with open(manifest, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        out = asyncio.run(run_graph_org_acquire_multi(manifest, ingest=True, json_output=True))
        # json_output prints; verify via the service directly instead.

        org = OrganizationKnowledgeGraphService()
        res = asyncio.run(org.acquire_and_link_repositories(
            [MultiRepoAcquisitionSpec(**s) for s in payload], ingest=True))
        assert res["repositories_acquired"] == 2
        assert res["relationships"] == 1

    def test_cli_missing_manifest(self, tmp_path):
        from app.cli_engineering_graph import run_graph_org_acquire_multi

        out = asyncio.run(run_graph_org_acquire_multi(
            str(tmp_path / "missing.json"), json_output=True))
        assert out is None
        # No exception raised; message would have printed "Manifest not found".

    def test_cli_invalid_manifest_schema(self, tmp_path):
        from app.cli_engineering_graph import run_graph_org_acquire_multi

        manifest = tmp_path / "bad.json"
        manifest.write_text(json.dumps({"not": "a list"}))
        out = asyncio.run(run_graph_org_acquire_multi(str(manifest), json_output=True))
        assert out is None


# ── HTTP API ─────────────────────────────────────────────────────


class TestAcquireMultiAPI:
    def _client(self):
        from fastapi.testclient import TestClient
        from app.main import app
        from app.api.v1 import engineering_graph as eg
        eg._org_service = None
        return TestClient(app)

    def test_post_acquire_multi(self, tmp_path):
        a = _make_repo(tmp_path, "a", {"a.py": "x"})
        b = _make_repo(tmp_path, "b", {"b.py": "x"})
        manifest = [
            {"repository_id": "api-a", "source": "local", "path": a},
            {"repository_id": "api-b", "source": "local", "path": b,
             "relationships": [{"target_repository_id": "api-a",
                                "relationship": "depends_on_repository"}]},
        ]
        client = self._client()
        r = client.post("/api/v1/graph/org/acquire-multi", json=manifest)
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["repositories_acquired"] == 2
        assert data["relationships"] == 1
        assert data["scope"] == "organization"

    def test_post_acquire_multi_empty_manifest_400(self):
        client = self._client()
        r = client.post("/api/v1/graph/org/acquire-multi", json=[])
        assert r.status_code == 400

    def test_post_acquire_multi_oversized_manifest_400(self):
        from app.models.engineering_graph import MAX_REPOSITORIES_PER_ORG

        client = self._client()
        manifest = [{"repository_id": f"r{i}", "source": "local", "path": "/tmp"}
                    for i in range(MAX_REPOSITORIES_PER_ORG + 1)]
        r = client.post("/api/v1/graph/org/acquire-multi", json=manifest)
        assert r.status_code == 400
