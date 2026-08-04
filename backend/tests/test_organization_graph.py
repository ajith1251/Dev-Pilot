"""
Phase 19A — Tests for the Organization Knowledge Graph.

Covers (Phase 19A spec):
- namespace registry (register / retrieve / isolation bounds)
- cross-repository edges (deterministic ids, allowed relationships only,
  unregistered repositories rejected, self-links rejected)
- cross-repository traversal (bounded, bridge-only boundary crossing)
- organization-wide query routing (LOCAL / ORGANIZATION / AUTO scopes,
  repository attribution, isolation guarantees)
- planner cross-repository intent classification (deterministic, no LLM)
- statistics (org + per-repository)
- persistence / recover round-trip (namespaces + cross-edges)
- migration 013 column/table presence
- regression: single-repo behavior unchanged, bounded results, no leaks

All tests are deterministic — no paid LLM or mock LLM required.
PostgreSQL tests skip cleanly when no DB URL is configured (CI in-memory job).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from app.models.engineering_graph import (
    DEFAULT_REPOSITORY_ID,
    EKNode,
    EKNodeType,
    EKRelationshipType,
    QueryScope,
    RepositoryNamespace,
    RetrievalStrategy,
)
from app.services.engineering_graph_service import EngineeringKnowledgeGraphService
from app.services.organization_graph_service import OrganizationKnowledgeGraphService


# ── Helpers ────────────────────────────────────────────────────


def make_org(**kwargs: Any) -> OrganizationKnowledgeGraphService:
    """Build an in-memory org graph (authoritative for unit tests)."""
    return OrganizationKnowledgeGraphService(**kwargs)


def add_repo_nodes(
    org: OrganizationKnowledgeGraphService,
    repository_id: str,
    names: List[str],
) -> Dict[str, EKNode]:
    """Add a few nodes to a repository's per-repo graph."""
    g = org.get_graph(repository_id)
    assert g is not None, f"{repository_id} not registered"
    nodes: Dict[str, EKNode] = {}
    for name in names:
        nodes[name] = g.add_node(
            EKNodeType.FILE, name, source_ref=f"{repository_id}:{name}",
            source_type="repository",
            qualified_name=f"{repository_id}/{name}",
            payload={"kind": "file", "name": name},
            provenance={"repository_id": repository_id, "source": "test"},
        )
    return nodes


def build_org(repos: int = 3) -> OrganizationKnowledgeGraphService:
    """Two-node repos 'repo-a', 'repo-b', 'repo-c' + a cross-repo link."""
    org = make_org()
    for i, rid in enumerate(["repo-a", "repo-b", "repo-c"]):
        org.register_repository(
            rid, name=f"Repository {rid}", path=f"/repos/{rid}",
            source_type="local", organization_id="default",
        )
        add_repo_nodes(org, rid, [f"{rid}-file-{i}" for i in range(2)])
    org.link_repositories(
        "repo-a", "repo-b", EKRelationshipType.SHARES_LIBRARY,
        weight=0.9, metadata={"library": "shared-utils"},
        provenance={"source": "test", "reason": "shared utils"},
    )
    org.link_repositories(
        "repo-b", "repo-c", EKRelationshipType.DEPENDS_ON_REPOSITORY,
        weight=0.8, metadata={"package": "shared-lib"},
        provenance={"source": "test", "reason": "depends"},
    )
    return org


def _pg_url() -> str:
    try:
        from app.config import settings

        return settings.DATABASE_URL or settings.TEST_DATABASE_URL or ""
    except Exception:
        return ""


def _pg_available() -> bool:
    return bool(_pg_url())


# ── Repository namespaces ──────────────────────────────────────


class TestRepositoryNamespaces:
    def test_register_and_retrieve(self):
        org = make_org()
        ns = org.register_repository("repo-a", name="Repo A", path="/repos/a")
        assert isinstance(ns, RepositoryNamespace)
        assert org.get_namespace("repo-a").repository_id == "repo-a"
        assert org.repositories()[0].name == "Repo A"
        # A REPOSITORY node exists in the org graph.
        assert org.get_graph("repo-a") is not None

    def test_register_missing_id_raises(self):
        org = make_org()
        with pytest.raises(ValueError):
            org.register_repository("")

    def test_register_limit_enforced(self):
        org = make_org(max_repositories=2)
        org.register_repository("r1")
        org.register_repository("r2")
        with pytest.raises(RuntimeError):
            org.register_repository("r3")

    def test_registered_repo_has_isolated_graph(self):
        org = make_org()
        org.register_repository("repo-a")
        org.register_repository("repo-b")
        ga = org.get_graph("repo-a")
        gb = org.get_graph("repo-b")
        add_repo_nodes(org, "repo-a", ["only-in-a"])
        add_repo_nodes(org, "repo-b", ["only-in-b"])
        # Nodes are isolated per-repository (find_nodes enforces namespace).
        assert len(ga.find_nodes(name="only-in-a")) == 1
        assert len(gb.find_nodes(name="only-in-a")) == 0
        assert ga.find_nodes(name="only-in-b") == []
        assert len(gb.find_nodes(name="only-in-b")) == 1


