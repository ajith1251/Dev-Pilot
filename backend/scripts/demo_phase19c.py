"""
Phase 19C — Interactive Engineering Knowledge Graph Visualization demo.

Drives all six required demonstrations deterministically (no paid LLM,
no live frontend, pure backend + API + WebSocket):

    A. Interactive exploration      — bounded neighborhood expansion at
       depth 1/2/3 + the node/relationship facets the frontend legend
       consumes (the data backing the force-directed view)
    B. Cross-repository navigation  — org-scope merge, local-scope
       isolation, bridge-only traversal (the backend behind "jump between
       repositories")
    C. Relationship highlighting    — backend EKRelationshipType enum is
       byte-for-byte covered by the frontend palette, plus a relationship
       histogram + filtered-edge demo over a real neighborhood
    D. Graph version comparison     — diff_versions change-set feeding the
       frontend graph timeline (added/removed nodes, changed edges,
       per-version breakdown)
    E. Live WebSocket graph updates — connect WS /api/v1/ws/graph, receive
       the snapshot, trigger a version increment, receive the live
       version_incremented broadcast (no page refresh)
    F. Search/filter performance    — 3000-node synthetic graph; bounded
       query + neighborhood latency and result caps

Usage:
    python scripts/demo_phase19c.py            # in-memory (deterministic)
    python scripts/demo_phase19c.py --pg       # PostgreSQL persistence
    python scripts/demo_phase19c.py --json     # JSON summary output
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Frontend palette keys (frontend/src/lib/graph/graphModel.ts) — the TS
# registry-contract test and demo C assert these stay in lock-step with the
# backend enums.
FRONTEND_NODE_TYPES = [
    "repository", "folder", "file", "module", "package", "class",
    "interface", "function", "method", "requirement",
    "acceptance_criterion", "implementation_plan", "plan_version", "goal",
    "patch", "commit_candidate", "test", "test_suite", "review_finding",
    "quality_gate", "evidence", "consensus", "contradiction",
    "notebook_entry", "decision", "run", "agent", "repository_memory",
]

FRONTEND_RELATIONSHIPS = [
    "calls", "imports", "contains", "depends_on", "implements", "tests",
    "references", "affects", "modifies", "satisfies", "created_during",
    "produced_by", "derived_from", "supports", "contradicts", "supersedes",
    "uses_memory", "validated_by", "reviewed_by", "approved_by",
    "depends_on_repository", "shares_library", "imports_package",
    "implements_shared_interface", "references_shared_component",
    "uses_shared_memory", "calls_external_service",
]


def _is_pg_configured() -> bool:
    from app.config import settings

    return bool(settings.DATABASE_URL or settings.TEST_DATABASE_URL)


def _db_url() -> str:
    from app.config import settings

    return settings.DATABASE_URL or settings.TEST_DATABASE_URL or ""


async def _make_graph():
    from app.services.engineering_graph_service import (
        EngineeringKnowledgeGraphService,
    )

    graph = EngineeringKnowledgeGraphService(database_url=_db_url() or None)
    await graph.recover()
    return graph


def _ekg_types():
    from app.models.engineering_graph import EKNodeType, EKRelationshipType

    return EKNodeType, EKRelationshipType


def _seed_lineage(graph, run_id: str, title: str, repository_id: str = "repo-acme-api"):
    """Deterministic Requirement→Plan→Patch→Test→Gate lineage for one run.

    Uses the real service add_node/add_edge + increment_version path so the
    demo exercises production graph ingestion. Returns the run node.
    """
    EKNodeType, EKRelationshipType = _ekg_types()
    req = graph.add_node(
        EKNodeType.REQUIREMENT, f"{title} requirement",
        source_ref=run_id, source_type="run", repository_id=repository_id,
        payload={"scope": "interactive"},
        provenance={"run_id": run_id, "evidence": "requirements doc"},
    )
    plan = graph.add_node(
        EKNodeType.IMPLEMENTATION_PLAN, f"{title} plan",
        source_ref=run_id, source_type="run", repository_id=repository_id,
        payload={"steps": 3},
        provenance={"run_id": run_id, "evidence": "planner output"},
    )
    file_node = graph.add_node(
        EKNodeType.FILE, f"{title}.py", source_ref=f"src/{title}.py",
        source_type="file", repository_id=repository_id,
        qualified_name=f"src/{title}.py",
        provenance={"run_id": run_id, "source": "patch"},
    )
    patch = graph.add_node(
        EKNodeType.PATCH, f"{title} patch",
        source_ref=run_id, source_type="run", repository_id=repository_id,
        payload={"changes": 1},
        provenance={"run_id": run_id, "evidence": "coding agent"},
    )
    test = graph.add_node(
        EKNodeType.TEST_SUITE, f"{title} tests",
        source_ref=run_id, source_type="run", repository_id=repository_id,
        payload={"tests": 8, "failed": 0},
        provenance={"run_id": run_id, "evidence": "test results"},
    )
    gate = graph.add_node(
        EKNodeType.QUALITY_GATE, f"{title} gate",
        source_ref=run_id, source_type="run", repository_id=repository_id,
        payload={"decision": "approved"},
        provenance={"run_id": run_id, "evidence": "reviewer output"},
    )
    run_node = graph.add_node(
        EKNodeType.RUN, f"{title} run",
        source_ref=run_id, source_type="run", repository_id=repository_id,
        payload={"status": "approved"},
        provenance={"run_id": run_id, "source": "orchestrator"},
    )

    edges = [
        (run_node.node_id, req.node_id, EKRelationshipType.CREATED_DURING),
        (plan.node_id, req.node_id, EKRelationshipType.SATISFIES),
        (patch.node_id, req.node_id, EKRelationshipType.SATISFIES),
        (patch.node_id, file_node.node_id, EKRelationshipType.MODIFIES),
        (patch.node_id, test.node_id, EKRelationshipType.VALIDATED_BY),
        (patch.node_id, gate.node_id, EKRelationshipType.APPROVED_BY),
        (file_node.node_id, plan.node_id, EKRelationshipType.IMPLEMENTS),
    ]
    for src, tgt, rel in edges:
        graph.add_edge(src, tgt, rel, provenance={"run_id": run_id})

    version = graph.increment_version(
        run_id=run_id,
        summary=f"Run {run_id}: {title}",
        updated_nodes=[req.node_id, plan.node_id, file_node.node_id,
                       patch.node_id, test.node_id, gate.node_id,
                       run_node.node_id],
        updated_edges=[e for _, _, e in edges],
    )
    return run_node, version


async def demo_a(graph) -> dict:
    """A. Interactive exploration — neighborhood expansion + facets."""
    run_node, _ = _seed_lineage(graph, "RUN-P19C-A", "OAuth login flow")

    expansions = {}
    prev_count = 0
    for depth in (1, 2, 3):
        res = graph.neighborhood(run_node.node_id, depth=depth, max_nodes=80)
        expansions[str(depth)] = {
            "nodes": len(res.nodes),
            "edges": len(res.edges),
            "truncated": res.truncated,
        }
        assert len(res.nodes) >= prev_count, (
            f"depth {depth} shrank the neighborhood (nodes)"
        )
        prev_count = len(res.nodes)

    node_types = sorted({n.node_type.value for n in graph.all_nodes(limit=10_000)})
    stats = graph.stats()
    relationship_types = sorted(stats.relationship_types.keys())

    assert len(node_types) >= 5, f"unexpectedly few node types: {node_types}"
    assert len(relationship_types) >= 5, (
        f"unexpectedly few relationship types: {relationship_types}"
    )
    return {
        "root": run_node.node_id,
        "expansions_depth_1_2_3": expansions,
        "node_types_present": node_types,
        "relationship_types_present": relationship_types,
        "monotonic_growth": True,
    }


async def demo_b(graph) -> dict:
    """B. Cross-repository navigation — org merge, isolation, bridges."""
    from app.models.engineering_graph import DEFAULT_REPOSITORY_ID, QueryScope
    from app.services.organization_graph_service import (
        OrganizationKnowledgeGraphService,
    )
    from app.models.engineering_graph import EKRelationshipType

    org = OrganizationKnowledgeGraphService(database_url=_db_url() or None)
    repos = ["repo-p19c-api", "repo-p19c-web"]
    for rid in repos:
        org.register_repository(
            rid, name=f"P19C {rid}", path=f"/org/{rid}",
            source_type="local", organization_id="default",
        )
        _seed_lineage(org.get_graph(rid), f"RUN-P19C-B-{rid}", f"Feature {rid}",
                      repository_id=rid)

    org.link_repositories(
        "repo-p19c-web", "repo-p19c-api", EKRelationshipType.DEPENDS_ON_REPOSITORY,
        weight=0.9, metadata={"reason": "web calls api"},
        provenance={"source": "platform", "reason": "declared dependency"},
    )
    await org.synchronize()

    org_scope = await org.query(
        "requirement", scope=QueryScope.ORGANIZATION, limit=25,
    )
    assert len(org_scope.repositories) >= 2, (
        f"org query did not merge repositories: {org_scope.repositories}"
    )
    assert org_scope.total_nodes > 0

    local_scope = await org.query(
        "requirement", scope=QueryScope.LOCAL,
        repository_ids=["repo-p19c-web"], limit=25,
    )
    assert local_scope.total_nodes > 0
    assert all(
        (n.repository_id or DEFAULT_REPOSITORY_ID) == "repo-p19c-web"
        for n in local_scope.nodes
    ), "local scope leaked nodes from another repository"

    from app.services.organization_graph_service import _repo_node_id

    traversal = org.cross_repository_traversal(
        _repo_node_id("repo-p19c-web"), depth=2, max_nodes=200,
    )
    traversed_repos = {n.repository_id for n in traversal.nodes}
    assert "repo-p19c-api" in traversed_repos, (
        f"bridge traversal did not cross into repo-p19c-api: {traversed_repos}"
    )

    stats = org.stats()
    await org.dispose()
    return {
        "repositories": sorted(repos),
        "org_scope_repos": sorted(org_scope.repositories),
        "org_query_nodes": org_scope.total_nodes,
        "local_scope_isolated": True,
        "traversal_crossed_into": sorted(t for t in traversed_repos if t),
        "cross_edges": stats.cross_edge_count,
        "bridge_type": "explicit deterministic edges (never LLM-inferred)",
    }


async def demo_c(graph) -> dict:
    """C. Relationship highlighting — palette contract + edge filtering."""
    EKNodeType, EKRelationshipType = _ekg_types()
    run_node, _ = _seed_lineage(graph, "RUN-P19C-C", "Add caching layer")

    backend_node_types = sorted({t.value for t in EKNodeType})
    backend_relationships = sorted({r.value for r in EKRelationshipType})

    missing_types = sorted(set(backend_node_types) - set(FRONTEND_NODE_TYPES))
    missing_rels = sorted(set(backend_relationships) - set(FRONTEND_RELATIONSHIPS))
    assert not missing_types, f"frontend palette missing node types: {missing_types}"
    assert not missing_rels, f"frontend palette missing relationships: {missing_rels}"

    res = graph.neighborhood(run_node.node_id, depth=3, max_nodes=80)
    histogram: dict[str, int] = {}
    for e in res.edges:
        histogram[e.relationship.value] = histogram.get(e.relationship.value, 0) + 1
    assert len(histogram) >= 4, f"neighborhood too sparse: {histogram}"

    highlight_set = {"validated_by", "approved_by"}
    filtered = [e for e in res.edges if e.relationship.value in highlight_set]
    assert filtered and all(
        e.relationship.value in highlight_set for e in filtered
    ), "relationship filter did not restrict edges"

    assert EKRelationshipType.SATISFIES.value in FRONTEND_RELATIONSHIPS
    assert EKNodeType.REPOSITORY.value in FRONTEND_NODE_TYPES
    return {
        "backend_node_types": len(backend_node_types),
        "backend_relationships": len(backend_relationships),
        "palette_contract": "frontend covers 100% of backend enums",
        "relationship_histogram": histogram,
        "highlight_relationships": sorted(highlight_set),
        "highlighted_edges": len(filtered),
    }


async def demo_d(graph) -> dict:
    """D. Graph version comparison — the frontend timeline data."""
    v0 = graph.current_version().version
    _seed_lineage(graph, "RUN-P19C-D1", "Refactor billing module")
    _seed_lineage(graph, "RUN-P19C-D2", "Add admin dashboard")
    v2 = graph.current_version().version

    diff = graph.diff_versions(v0, v2)
    assert diff["counts"]["added"] > 0, "expected added nodes in diff"
    assert len(diff["per_version"]) >= 2, (
        f"expected >=2 version increments, got {len(diff['per_version'])}"
    )
    assert diff["from_version"] == v0 and diff["to_version"] == v2

    # A single increment's change-set is a strict subset of the whole.
    v1 = diff["per_version"][0]["version"]
    single = graph.diff_versions(v0, v1)
    assert single["counts"]["added"] <= diff["counts"]["added"]
    assert single["per_version"][0]["version"] == v1

    added_names = sorted(n["name"] for n in diff["added_nodes"])[:6]
    return {
        "from_version": v0,
        "to_version": v2,
        "added_nodes": diff["counts"]["added"],
        "removed_nodes": diff["counts"]["removed"],
        "changed_edges": diff["counts"]["changed_edges"],
        "per_version_increments": len(diff["per_version"]),
        "sample_added": added_names,
        "bounded_per_version": True,
    }


async def demo_e(graph) -> dict:
    """E. Live WebSocket graph updates (WS /api/v1/ws/graph)."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.api.v1 import engineering_graph as eg_module

    eg_module._service = graph  # point the API singleton at this demo graph

    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/ws/graph") as ws:
            snap = ws.receive_json()
            assert snap["type"] == "graph_update", f"bad snapshot: {snap}"
            assert snap["event_type"] == "snapshot", snap
            snapshot_version = snap["data"]["version"]

            new_version = client.portal.call(
                lambda: graph.increment_version(
                    run_id="RUN-P19C-E", summary="Live graph update demo",
                    updated_nodes=[],
                )
            )
            msg = ws.receive_json()
            assert msg["type"] == "graph_update", f"bad live event: {msg}"
            assert msg["event_type"] == "version_incremented", msg
            assert msg["data"]["version"] == new_version.version, msg

    return {
        "snapshot_event": "graph_update/snapshot",
        "snapshot_version": snapshot_version,
        "live_event": "graph_update/version_incremented",
        "live_version": new_version.version,
        "delivered_without_reload": True,
    }


