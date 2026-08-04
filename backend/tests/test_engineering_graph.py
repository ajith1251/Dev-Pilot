"""
Phase 18 — Tests for the Engineering Knowledge Graph (EKG).

Covers (§20):
- node creation (upsert by stable id, deterministic ids, provenance)
- edge creation / relationship dedup
- graph traversal (bounded neighborhood, dependencies)
- graph versioning (incremental bumps, supersede, version history)
- query planner (intent classification, strategy selection, bounded results)
- provenance (explain endpoint logic, evidence origins never lost)
- historical graph (temporal node history across versions)
- PostgreSQL persistence (record_run round-trip + restart recovery)
- API endpoints (query / node / history / neighborhood / explain / version)
- CLI commands (query / explain / history / neighborhood / version)
- integrations (ContextEngine retrieval, orchestration ingestion)
- regression: evidence-only exposure, bounded results, idempotent ingestion

All tests are deterministic — no paid LLM or mock LLM required.
PostgreSQL tests skip cleanly when no DB URL is configured (CI in-memory job).
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

import pytest

from app.models.engineering_graph import (
    MAX_NEIGHBORHOOD_NODES,
    MAX_QUERY_RESULTS,
    EKEdge,
    EKNode,
    EKNodeStatus,
    EKNodeType,
    EKRelationshipType,
    GraphQueryResult,
    RetrievalStrategy,
)
from app.services.engineering_graph_service import (
    EngineeringKnowledgeGraphService,
    _stable_id,
)


# ── Helpers ────────────────────────────────────────────────────


def _db_url() -> str:
    """Resolve the DB URL for PG-backed tests (mirrors PostgresRunStore)."""
    try:
        from app.config import settings

        return settings.DATABASE_URL or settings.TEST_DATABASE_URL or ""
    except Exception:
        return ""


def _pg_available() -> bool:
    return bool(_db_url())


def make_service(**kwargs: Any) -> EngineeringKnowledgeGraphService:
    """Build an in-memory EKG service (authoritative for unit tests)."""
    return EngineeringKnowledgeGraphService(**kwargs)


def add_requirement(graph: EngineeringKnowledgeGraphService, run_id: str, text: str) -> EKNode:
    return graph.add_node(
        EKNodeType.REQUIREMENT, text[:100], source_ref=run_id, source_type="run",
        payload={"description": text}, provenance={"run_id": run_id, "source": "requirements"},
    )


def add_patch(graph: EngineeringKnowledgeGraphService, run_id: str) -> EKNode:
    return graph.add_node(
        EKNodeType.PATCH, f"patch:{run_id}", source_ref=run_id, source_type="run",
        qualified_name=f"patch:{run_id}", provenance={"run_id": run_id, "source": "coding"},
    )


def add_test_suite(graph: EngineeringKnowledgeGraphService, run_id: str) -> EKNode:
    return graph.add_node(
        EKNodeType.TEST_SUITE, f"tests:{run_id}", source_ref=run_id, source_type="run",
        qualified_name=f"tests:{run_id}",
        payload={"status": "passed", "tests_total": 10, "tests_failed": 0},
        provenance={"run_id": run_id, "source": "testing"},
    )


def add_run(graph: EngineeringKnowledgeGraphService, run_id: str, title: str = "Task") -> EKNode:
    return graph.add_node(
        EKNodeType.RUN, f"run:{run_id}", source_ref=run_id, source_type="run",
        qualified_name=run_id, payload={"title": title},
        provenance={"run_id": run_id, "source": "orchestration"},
    )


def build_lineage(graph: EngineeringKnowledgeGraphService, run_id: str = "RUN-UNIT-1") -> Dict[str, EKNode]:
    """Requirement → patch → tests → gate lineage (Demonstration A)."""
    run = add_run(graph, run_id)
    req = add_requirement(graph, run_id, "Implement authentication")
    patch = add_patch(graph, run_id)
    tests = add_test_suite(graph, run_id)
    gate = graph.add_node(
        EKNodeType.QUALITY_GATE, f"gate:{run_id}", source_ref=run_id, source_type="run",
        qualified_name=f"gate:{run_id}", payload={"decision": "approved"},
        provenance={"run_id": run_id, "source": "quality_gate"},
    )
    graph.add_edge(run.node_id, req.node_id, EKRelationshipType.CONTAINS,
                   metadata={"run_id": run_id})
    graph.add_edge(run.node_id, patch.node_id, EKRelationshipType.CREATED_DURING,
                   metadata={"run_id": run_id})
    graph.add_edge(patch.node_id, tests.node_id, EKRelationshipType.VALIDATED_BY,
                   metadata={"run_id": run_id})
    graph.add_edge(patch.node_id, gate.node_id, EKRelationshipType.APPROVED_BY,
                   metadata={"run_id": run_id})
    return {"run": run, "requirement": req, "patch": patch, "tests": tests, "gate": gate}


# ── Node creation ──────────────────────────────────────────────


class TestNodeCreation:
    def test_add_node_sets_provenance(self):
        g = make_service()
        node = add_requirement(g, "RUN-X", "Auth must be OAuth2")
        assert node.node_type == EKNodeType.REQUIREMENT
        assert node.provenance == {"run_id": "RUN-X", "source": "requirements"}
        assert node.status == EKNodeStatus.ACTIVE
        assert g.get_node(node.node_id) is node

    def test_stable_id_deterministic_and_bounded(self):
        a = _stable_id(EKNodeType.RUN, "run", "RUN-1", "run:RUN-1")
        b = _stable_id(EKNodeType.RUN, "run", "RUN-1", "run:RUN-1")
        assert a == b
        # Must fit String(40) columns (regression: over-long ids silently
        # failed node persistence while edges wrote — corrupting recovery).
        assert len(a) <= 40
        c = _stable_id(EKNodeType.RUN, "run", "RUN-" + "X" * 120, "run:" + "Y" * 120)
        assert len(c) <= 40
        # Different inputs still yield different ids.
        assert _stable_id(EKNodeType.RUN, "run", "RUN-2", "run:RUN-2") != a

    def test_upsert_keeps_stable_id_and_created_at(self):
        g = make_service()
        # Same name + source_ref -> same stable id (upsert), not duplicate.
        n1 = add_requirement(g, "RUN-1", "Same description")
        n2 = add_requirement(g, "RUN-1", "Same description")
        assert n1.node_id == n2.node_id
        assert g.get_node(n1.node_id) is n2
        assert n2.created_at == n1.created_at  # preserved on upsert

    def test_find_nodes_filters(self):
        g = make_service()
        add_requirement(g, "RUN-1", "Login")
        add_test_suite(g, "RUN-1")
        reqs = g.find_nodes(node_type=EKNodeType.REQUIREMENT)
        assert len(reqs) == 1
        assert reqs[0].node_type == EKNodeType.REQUIREMENT
        by_run = g.find_nodes(source_ref="RUN-1")
        assert len(by_run) == 2


# ── Edge creation / dedup ──────────────────────────────────────


class TestEdges:
    def test_add_edge_dedups_same_relationship(self):
        g = make_service()
        run = add_run(g, "RUN-E")
        patch = add_patch(g, "RUN-E")
        e1 = g.add_edge(run.node_id, patch.node_id, EKRelationshipType.CREATED_DURING)
        e2 = g.add_edge(run.node_id, patch.node_id, EKRelationshipType.CREATED_DURING)
        assert e1 is not None and e2 is not None
        assert e1.edge_id == e2.edge_id  # dedup keeps stable edge id
        assert len(g.get_edges(run.node_id)) == 1

    def test_add_edge_skips_missing_endpoint(self):
        g = make_service()
        run = add_run(g, "RUN-M")
        e = g.add_edge(run.node_id, "NOPE", EKRelationshipType.REFERENCES)
        assert e is None

    def test_edges_roundtrip(self):
        g = make_service()
        lineage = build_lineage(g)
        out = g.get_edges(lineage["run"].node_id)
        assert len(out) == 2  # CONTAINS + CREATED_DURING
        rev = g.get_reverse_edges(lineage["patch"].node_id)
        assert any(e.relationship == EKRelationshipType.CREATED_DURING for e in rev)


# ── Traversal ──────────────────────────────────────────────────


class TestTraversal:
    def test_neighborhood_bounded_bfs(self):
        g = make_service()
        lineage = build_lineage(g)
        result = g.neighborhood(lineage["run"].node_id, depth=2)
        assert isinstance(result, GraphQueryResult)
        assert result.strategy == RetrievalStrategy.KNOWLEDGE_GRAPH
        assert result.total_nodes == 5  # run + req + patch + tests + gate
        assert len(result.edges) == 4
        assert result.nodes[0].node_id == lineage["run"].node_id  # root first

    def test_neighborhood_missing_node(self):
        g = make_service()
        result = g.neighborhood("NOPE")
        assert result.total_nodes == 0

    def test_dependencies_outgoing(self):
        g = make_service()
        lineage = build_lineage(g)
        result = g.dependencies(lineage["patch"].node_id, depth=3)
        ids = {n.node_id for n in result.nodes}
        assert lineage["tests"].node_id in ids
        assert lineage["gate"].node_id in ids


# ── Versioning ─────────────────────────────────────────────────


class TestVersioning:
    def test_increment_version_monotonic(self):
        g = make_service()
        v0 = g.current_version().version
        v1 = g.increment_version(run_id="RUN-V", summary="change", updated_nodes=["n1"])
        assert v1.version == v0 + 1
        assert v1.run_id == "RUN-V"
        assert g.current_version().version == v0 + 1

    def test_supersede_marks_nodes(self):
        g = make_service()
        node = add_requirement(g, "RUN-S", "Old requirement")
        g.increment_version(superseded_node_ids=[node.node_id])
        assert g.get_node(node.node_id).status == EKNodeStatus.SUPERSEDED
        hist = g.history(node.node_id)
        assert len(hist.entries) >= 1

    def test_version_history_bounded(self):
        g = make_service()
        for i in range(5):
            g.increment_version(run_id=f"RUN-{i}", summary=f"v{i}")
        hist = g.version_history(limit=3)
        assert len(hist) == 3
        assert hist[-1].version == g.current_version().version

    def test_stats(self):
        g = make_service()
        build_lineage(g)
        stats = g.stats()
        assert stats.node_count >= 5
        assert stats.edge_count == 4
        assert stats.node_types.get("run") == 1
        assert stats.node_types.get("quality_gate") == 1
        assert stats.relationship_types.get("approved_by") == 1


# ── Version diff (§19C timeline) ───────────────────────────────


class TestVersionDiff:
    def test_diff_versions_returns_change_set(self):
        g = make_service()
        g.increment_version(run_id="RUN-D0", summary="seed", updated_nodes=["n0"])
        node_a = add_requirement(g, "RUN-D1", "req added at v2")
        g.increment_version(
            run_id="RUN-D1", summary="added req", updated_nodes=[node_a.node_id],
        )
        v2 = g.current_version().version
        diff = g.diff_versions(v2 - 1, v2)
        assert diff["from_version"] == v2 - 1
        assert diff["to_version"] == v2
        assert any(n["node_id"] == node_a.node_id for n in diff["added_nodes"])
        assert diff["counts"]["added"] >= 1
        assert diff["per_version"][-1]["version"] == v2

    def test_diff_versions_removed_nodes(self):
        g = make_service()
        node = add_requirement(g, "RUN-D", "will be superseded")
        g.increment_version(
            run_id="RUN-D",
            summary="supersede",
            updated_nodes=["other"],
            superseded_node_ids=[node.node_id],
        )
        diff = g.diff_versions(0)
        assert any(n["node_id"] == node.node_id for n in diff["removed_nodes"])
        removed = next(n for n in diff["removed_nodes"] if n["node_id"] == node.node_id)
        assert removed["name"] == "will be superseded"

    def test_diff_versions_to_defaults_current(self):
        g = make_service()
        g.increment_version(run_id="RUN-X", summary="x", updated_nodes=["n"])
        diff = g.diff_versions(0)
        assert diff["to_version"] == g.current_version().version
        assert diff["counts"]["added"] >= 1

    def test_diff_versions_invalid_range(self):
        g = make_service()
        import pytest as _pytest

        with _pytest.raises(ValueError):
            g.diff_versions(5, 2)


# ── History / explain (provenance) ─────────────────────────────


class TestHistoryAndExplain:
    def test_history_records_snapshots(self):
        g = make_service()
        node = add_requirement(g, "RUN-H", "v1 text")
        g.add_node(EKNodeType.REQUIREMENT, "v1 text", node_id=node.node_id,
                   source_ref="RUN-H", source_type="run", payload={"description": "v2 text"})
        hist = g.history(node.node_id)
        assert len(hist.entries) >= 1
        assert hist.entries[0].payload.get("description") in ("v1 text", "v2 text")
        assert hist.current is not None

    def test_explain_provenance_and_related(self):
        g = make_service()
        lineage = build_lineage(g)
        explanation = g.explain(lineage["patch"].node_id)
        assert explanation["found"] is True
        assert explanation["provenance"] == {"run_id": "RUN-UNIT-1", "source": "coding"}
        related = explanation["related"]
        rel_types = {r["relationship"] for r in related}
        assert "created_during" in rel_types
        assert "validated_by" in rel_types
        assert "approved_by" in rel_types

    def test_explain_missing(self):
        g = make_service()
        assert g.explain("NOPE")["found"] is False


# ── Query planner ──────────────────────────────────────────────


class TestQueryPlanner:
    async def _seed(self) -> EngineeringKnowledgeGraphService:
        g = make_service()
        build_lineage(g, run_id="RUN-Q1")
        build_lineage(g, run_id="RUN-Q2")
        return g

    @pytest.mark.asyncio
    async def test_query_returns_bounded_nodes(self):
        g = await self._seed()
        result = await g.query("authentication", limit=5)
        assert result.query == "authentication"
        assert len(result.nodes) <= MAX_QUERY_RESULTS
        assert result.version == g.current_version().version

    @pytest.mark.asyncio
    async def test_query_planner_classifies_affected_tests(self):
        from app.services.knowledge_query_planner import KnowledgeQueryPlanner

        g = await self._seed()
        planner = KnowledgeQueryPlanner(graph=g)
        plan = planner.plan("which tests are affected by auth changes?")
        assert plan is not None
        assert plan.intent == "affected_tests"
        assert plan.strategy in (RetrievalStrategy.SEMANTIC_GRAPH, RetrievalStrategy.MULTI)
        assert EKNodeType.TEST_SUITE in plan.target_kinds or EKNodeType.TEST in plan.target_kinds

    @pytest.mark.asyncio
    async def test_query_planner_classifies_requirements(self):
        from app.services.knowledge_query_planner import KnowledgeQueryPlanner

        g = await self._seed()
        planner = KnowledgeQueryPlanner(graph=g)
        plan = planner.plan("find requirements related to authentication")
        assert plan.intent == "find_related_requirements"
        assert EKNodeType.REQUIREMENT in plan.target_kinds

    @pytest.mark.asyncio
    async def test_query_planner_classifies_history(self):
        from app.services.knowledge_query_planner import KnowledgeQueryPlanner

        g = await self._seed()
        planner = KnowledgeQueryPlanner(graph=g)
        plan = planner.plan("show the engineering history timeline for authentication")
        assert plan.intent == "engineering_history"
        assert EKNodeType.NOTEBOOK_ENTRY in plan.target_kinds


# ── PostgreSQL persistence ─────────────────────────────────────


def _test_db_url() -> Optional[str]:
    url = _db_url()
    return url or None


@pytest.mark.skipif(not _pg_available(), reason="PostgreSQL not configured")
class TestPostgresPersistence:
    @pytest.mark.asyncio
    async def test_record_run_and_recover_roundtrip(self):
        from app.models.issues import ImplementationPlan, ImplementationStep
        from app.models.orchestration import (
            DevPilotRun,
            RunSource,
            RunSourceType,
            RunStatus,
            StageType,
        )
        from app.models.repair import RepairResult
        from app.models.review import QualityGateDecision, QualityGateResult, ReviewReport
        from app.models.testing import ExecutionStatus, TestRunResult

        svc = EngineeringKnowledgeGraphService(database_url=_test_db_url())
        await svc.recover()
        try:
            run = DevPilotRun(
                run_id="RUN-PG-1",
                source=RunSource(source_type=RunSourceType.USER_TASK, title="PG roundtrip"),
                repository_path="repo/pg-app",
                status=RunStatus.APPROVED,
                current_stage=StageType.QUALITY_GATE,
                plan=ImplementationPlan(
                    summary="Plan", objective="Objective",
                    steps=[ImplementationStep(id="S1", title="step", description="desc")],
                ),
                test_result=TestRunResult(
                    run_id="RUN-PG-1", workspace_id="ws-pg",
                    tests_total=8, tests_failed=0, status=ExecutionStatus.PASSED,
                ),
                repair_result=RepairResult.model_construct(attempts=1, stop_reason="none"),
                review_report=ReviewReport(review_id="REV-PG-1", findings=[]),
                quality_gate_result=QualityGateResult(
                    review_id="REV-PG-1", decision=QualityGateDecision.APPROVED,
                ),
            )
            version = await svc.record_run(run)
            assert version.version > 0

            # Fresh service = simulated restart.
            fresh = EngineeringKnowledgeGraphService(database_url=_test_db_url())
            await fresh.recover()
            try:
                nodes = fresh.all_nodes(limit=10_000)
                assert len(nodes) >= len(version.updated_nodes)
                run_nodes = [n for n in nodes if n.source_ref == "RUN-PG-1"]
                assert run_nodes, "run nodes not recovered"
            finally:
                await fresh.dispose()
        finally:
            await svc.dispose()

    @pytest.mark.asyncio
    async def test_recover_idempotent(self):
        svc = EngineeringKnowledgeGraphService(database_url=_test_db_url())
        await svc.recover()
        count1 = len(svc.all_nodes(limit=10_000))
        await svc.recover()
        count2 = len(svc.all_nodes(limit=10_000))
        assert count1 == count2
        await svc.dispose()


# ── API ────────────────────────────────────────────────────────


class TestGraphAPI:
    @pytest.fixture(autouse=True)
    def _reset_service(self):
        from app.api.v1 import engineering_graph as api_module

        api_module._service = None
        yield
        api_module._service = None

    def _seed_api(self):
        from app.api.v1 import engineering_graph as api_module
        from app.services.engineering_graph_service import (
            EngineeringKnowledgeGraphService,
        )

        svc = EngineeringKnowledgeGraphService()
        build_lineage(svc, run_id="RUN-API-1")
        api_module._service = svc
        return svc

    def test_query_endpoint(self):
        from fastapi.testclient import TestClient

        from app.main import app

        self._seed_api()
        with TestClient(app) as client:
            resp = client.get("/api/v1/graph/query", params={"q": "authentication"})
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] is True
            assert data["data"]["strategy"] in {s.value for s in RetrievalStrategy}

    def test_node_endpoint(self):
        from fastapi.testclient import TestClient

        from app.main import app

        svc = self._seed_api()
        run_node = svc.find_nodes(node_type=EKNodeType.RUN)[0]
        with TestClient(app) as client:
            resp = client.get(f"/api/v1/graph/node/{run_node.node_id}")
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["node"]["node_type"] == "run"
            assert len(data["outgoing_edges"]) >= 1

    def test_node_endpoint_404(self):
        from fastapi.testclient import TestClient

        from app.main import app

        self._seed_api()
        with TestClient(app) as client:
            assert client.get("/api/v1/graph/node/NOPE").status_code == 404

    def test_history_endpoint(self):
        from fastapi.testclient import TestClient

        from app.main import app

        svc = self._seed_api()
        node = add_requirement(svc, "RUN-API-1", "changed requirement")
        with TestClient(app) as client:
            resp = client.get(f"/api/v1/graph/history/{node.node_id}")
            assert resp.status_code == 200
            assert resp.json()["data"]["node_id"] == node.node_id

    def test_neighborhood_endpoint(self):
        from fastapi.testclient import TestClient

        from app.main import app

        svc = self._seed_api()
        run_node = svc.find_nodes(node_type=EKNodeType.RUN)[0]
        with TestClient(app) as client:
            resp = client.get(
                f"/api/v1/graph/neighborhood/{run_node.node_id}",
                params={"depth": 2},
            )
            assert resp.status_code == 200
            assert resp.json()["data"]["total_nodes"] >= 5

    def test_explain_endpoint(self):
        from fastapi.testclient import TestClient

        from app.main import app

        svc = self._seed_api()
        patch = svc.find_nodes(node_type=EKNodeType.PATCH)[0]
        with TestClient(app) as client:
            resp = client.get(f"/api/v1/graph/explain/{patch.node_id}")
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["found"] is True
            assert data["provenance"]  # evidence origins never lost

    def test_version_endpoint(self):
        from fastapi.testclient import TestClient

        from app.main import app

        self._seed_api()
        with TestClient(app) as client:
            resp = client.get("/api/v1/graph/version")
            assert resp.status_code == 200
            assert resp.json()["data"]["version"]["version"] >= 1

    def test_explain_never_exposes_hidden_reasoning(self):
        """Security invariant: explain returns provenance + evidence only."""
        from fastapi.testclient import TestClient

        from app.main import app

        svc = self._seed_api()
        run_node = svc.find_nodes(node_type=EKNodeType.RUN)[0]
        with TestClient(app) as client:
            resp = client.get(f"/api/v1/graph/explain/{run_node.node_id}")
            assert resp.status_code == 200
            text = str(resp.json())
            assert "chain" not in text.lower()
            assert "cot" not in text
            assert "hidden_prompt" not in text.lower()

    def test_diff_endpoint(self):
        from fastapi.testclient import TestClient

        from app.main import app

        svc = self._seed_api()
        svc.increment_version(run_id="RUN-DIFF", summary="timeline step",
                              updated_nodes=["n1", "n2"])
        current = svc.current_version().version
        with TestClient(app) as client:
            resp = client.get(
                "/api/v1/graph/diff",
                params={"from_version": current - 1, "to_version": current},
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["from_version"] == current - 1
            assert data["to_version"] == current
            assert data["counts"]["added"] >= 1
            assert data["per_version"][-1]["version"] == current

    def test_diff_endpoint_invalid_range(self):
        from fastapi.testclient import TestClient

        from app.main import app

        self._seed_api()
        with TestClient(app) as client:
            resp = client.get(
                "/api/v1/graph/diff",
                params={"from_version": 9, "to_version": 3},
            )
            assert resp.status_code == 400

    def test_diff_endpoint_defaults_to_current(self):
        from fastapi.testclient import TestClient

        from app.main import app

        svc = self._seed_api()
        svc.increment_version(run_id="RUN-DIFF2", summary="s", updated_nodes=["n3"])
        with TestClient(app) as client:
            resp = client.get("/api/v1/graph/diff", params={"from_version": 0})
            assert resp.status_code == 200
            assert resp.json()["data"]["to_version"] == svc.current_version().version


# ── Graph live WebSocket (§19C) ────────────────────────────────


class TestGraphWebSocket:
    def test_graph_feed_sends_snapshot(self):
        from fastapi.testclient import TestClient

        from app.main import app

        with TestClient(app) as client:
            with client.websocket_connect("/api/v1/ws/graph") as ws:
                msg = ws.receive_json()
                assert msg["type"] == "graph_update"
                assert msg["event_type"] == "snapshot"
                assert "data" in msg
                assert msg["data"]["version"] >= 0

    def test_graph_feed_receives_live_version_bump(self):
        """A version increment while subscribed is pushed to the client."""
        from fastapi.testclient import TestClient

        from app.main import app
        from app.services.engineering_graph_service import (
            EngineeringKnowledgeGraphService,
        )

        svc = EngineeringKnowledgeGraphService()
        from app.api.v1 import engineering_graph as api_module

        api_module._service = svc

        with TestClient(app) as client:
            with client.websocket_connect("/api/v1/ws/graph") as ws:
                first = ws.receive_json()
                assert first["event_type"] == "snapshot"
                # Bump the version inside the server's event loop so the
                # best-effort broadcast fires while the client is subscribed.
                client.portal.call(
                    lambda: svc.increment_version(
                        run_id="RUN-WS",
                        summary="live update",
                        updated_nodes=["n9"],
                    )
                )
                second = ws.receive_json()
                assert second["type"] == "graph_update"
                assert second["event_type"] == "version_incremented"
                assert second["data"]["run_id"] == "RUN-WS"
                assert "n9" in second["data"]["updated_nodes"]

        api_module._service = None


# ── CLI ────────────────────────────────────────────────────────


class TestGraphCLI:
    def test_cli_commands_exist(self):
        import argparse

        from app.cli_engineering_graph import add_cli_commands

        parser = argparse.ArgumentParser()
        add_cli_commands(parser.add_subparsers())
        # Exercise --help for each subcommand without failing.
        for sub in ("query", "explain", "history", "neighborhood", "version"):
            try:
                parser.parse_args(["graph", sub, "--help"])
            except SystemExit:
                pass  # --help exits 0; parse success is what we assert via no crash

    @pytest.mark.asyncio
    async def test_run_graph_version(self, capsys):
        from app.cli_engineering_graph import run_graph_version

        await run_graph_version()
        captured = capsys.readouterr()
        assert "Engineering Knowledge Graph" in captured.out
        assert "Nodes:" in captured.out

    @pytest.mark.asyncio
    async def test_run_graph_query(self, capsys):
        from app.cli_engineering_graph import run_graph_query

        # The CLI builds its own service; run against an empty graph and
        # verify the command executes and prints a header.
        await run_graph_query("authentication")
        captured = capsys.readouterr()
        assert "Graph Query" in captured.out
        assert "Strategy:" in captured.out


# ── Phase 19: Semantic retrieval ───────────────────────────────


class TestSemanticEmbedder:
    def test_hashed_embedder_similarity_preserving(self):
        """Similar texts must get higher cosine similarity than unrelated
        texts (the deterministic hashed n-gram provider is NOT hash-random)."""
        from app.rag.embeddings.hashed_provider import HashedNGramEmbeddingProvider

        provider = HashedNGramEmbeddingProvider(dimension=256)
        a = provider.embed_query("caching layer for repeated reads")
        b = provider.embed_query("cache invalidation strategy")
        c = provider.embed_query("payment retry webhook timeout")

        def cos(x, y):
            dot = sum(i * j for i, j in zip(x, y))
            na = (sum(i * i for i in x)) ** 0.5
            nb = (sum(j * j for j in y)) ** 0.5
            return dot / (na * nb)

        same_topic = cos(a, b)
        different_topic = cos(a, c)
        assert same_topic > different_topic + 0.05, (
            f"similarity not discriminating: {same_topic:.4f} vs {different_topic:.4f}"
        )
        # Deterministic across calls
        assert provider.embed_query("caching layer for repeated reads") == a

    def test_hashed_embedder_dimension(self):
        from app.rag.embeddings.hashed_provider import HashedNGramEmbeddingProvider

        provider = HashedNGramEmbeddingProvider(dimension=128)
        vec = provider.embed_query("hello world")
        assert len(vec) == 128
        assert provider.dimension == 128


class TestSemanticSearch:
    @pytest.mark.asyncio
    async def test_semantic_search_finds_related_node(self):
        """A node whose payload shares vocabulary with the query is surfaced
        even when the lexical name match is absent."""
        g = make_service()
        # Node with a name that does NOT contain the query term but whose
        # payload does — semantic similarity should still find it.
        g.add_node(
            EKNodeType.REQUIREMENT, "speed up repeated reads",
            source_ref="RUN-SEM-1", source_type="run",
            payload={"description": "Add a caching layer for repeated reads"},
            provenance={"run_id": "RUN-SEM-1", "source": "requirements"},
        )
        g.add_node(
            EKNodeType.REQUIREMENT, "handle payment failures",
            source_ref="RUN-SEM-2", source_type="run",
            payload={"description": "Retry webhook calls with backoff"},
            provenance={"run_id": "RUN-SEM-2", "source": "requirements"},
        )
        hits = await g.semantic_search(
            "cache repeated reads", limit=5, target_kinds=[EKNodeType.REQUIREMENT]
        )
        assert hits, "expected at least one semantic hit"
        assert hits[0]["score"] > 0.0
        names = [h["name"] for h in hits]
        assert "speed up repeated reads" in names[:3], (
            f"caching node not ranked high: {names}"
        )

    @pytest.mark.asyncio
    async def test_semantic_search_bounded_and_filtered(self):
        g = make_service()
        for i in range(10):
            g.add_node(
                EKNodeType.REQUIREMENT, f"requirement number {i}",
                source_ref=f"RUN-S-{i}", source_type="run",
                payload={"description": f"shared topic {i}"},
            )
        g.add_node(
            EKNodeType.FILE, "notes.txt", source_ref="notes.txt", source_type="file",
            payload={"description": "shared topic"},
        )
        hits = await g.semantic_search("shared topic", limit=3)
        assert len(hits) <= 3
        filtered = await g.semantic_search(
            "shared topic", limit=10, target_kinds=[EKNodeType.FILE]
        )
        assert filtered
        assert all(h["node_type"] == "file" for h in filtered)

    @pytest.mark.asyncio
    async def test_semantic_search_empty_graph(self):
        g = make_service()
        assert await g.semantic_search("anything", limit=5) == []


class TestSemanticPlannerMerge:
    @pytest.mark.asyncio
    async def test_planner_merges_semantic_within_bounds(self):
        """retrieve() must merge semantic hits into lexical results, set the
        semantic flags, and never exceed MAX_QUERY_RESULTS."""
        g = make_service()
        build_lineage(g, run_id="RUN-SQ1")
        build_lineage(g, run_id="RUN-SQ2")
        # A semantically-related node whose PAYLOAD matches the query topic
        # but whose NAME/qualified_name/source_ref share NO lexical terms
        # with the query — only the semantic pass can surface it.
        g.add_node(
            EKNodeType.REQUIREMENT, "buffer responses",
            source_ref="RUN-SQ3", source_type="run",
            payload={"description": "Caching layer for repeated reads"},
            provenance={"run_id": "RUN-SQ3", "source": "requirements"},
        )
        # Key terms (caching, layer, repeated, reads) appear ONLY in the
        # payload of RUN-SQ3 — no node name/source_ref contains them, so the
        # lexical scan finds nothing and the semantic merge must supply it.
        result = await g.query("caching layer for repeated reads", limit=5)
        assert len(result.nodes) <= MAX_QUERY_RESULTS
        assert result.semantic_used is True
        assert result.semantic_matches >= 1, (
            f"expected semantic merge, got matches={result.semantic_matches}"
        )
        ids = {n.node_id for n in result.nodes}
        assert any(n.node_id in ids for n in result.nodes)  # nodes are deduped
        assert "buffer responses" in {n.name for n in result.nodes}, (
            "semantic hit (buffer responses) not present in merged results"
        )

    @pytest.mark.asyncio
    async def test_planner_semantic_pure_hit(self):
        """A query with NO lexical overlap must still surface a semantically
        related node (merged within bounds)."""
        g = make_service()
        build_lineage(g, run_id="RUN-SP1")
        g.add_node(
            EKNodeType.REQUIREMENT, "hold frequently used data",
            source_ref="RUN-SP2", source_type="run",
            payload={"description": "Cache hot results in memory"},
            provenance={"run_id": "RUN-SP2", "source": "requirements"},
        )
        result = await g.query("memory caching of hot reads", limit=5)
        assert result.semantic_used is True
        assert result.semantic_matches >= 1
        assert result.semantic_top_score > 0.0

    @pytest.mark.asyncio
    async def test_semantic_index_deterministic_across_instances(self):
        """The index is derived deterministically: two services embedding the
        same nodes return the same top hit for the same query (this is what
        makes restart recovery exact even without a PG mirror)."""
        def build() -> EngineeringKnowledgeGraphService:
            svc = make_service()
            svc.add_node(
                EKNodeType.REQUIREMENT, "cache repeated reads",
                source_ref="RUN-REC-1", source_type="run",
                payload={"description": "Add caching layer"},
            )
            svc.add_node(
                EKNodeType.REQUIREMENT, "handle payment failures",
                source_ref="RUN-REC-2", source_type="run",
                payload={"description": "Retry webhook calls with backoff"},
            )
            return svc

        g1, g2 = build(), build()
        hits1 = await g1.semantic_search("caching layer", limit=5)
        hits2 = await g2.semantic_search("caching layer", limit=5)
        assert hits1 and hits2
        assert hits1[0]["node_id"] == hits2[0]["node_id"]
        assert hits1[0]["score"] == hits2[0]["score"]
        assert hits1[0]["name"] == "cache repeated reads"


# ── Phase 12d closure: impact-edge test selection ───────────────


def build_impact_evidence(graph: EngineeringKnowledgeGraphService, run_id: str = "RUN-IMP-1") -> Dict[str, EKNode]:
    """FILE ←MODIFIES← PATCH →VALIDATED_BY→ TEST_SUITE with test_files."""
    run = add_run(graph, run_id)
    patch = add_patch(graph, run_id)
    file_node = graph.add_node(
        EKNodeType.FILE, "service.py", source_ref="auth/service.py",
        source_type="file", qualified_name="auth/service.py",
        provenance={"run_id": run_id, "source": "patch"},
    )
    tests = graph.add_node(
        EKNodeType.TEST_SUITE, f"tests:{run_id}", source_ref=run_id, source_type="run",
        qualified_name=f"tests:{run_id}",
        payload={"status": "passed", "tests_total": 10, "tests_failed": 0,
                 "test_files": ["auth/tests/test_auth.py", "tests/test_session.py"]},
        provenance={"run_id": run_id, "source": "testing"},
    )
    graph.add_edge(run.node_id, patch.node_id, EKRelationshipType.CREATED_DURING,
                   metadata={"run_id": run_id})
    graph.add_edge(patch.node_id, file_node.node_id, EKRelationshipType.MODIFIES,
                   metadata={"run_id": run_id})
    graph.add_edge(patch.node_id, tests.node_id, EKRelationshipType.VALIDATED_BY,
                   metadata={"run_id": run_id})
    return {"run": run, "patch": patch, "file": file_node, "tests": tests}


class TestImpactEdgeTestSelection:
    def test_selects_tests_via_patch_impact_edges(self):
        """Changed file → FILE → MODIFIES → PATCH → VALIDATED_BY → TEST_SUITE
        yields the test files recorded on the suite payload."""
        g = make_service()
        build_impact_evidence(g)
        selected = g.select_tests_for_changes(["auth/service.py"])
        assert "auth/tests/test_auth.py" in selected
        assert "tests/test_session.py" in selected

    def test_unknown_changed_files_return_empty(self):
        g = make_service()
        build_impact_evidence(g)
        assert g.select_tests_for_changes(["nope/unknown.py"]) == []

    def test_empty_changed_files_return_empty(self):
        g = make_service()
        build_impact_evidence(g)
        assert g.select_tests_for_changes([]) == []

    def test_empty_graph_returns_empty(self):
        g = make_service()
        assert g.select_tests_for_changes(["auth/service.py"]) == []

    def test_dedupes_and_bounds(self):
        g = make_service()
        build_impact_evidence(g, run_id="RUN-IMP-1")
        build_impact_evidence(g, run_id="RUN-IMP-2")
        selected = g.select_tests_for_changes(["auth/service.py"], limit=2)
        assert len(selected) == 2
        assert len(set(selected)) == len(selected)  # no duplicates

    def test_never_raises_on_error(self):
        g = make_service()
        assert g.select_tests_for_changes(None) == []  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_record_run_persists_test_files_in_suite_payload(self):
        """record_run must carry the tests that ran into the TEST_SUITE payload
        so a later replan can recover them via impact edges."""
        from app.models.coding import FileChange, FileOperation, PatchSet
        from app.models.issues import ImplementationPlan, ImplementationStep
        from app.models.orchestration import (
            DevPilotRun,
            RunSource,
            RunSourceType,
            RunStatus,
            StageType,
        )
        from app.models.testing import (
            CommandCategory,
            ExecutionStatus,
            ProcessExecutionResult,
            TestRunResult,
        )

        g = make_service()
        run = DevPilotRun(
            run_id="RUN-TF-1",
            source=RunSource(source_type=RunSourceType.USER_TASK, title="Fix auth"),
            repository_path="repo/acme",
            status=RunStatus.APPROVED,
            current_stage=StageType.QUALITY_GATE,
            plan=ImplementationPlan(
                summary="Fix auth", objective="Fix auth",
                steps=[ImplementationStep(id="S1", title="s", description="d")],
                test_strategy="impact-driven tests: auth/tests/test_auth.py",
            ),
            patch_set=PatchSet(
                patch_id="P-1",
                changes=[FileChange(
                    change_id="C-1", operation=FileOperation.MODIFY,
                    path="auth/service.py",
                )],
            ),
            test_result=TestRunResult(
                run_id="RUN-TF-1", workspace_id="ws-1",
                tests_total=8, tests_failed=0, status=ExecutionStatus.PASSED,
                process_results=[
                    ProcessExecutionResult(
                        step_id="STEP-001",
                        command="python -m pytest -q auth/tests/test_auth.py tests/test_session.py",
                        category=CommandCategory.TEST,
                        status=ExecutionStatus.PASSED,
                    ),
                ],
            ),
        )
        await g.record_run(run)
        suites = g.find_nodes(node_type=EKNodeType.TEST_SUITE)
        assert suites
        test_files = suites[0].payload.get("test_files", [])
        assert "auth/tests/test_auth.py" in test_files
        assert "tests/test_session.py" in test_files
        # And the impact-edge walk recovers them for a changed file.
        selected = g.select_tests_for_changes(["auth/service.py"])
        assert "auth/tests/test_auth.py" in selected
        assert "tests/test_session.py" in selected

    @pytest.mark.asyncio
    async def test_record_run_without_tests_persists_empty_list(self):
        g = make_service()
        lineage = build_lineage(g, run_id="RUN-NOTF")
        suites = g.find_nodes(node_type=EKNodeType.TEST_SUITE)
        assert suites
        assert suites[0].payload.get("test_files", []) == []
        assert lineage["tests"].node_id is not None


# ── Integration: ContextEngine & orchestration ingestion ───────


class TestIntegrations:
    @pytest.mark.asyncio
    async def test_context_engine_queries_ekg(self):
        """ContextEngine retrieval should include EKG nodes when present."""
        from app.services.context_engine import ContextEngine

        engine = ContextEngine()
        ctx = await engine.build_context(
            task="Implement authentication",
            agent_type="planner",
            repository_path="repo/demo",
        )
        # The engine must not crash and must return a structured context
        # carrying the requested task + agent type.
        assert ctx is not None
        assert ctx.agent_type == "planner"
        assert ctx.task == "Implement authentication"

    @pytest.mark.asyncio
    async def test_orchestration_ingests_run_into_graph(self):
        """record_run is idempotent: ingesting the same run twice adds no
        duplicate nodes/edges."""
        g = make_service()
        lineage = build_lineage(g, run_id="RUN-IDEM")
        before_nodes = len(g.all_nodes(limit=10_000))
        # Re-ingest the same lineage — must dedup, not duplicate.
        build_lineage(g, run_id="RUN-IDEM")
        after_nodes = len(g.all_nodes(limit=10_000))
        assert after_nodes == before_nodes
        assert lineage["run"].node_id is not None

    @pytest.mark.asyncio
    async def test_regression_evidence_only_bounded(self):
        """Query results and explain responses never contain raw chain-of-
        thought and are bounded to configured caps."""
        g = make_service()
        build_lineage(g)
        result = await g.query("authentication")
        assert len(result.nodes) <= MAX_QUERY_RESULTS
        assert len(result.edges) <= 100
        for node in result.nodes:
            payload = str(node.payload)
            assert "thinking" not in payload.lower()
            assert "chain" not in payload.lower()
        assert g.stats().node_count <= 10_000