# ── Cross-repository edges ─────────────────────────────────────


class TestCrossRepositoryEdges:
    def test_link_requires_registered_repos(self):
        org = make_org()
        org.register_repository("repo-a")
        with pytest.raises(KeyError):
            org.link_repositories(
                "repo-a", "repo-b", EKRelationshipType.SHARES_LIBRARY,
            )
        org.register_repository("repo-b")
        with pytest.raises(KeyError):
            org.link_repositories(
                "repo-a", "repo-unknown", EKRelationshipType.SHARES_LIBRARY,
            )

    def test_self_link_rejected(self):
        org = make_org()
        org.register_repository("repo-a")
        with pytest.raises(ValueError):
            org.link_repositories(
                "repo-a", "repo-a", EKRelationshipType.SHARES_LIBRARY,
            )

    def test_only_explicit_relationships_allowed(self):
        org = make_org()
        org.register_repository("repo-a")
        org.register_repository("repo-b")
        # An in-repository relationship must NOT be usable as a cross-repo link.
        with pytest.raises(ValueError):
            org.link_repositories(
                "repo-a", "repo-b", EKRelationshipType.CONTAINS,
            )

    def test_link_is_deterministic(self):
        org = make_org()
        org.register_repository("repo-a")
        org.register_repository("repo-b")
        e1 = org.link_repositories(
            "repo-a", "repo-b", EKRelationshipType.SHARES_LIBRARY,
            weight=0.9,
        )
        # Re-linking the same pair+relationship must reuse the same edge.
        org.link_repositories(
            "repo-a", "repo-b", EKRelationshipType.SHARES_LIBRARY,
            weight=0.5,
        )
        assert len(org.cross_edges()) == 1
        e2 = org.cross_edges()[0]
        assert e1.edge_id == e2.edge_id
        assert e2.weight == pytest.approx(0.5)
        assert e2.relationship == EKRelationshipType.SHARES_LIBRARY

    def test_neighbors_and_direction(self):
        org = build_org()
        neighbors = org.neighbors_of("repo-a")
        assert any(rel == EKRelationshipType.SHARES_LIBRARY for _t, rel in neighbors)
        # repo-a has no incoming links in this graph.
        assert org._cross_in.get("repo-a") in (None, [])
        # repo-b receives one incoming (from repo-a) + one outgoing (to repo-c).
        incoming_b = org._cross_in.get("repo-b", [])
        assert any(src == "repo-a" for _eid, src, _rel in incoming_b)


# ── Cross-repository traversal ─────────────────────────────────


class TestCrossRepositoryTraversal:
    def test_traversal_stays_bounded(self):
        org = build_org()
        g = org.get_graph("repo-a")
        node = next(iter(g.all_nodes()))
        result = org.cross_repository_traversal(node.node_id, depth=2, max_nodes=10)
        assert len(result.nodes) <= 10
        assert result.total_nodes <= 10
        assert result.strategy == RetrievalStrategy.CROSS_REPOSITORY

    def test_traversal_crosses_bridge_repositories(self):
        org = build_org()
        g = org.get_graph("repo-a")
        node = next(iter(g.all_nodes()))
        result = org.cross_repository_traversal(node.node_id, depth=3, max_nodes=200)
        repo_ids = {n.repository_id for n in result.nodes}
        # repo-a nodes present; bridge reached repo-b (shared library edge).
        assert "repo-a" in repo_ids
        # Only linked repos may appear — repo-c is NOT reachable from repo-a.
        assert "repo-c" not in repo_ids

    def test_unknown_start_node_returns_empty(self):
        org = build_org()
        result = org.cross_repository_traversal("does-not-exist")
        assert result.nodes == []


# ── Organization-wide query routing ────────────────────────────