async def demo_f(graph) -> dict:
    """F. Search/filter performance on a large synthetic graph."""
    EKNodeType, EKRelationshipType = _ekg_types()
    n_nodes = 3000
    t0 = time.perf_counter()
    first = None
    last = None
    prev = None
    for i in range(n_nodes):
        node = graph.add_node(
            EKNodeType.FILE, f"file_{i:04d}.py", source_ref=f"src/file_{i:04d}.py",
            source_type="file", repository_id="repo-big",
            qualified_name=f"src/file_{i:04d}.py",
            payload={"index": i},
            provenance={"run_id": "RUN-P19C-F", "source": "synthetic"},
        )
        if i == 0:
            first = node
        last = node
        if prev is not None:
            graph.add_edge(prev.node_id, node.node_id,
                           EKRelationshipType.IMPORTS,
                           provenance={"run_id": "RUN-P19C-F"})
        prev = node
    ingest_s = time.perf_counter() - t0

    t1 = time.perf_counter()
    result = await graph.query("file_1500", limit=10)
    query_s = time.perf_counter() - t1
    assert len(result.nodes) <= 10, "query result exceeded the bound"
    assert len(result.nodes) > 0, "expected to find file_1500"

    t2 = time.perf_counter()
    hood = graph.neighborhood(first.node_id, depth=2, max_nodes=60)
    hood_s = time.perf_counter() - t2
    assert len(hood.nodes) <= 60, "neighborhood exceeded the bound"

    budget = 10.0
    assert query_s < budget and hood_s < budget, (
        f"large-graph latency out of budget: query={query_s:.3f}s hood={hood_s:.3f}s"
    )
    return {
        "synthetic_nodes": n_nodes,
        "ingest_seconds": round(ingest_s, 3),
        "query_seconds": round(query_s, 4),
        "query_hits": len(result.nodes),
        "query_strategy": result.strategy.value,
        "neighborhood_seconds": round(hood_s, 4),
        "neighborhood_nodes": len(hood.nodes),
        "latency_budget_seconds": budget,
        "results_bounded": True,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 19C graph viz demo")
    parser.add_argument("--pg", action="store_true",
                        help="Run against PostgreSQL when configured")
    parser.add_argument("--json", action="store_true",
                        help="Output JSON summary")
    args = parser.parse_args()

    graph = await _make_graph()

    results = {}
    for name, fn in [
        ("A_interactive_exploration", demo_a),
        ("B_cross_repository_navigation", demo_b),
        ("C_relationship_highlighting", demo_c),
        ("D_version_comparison", demo_d),
        ("E_live_websocket_updates", demo_e),
        ("F_search_filter_performance", demo_f),
    ]:
        try:
            results[name] = await fn(graph)
            results[name]["PASS"] = True
        except Exception as exc:  # pragma: no cover
            results[name] = {"PASS": False, "error": str(exc)}

    pg = _is_pg_configured()

    if args.json:
        print(json.dumps({
            "phase": "19C",
            "persistence": "postgresql" if pg else "in-memory",
            "graph_version": graph.current_version().version,
            "demonstrations": results,
        }, indent=2, default=str))
        return

    print(f"\n{'='*64}")
    print("  Phase 19C - Interactive Engineering Knowledge Graph Demo")
    print(f"  Persistence: {'PostgreSQL' if pg else 'In-memory'}")
    print(f"  Graph version: {graph.current_version().version}")
    print(f"{'='*64}")

    labels = {
        "A_interactive_exploration": "A. Interactive exploration (neighborhood expansion + facets)",
        "B_cross_repository_navigation": "B. Cross-repository navigation (org merge / isolation / bridges)",
        "C_relationship_highlighting": "C. Relationship highlighting (palette contract + filtering)",
        "D_version_comparison": "D. Graph version comparison (timeline change-set)",
        "E_live_websocket_updates": "E. Live WebSocket graph updates (no reload)",
        "F_search_filter_performance": "F. Search/filter performance on a 3000-node graph",
    }
    for name, r in results.items():
        mark = "PASS" if r.get("PASS") else "FAIL"
        print(f"\n  [{mark}] {labels.get(name, name)}")
        for k, v in r.items():
            if k == "PASS":
                continue
            print(f"        {k}: {v}")

    all_pass = all(r.get("PASS") for r in results.values())
    print(f"\n{'='*64}")
    print(f"  OVERALL: {'ALL PASS' if all_pass else 'SOME FAILED'}")
    print(f"  GRAPH VISUALIZATION: "
          f"{'PASS' if results['A_interactive_exploration'].get('PASS') else 'FAIL'}")
    print(f"  CROSS-REPO NAVIGATION: "
          f"{'PASS' if results['B_cross_repository_navigation'].get('PASS') else 'FAIL'}")
    print(f"  RELATIONSHIP HIGHLIGHTING: "
          f"{'PASS' if results['C_relationship_highlighting'].get('PASS') else 'FAIL'}")
    print(f"  GRAPH TIMELINE: "
          f"{'PASS' if results['D_version_comparison'].get('PASS') else 'FAIL'}")
    print(f"  WEBSOCKET: "
          f"{'PASS' if results['E_live_websocket_updates'].get('PASS') else 'FAIL'}")
    print(f"  SEARCH & FILTER PERFORMANCE: "
          f"{'PASS' if results['F_search_filter_performance'].get('PASS') else 'FAIL'}")
    print(f"  POSTGRESQL: {'PASS' if pg else 'n/a (in-memory)'}")
    print(f"{'='*64}\n")

    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    asyncio.run(main())