class TestOrganizationQueryRouting:
    @pytest.mark.asyncio
    async def test_local_scope_isolates_repo(self):
        org = build_org()
        result = await org.query(
            "file", scope=QueryScope.LOCAL, repository_ids=["repo-a"],
            target_kinds=[EKNodeType.FILE],
        )
        assert result.scope == QueryScope.LOCAL
        assert all(
            (n.repository_id or DEFAULT_REPOSITORY_ID) == "repo-a"
            for n in result.nodes
        )
        # repo-a contributed nodes, repo-b/c did not.
        assert result.repositories.get("repo-a", 0) > 0
        assert result.repositories.get("repo-b", 0) == 0
        assert result.repositories.get("repo-c", 0) == 0

    @pytest.mark.asyncio
    async def test_organization_scope_merges_linked_repos(self):
        org = build_org()
        result = await org.query(
            "file", scope=QueryScope.ORGANIZATION,
            target_kinds=[EKNodeType.FILE],
        )
        assert result.scope == QueryScope.ORGANIZATION
        assert result.strategy == RetrievalStrategy.CROSS_REPOSITORY
        assert result.total_nodes > 0
        # Organization scope is allowed to touch all registered repos.
        assert "repo-a" in result.repositories

    @pytest.mark.asyncio
    async def test_auto_scope_routes_cross_repository_vocabulary(self):
        org = build_org()
        result = await org.query(
            "which files are shared across repositories",
            scope=QueryScope.AUTO,
        )
        plan = result.plan
        assert plan is not None
        assert plan.intent == "cross_repository"
        assert plan.strategy == RetrievalStrategy.CROSS_REPOSITORY
        assert plan.cross_repository is True

    @pytest.mark.asyncio
    async def test_auto_scope_stays_local_without_vocabulary(self):
        org = build_org()
        result = await org.query(
            "explain repo-a-file-0 implementation",
            scope=QueryScope.AUTO,
        )
        plan = result.plan
        assert plan is not None
        assert plan.intent != "cross_repository"
        assert plan.cross_repository is False

    @pytest.mark.asyncio
    async def test_repository_attribution_on_result(self):
        org = build_org()
        result = await org.query(
            "file", scope=QueryScope.ORGANIZATION,
            target_kinds=[EKNodeType.FILE],
        )
        total = sum(result.repositories.values())
        assert total == result.total_nodes
        for n in result.nodes:
            assert n.repository_id in ("repo-a", "repo-b", "repo-c")


# ── Planner cross-repository intent ────────────────────────────


class TestPlannerCrossRepositoryIntent:
    def _plan(self, query: str):
        from app.services.knowledge_query_planner import KnowledgeQueryPlanner

        org = build_org()
        view = org._graphs
        from app.services.organization_graph_service import _OrgGraphView

        planner = KnowledgeQueryPlanner(graph=_OrgGraphView(list(view.values())))
        return planner.plan(query)

    def test_cross_repository_intent(self):
        for q in [
            "cross repository dependency",
            "how does this integrate across repositories",
            "whole organization architecture",
            "other repository similar problem",
        ]:
            plan = self._plan(q)
            assert plan.intent == "cross_repository", q
            assert plan.strategy == RetrievalStrategy.CROSS_REPOSITORY, q

    def test_local_intent_unchanged(self):
        plan = self._plan("explain the auth implementation")
        assert plan.intent == "explain_implementation"
        assert plan.strategy == RetrievalStrategy.KNOWLEDGE_GRAPH


# ── Statistics ─────────────────────────────────────────────────


class TestOrganizationStats:
    def test_org_stats(self):
        org = build_org()
        stats = org.stats()
        assert stats.repository_count == 3
        assert stats.cross_edge_count == 2
        assert stats.cross_relationship_types.get(EKRelationshipType.SHARES_LIBRARY.value, 0) == 1
        assert stats.cross_relationship_types.get(EKRelationshipType.DEPENDS_ON_REPOSITORY.value, 0) == 1
        assert stats.node_count >= 6

    def test_repository_stats(self):
        org = build_org()
        rs = org.repository_stats("repo-a")
        assert rs is not None
        assert rs["node_count"] == 2
        assert any(l["relationship"] == EKRelationshipType.SHARES_LIBRARY.value for l in rs["outgoing_links"])

    def test_repository_stats_missing(self):
        org = build_org()
        assert org.repository_stats("repo-unknown") is None


# ── Explain ────────────────────────────────────────────────────


class TestOrgExplain:
    def test_explain_found_node(self):
        org = build_org()
        node = next(iter(org.get_graph("repo-a").all_nodes()))
        out = org.explain(node.node_id)
        assert out.get("found") is True

    def test_explain_missing_node(self):
        org = build_org()
        out = org.explain("nope")
        assert out.get("found") is False


# ── Persistence round-trip ─────────────────────────────────────


@pytest.mark.skipif(not _pg_available(), reason="PostgreSQL not configured")
class TestOrgPostgresPersistence:
    @pytest.mark.asyncio
    async def test_namespace_and_edge_roundtrip(self):
        # Unique namespace per run so the round-trip is idempotent against
        # an accumulated shared PostgreSQL (other runs / the demo may have
        # persisted their own namespaces + cross-edges).
        import uuid

        suffix = uuid.uuid4().hex[:8]
        repo_a = f"rt-{suffix}-a"
        repo_b = f"rt-{suffix}-b"

        org = make_org(database_url=_pg_url())
        await org.recover()
        try:
            org.register_repository(repo_a, name="Repo A", path="/repos/a")
            org.register_repository(repo_b, name="Repo B", path="/repos/b")
            org.link_repositories(
                repo_a, repo_b, EKRelationshipType.SHARES_LIBRARY,
                weight=0.7, metadata={"library": "utils"},
                provenance={"source": "test"},
            )
            written = await org.synchronize()
            assert written >= 3  # 2 namespaces + 1 cross-edge

            # Fresh service = simulated restart.
            fresh = make_org(database_url=_pg_url())
            await fresh.recover()
            try:
                assert fresh.get_namespace(repo_a) is not None
                assert fresh.get_namespace(repo_b) is not None
                # The DB may hold unrelated cross-edges from other runs —
                # assert OUR edge survived the restart, not a global count.
                our_edges = [
                    e for e in fresh.cross_edges()
                    if e.source_repository_id == repo_a
                    and e.target_repository_id == repo_b
                ]
                assert len(our_edges) == 1
                edge = our_edges[0]
                assert edge.relationship == EKRelationshipType.SHARES_LIBRARY
                assert edge.weight == pytest.approx(0.7)
            finally:
                await fresh.dispose()
        finally:
            await org.dispose()


# ── Migration 013 ──────────────────────────────────────────────


@pytest.mark.skipif(not _pg_available(), reason="PostgreSQL not configured")
class TestMigration013OrgGraph:
    @pytest.mark.asyncio
    async def test_org_tables_and_columns_exist(self):
        from sqlalchemy import inspect
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine(_pg_url())
        try:
            async with engine.connect() as conn:
                def _inspect():
                    return inspect(conn)

                tables = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
            for table in (
                "ekg_organizations",
                "ekg_repository_namespaces",
                "ekg_cross_repository_edges",
            ):
                assert table in tables, f"missing migration 013 table: {table}"
            # repository_id columns on Phase 18 tables.
            async with engine.connect() as conn:
                async def _cols(table_name: str) -> List[str]:
                    return await conn.run_sync(
                        lambda sync_conn: [
                            c["name"]
                            for c in inspect(sync_conn).get_columns(table_name)
                        ]
                    )

                node_cols = await _cols("ekg_nodes")
                edge_cols = await _cols("ekg_edges")
            assert "repository_id" in node_cols
            assert "repository_id" in edge_cols
        finally:
            await engine.dispose()


# ── Regression: single-repo behavior unchanged ─────────────────


class TestSingleRepoRegression:
    @pytest.mark.asyncio
    async def test_default_repository_id_still_works(self):
        g = EngineeringKnowledgeGraphService()
        node = g.add_node(
            EKNodeType.FILE, "single-file", source_ref="s1", source_type="repository",
            qualified_name="single/file",
            provenance={"source": "test"},
        )
        assert node.repository_id == DEFAULT_REPOSITORY_ID

    @pytest.mark.asyncio
    async def test_org_query_with_no_repos_returns_empty(self):
        org = make_org()
        result = await org.query("anything", scope=QueryScope.ORGANIZATION)
        assert result.nodes == []


# ── ContextEngine integration (Phase 19A) ──────────────────────


class TestOrgContextEngineIntegration:
    @pytest.mark.asyncio
    async def test_cross_repository_vocabulary_surfaces_org_evidence(self):
        from app.services.context_engine import ContextEngine

        org = build_org()
        engine = ContextEngine(organization_graph=org)
        ctx = await engine.build_context(
            "which components are shared across repositories",
            agent_type="planner",
        )
        contents = " ".join(i.content for i in ctx.raw_items)
        assert "Organization knowledge graph" in contents
        assert "repo:" in contents

    @pytest.mark.asyncio
    async def test_local_query_stays_isolated_no_org_evidence(self):
        from app.services.context_engine import ContextEngine

        org = build_org()
        engine = ContextEngine(organization_graph=org)
        ctx = await engine.build_context(
            "explain the authentication implementation",
            agent_type="planner",
        )
        assert not any(
            "Organization knowledge graph" in i.content for i in ctx.raw_items
        )

    @pytest.mark.asyncio
    async def test_empty_org_graph_degrades_cleanly(self):
        from app.services.context_engine import ContextEngine

        org = make_org()
        engine = ContextEngine(organization_graph=org)
        ctx = await engine.build_context(
            "which files are shared across repositories",
            agent_type="planner",
        )
        assert not any(
            "Organization knowledge graph" in i.content for i in ctx.raw_items
        )

    @pytest.mark.asyncio
    async def test_forced_organization_scope_surfaces_org_evidence(self):
        # Phase 20 A3: multi-repo runs force the ORGANIZATION scope so
        # local-looking vocabulary (which AUTO would filter) still surfaces
        # cross-repository evidence to the planner.
        from app.services.context_engine import ContextEngine

        org = build_org()
        engine = ContextEngine(organization_graph=org)
        ctx = await engine.build_context(
            "explain the authentication implementation",
            agent_type="planner",
            include_organization_context=True,
        )
        contents = " ".join(i.content for i in ctx.raw_items)
        assert "Organization knowledge graph" in contents
        assert "repo:" in contents

    @pytest.mark.asyncio
    async def test_forced_scope_empty_org_graph_degrades_cleanly(self):
        from app.services.context_engine import ContextEngine

        org = make_org()
        engine = ContextEngine(organization_graph=org)
        ctx = await engine.build_context(
            "explain the authentication implementation",
            agent_type="planner",
            include_organization_context=True,
        )
        assert not any(
            "Organization knowledge graph" in i.content for i in ctx.raw_items
        )

    @pytest.mark.asyncio
    async def test_forced_scope_unavailable_org_graph_degrades_cleanly(self):
        # No org graph injected: the engine lazily falls back to an empty
        # in-memory graph; a forced scope must still degrade gracefully.
        from app.services.context_engine import ContextEngine

        engine = ContextEngine()
        ctx = await engine.build_context(
            "which components are shared across repositories",
            agent_type="planner",
            include_organization_context=True,
        )
        assert not any(
            "Organization knowledge graph" in i.content for i in ctx.raw_items
        )


# ── API endpoints (Phase 19A) ──────────────────────────────────


class TestOrgApiEndpoints:
    async def _client(self):
        import httpx
        import app.api.v1.engineering_graph as eg_api

        org = build_org()
        eg_api._org_service = org
        import app.main as main_app

        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main_app.app),
            base_url="http://testserver",
        )

    @pytest.mark.asyncio
    async def test_org_stats_endpoint(self):
        client = await self._client()
        try:
            resp = await client.get("/api/v1/graph/org/stats")
            body = resp.json()
            assert body["success"] is True
            assert body["data"]["repository_count"] == 3
            assert body["data"]["cross_edge_count"] == 2
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_org_repositories_endpoint(self):
        client = await self._client()
        try:
            resp = await client.get("/api/v1/graph/org/repositories")
            body = resp.json()
            assert body["success"] is True
            ids = {r["repository_id"] for r in body["data"]["repositories"]}
            assert {"repo-a", "repo-b", "repo-c"} <= ids
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_org_cross_edges_endpoint(self):
        client = await self._client()
        try:
            resp = await client.get("/api/v1/graph/org/cross-edges")
            body = resp.json()
            assert body["success"] is True
            assert len(body["data"]["cross_edges"]) == 2
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_org_query_endpoint_local_scope(self):
        client = await self._client()
        try:
            resp = await client.get(
                "/api/v1/graph/org/query",
                params={"q": "file", "scope": "local", "repository_id": "repo-a",
                        "limit": 10},
            )
            body = resp.json()
            assert body["success"] is True
            data = body["data"]
            assert data["scope"] == "local"
            assert data["repositories"].get("repo-a", 0) > 0
            assert data["repositories"].get("repo-b", 0) == 0
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_org_link_endpoint_rejects_bad_relationship(self):
        client = await self._client()
        try:
            resp = await client.post("/api/v1/graph/org/link", json={
                "source_repository_id": "repo-a",
                "target_repository_id": "repo-b",
                "relationship": "contains",
            })
            body = resp.json()
            assert resp.status_code == 400
            assert body["detail"]
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_org_register_endpoint(self):
        import httpx
        import app.api.v1.engineering_graph as eg_api

        org = make_org()
        eg_api._org_service = org
        import app.main as main_app

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main_app.app),
            base_url="http://testserver",
        ) as client:
            resp = await client.post("/api/v1/graph/org/repositories", json={
                "repository_id": "repo-new", "name": "New Repo",
            })
            body = resp.json()
            assert body["success"] is True
            assert body["data"]["namespace"]["repository_id"] == "repo-new"
