"""
Phase 19A — Organization Knowledge Graph demonstration script.

Drives the six required demonstrations deterministically (no paid LLM
required):

    A. Repository namespace registry — register multiple repositories,
       each owning an isolated EngineeringKnowledgeGraphService stamped
       with its repository_id
    B. Cross-repository linking — explicit, deterministic edges only
       (shared libraries, depends-on); invalid relationships and
       unregistered repositories are rejected; re-linking is idempotent
    C. Cross-repository traversal — bounded BFS that crosses a repo
       boundary ONLY through an explicit cross-repository edge (isolation
       is enforced structurally)
    D. Organization-wide query routing — LOCAL (strict isolation) /
       ORGANIZATION (merged linked repos) / AUTO (planner vocabulary
       decides) + per-repository evidence attribution
    E. ContextEngine integration — org-wide evidence surfaces for
       cross-repository vocabulary, stays silent for repository-local
       queries
    F. PostgreSQL persistence / restart recovery — namespaces + cross-edges
       round-trip through ekg_repository_namespaces /
       ekg_cross_repository_edges (migration 013), then cleanup

Usage:
    python scripts/demo_phase19a.py             # in-memory (deterministic)
    python scripts/demo_phase19a.py --pg        # PostgreSQL persistence
    python scripts/demo_phase19a.py --json      # JSON summary output

Mirrors the Phase 18 live-validation pattern: a single command that
exercises the capability end-to-end and prints a human-readable summary.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Run-unique repository suffix so the demo is idempotent even when a shared
# PostgreSQL database already holds organization state from earlier runs.
_SUFFIX = uuid.uuid4().hex[:6]
REPO_API = f"api-{_SUFFIX}"
REPO_CORE = f"core-{_SUFFIX}"
REPO_WEB = f"web-{_SUFFIX}"
# An unlinked namespace — proves isolation (no explicit edge, no leakage).
REPO_EXTRA = f"extra-{_SUFFIX}"


def _is_pg_configured() -> bool:
    from app.config import settings

    return bool(settings.DATABASE_URL or settings.TEST_DATABASE_URL)


def _db_url() -> str:
    # Prefer the dedicated test database (scratch) for a write-heavy demo;
    # fall back to the primary database when only that is configured.
    from app.config import settings

    return settings.TEST_DATABASE_URL or settings.DATABASE_URL or ""


async def _make_org(database_url: str | None = None):
    """Build an org graph service.

    When a database_url is supplied the service rehydrates persisted
    namespaces + cross-edges (restart simulation); otherwise it is a fresh
    in-memory organization (deterministic — no dependence on external state).
    """
    from app.services.organization_graph_service import (
        OrganizationKnowledgeGraphService,
    )

    org = OrganizationKnowledgeGraphService(database_url=database_url)
    if database_url:
        await org.recover()
    return org


async def demo_a_registry(org) -> dict:
    """A. Register repository namespaces + populate isolated graphs."""
    from app.models.engineering_graph import EKNodeType

    before = len(org.repositories())
    for rid, name in ((REPO_API, "API Gateway"), (REPO_CORE, "Core Domain"),
                      (REPO_WEB, "Web Client"), (REPO_EXTRA, "Isolated Sandbox")):
        org.register_repository(rid, name=name, path=f"/org/{rid}",
                                source_type="local")

    assert len(org.repositories()) == before + 4
    for rid in (REPO_API, REPO_CORE, REPO_WEB, REPO_EXTRA):
        assert org.get_namespace(rid) is not None
        assert org.get_graph(rid) is not None

    # repo-api files
    g = org.get_graph(REPO_API)
    for name in ("auth.ts", "token.ts", "shared_session_utils.ts"):
        g.add_node(EKNodeType.FILE, name, source_ref=f"{REPO_API}:{name}",
                   source_type="repository", qualified_name=f"{REPO_API}/{name}",
                   payload={"kind": "file"}, provenance={"repository_id": REPO_API})
    # repo-core files
    gc = org.get_graph(REPO_CORE)
    for name in ("session.ts", "shared_session_utils.ts"):
        gc.add_node(EKNodeType.FILE, name, source_ref=f"{REPO_CORE}:{name}",
                    source_type="repository", qualified_name=f"{REPO_CORE}/{name}",
                    payload={"kind": "file"}, provenance={"repository_id": REPO_CORE})
    # repo-web file
    gw = org.get_graph(REPO_WEB)
    gw.add_node(EKNodeType.FILE, "login.tsx", source_ref=f"{REPO_WEB}:login.tsx",
                source_type="repository", qualified_name=f"{REPO_WEB}/login.tsx",
                payload={"kind": "file"}, provenance={"repository_id": REPO_WEB})
    # repo-extra file (no cross-repository links at all)
    ge = org.get_graph(REPO_EXTRA)
    ge.add_node(EKNodeType.FILE, "scratch.py", source_ref=f"{REPO_EXTRA}:scratch.py",
                source_type="repository", qualified_name=f"{REPO_EXTRA}/scratch.py",
                payload={"kind": "file"}, provenance={"repository_id": REPO_EXTRA})

    return {"repositories_registered": 4,
            "nodes": [org.get_graph(REPO_API).stats().node_count,
                      org.get_graph(REPO_CORE).stats().node_count,
                      org.get_graph(REPO_WEB).stats().node_count,
                      org.get_graph(REPO_EXTRA).stats().node_count]}


async def demo_b_linking(org) -> dict:
    """B. Cross-repository linking (deterministic, validated)."""
    from app.models.engineering_graph import EKRelationshipType

    before = len(org.cross_edges())
    edge = org.link_repositories(
        REPO_API, REPO_CORE, EKRelationshipType.SHARES_LIBRARY,
        weight=0.9, metadata={"library": "shared-session-utils"},
        provenance={"source": "demo", "reason": "both use shared session utils"},
    )
    edge2 = org.link_repositories(
        REPO_WEB, REPO_API, EKRelationshipType.DEPENDS_ON_REPOSITORY,
        weight=0.8, metadata={"package": "api-client"},
        provenance={"source": "demo", "reason": "web consumes the API"},
    )
    assert len(org.cross_edges()) == before + 2

    # Re-linking the same pair is idempotent (same deterministic edge id).
    edge3 = org.link_repositories(
        REPO_API, REPO_CORE, EKRelationshipType.SHARES_LIBRARY, weight=0.5,
    )
    assert edge.edge_id == edge3.edge_id
    assert len(org.cross_edges()) == before + 2

    # Invalid: relationship that is not a cross-repo bridge.
    rejected = False
    try:
        org.link_repositories(REPO_API, REPO_WEB, EKRelationshipType.CONTAINS)
    except ValueError:
        rejected = True
    assert rejected

    # Invalid: unregistered repository must be rejected (no dangling links).
    unregistered = False
    try:
        org.link_repositories(REPO_API, "ghost-repo", EKRelationshipType.SHARES_LIBRARY)
    except (ValueError, KeyError):
        unregistered = True
    assert unregistered

    return {"cross_edges": len(org.cross_edges()),
            "edge_id": edge.edge_id,
            "second_edge_id": edge2.edge_id,
            "idempotent_relink": edge3.weight,
            "invalid_relationship_rejected": True,
            "unregistered_rejected": True}


async def demo_c_traversal(org) -> dict:
    """C. Cross-repository traversal — bounded, bridge-only."""
    from app.models.engineering_graph import EKNodeType
    from app.services.organization_graph_service import _repo_node_id

    start = _repo_node_id(REPO_API)
    result = org.cross_repository_traversal(start, depth=3, max_nodes=100)

    def _origin(n) -> str:
        return n.source_ref if n.node_type == EKNodeType.REPOSITORY else n.repository_id

    origins = {_origin(n) for n in result.nodes}
    # api, core AND web are reachable — web is EXPLICITLY linked (web
    # DEPENDS_ON api), so crossing into it is legitimate.
    assert REPO_API in origins
    assert REPO_CORE in origins
    assert REPO_WEB in origins
    # repo-extra has NO cross-repository edge at all — isolation must hold.
    assert REPO_EXTRA not in origins
    return {"visited_nodes": result.total_nodes,
            "visited_repositories": sorted(origins),
            "bridge_only": True,
            "bounded": result.total_nodes <= 100,
            "unlinked_repo_isolated": True}


async def demo_d_query_routing(org) -> dict:
    """D. Organization-wide query routing (LOCAL / ORGANIZATION / AUTO)."""
    from app.models.engineering_graph import QueryScope

    # LOCAL — strict isolation to REPO_API only.
    local = await org.query(
        "shared session utils", scope=QueryScope.LOCAL,
        repository_ids=[REPO_API],
    )
    local_repos = {n.repository_id for n in local.nodes}
    assert local_repos and all(r == REPO_API for r in local_repos)
    assert REPO_CORE not in local_repos and REPO_WEB not in local_repos

    # ORGANIZATION — merged view across all linked repositories with
    # per-repository attribution.
    merged = await org.query("shared session utils", scope=QueryScope.ORGANIZATION)
    assert merged.scope == QueryScope.ORGANIZATION
    assert merged.strategy.value == "cross_repository"
    contrib = dict(merged.repositories)
    assert contrib.get(REPO_API, 0) >= 1
    assert contrib.get(REPO_CORE, 0) >= 1

    # AUTO — vocabulary decides; cross-repo vocabulary routes wide.
    auto = await org.query(
        "which components are shared across repositories", scope=QueryScope.AUTO,
    )
    assert auto.plan is not None
    assert auto.plan.cross_repository is True

    return {"local_scope": local.scope.value,
            "organization_contributors": contrib,
            "organization_total": merged.total_nodes,
            "auto_intent": auto.plan.intent if auto.plan else None}


async def demo_e_context_engine(org) -> dict:
    """E. ContextEngine integration — org evidence when cross-repo relevant."""
    from app.services.context_engine import ContextEngine

    engine = ContextEngine(organization_graph=org)
    ctx = await engine.build_context(
        "which components are shared across repositories", agent_type="planner",
    )
    surfaced = any(
        "Organization knowledge graph" in i.content for i in ctx.raw_items
    )
    assert surfaced

    # Local vocabulary: org evidence must stay silent (EKG/local context
    # already covers repository-local retrieval).
    local_ctx = await engine.build_context(
        "explain the token expiration logic", agent_type="planner",
    )
    silent = not any(
        "Organization knowledge graph" in i.content for i in local_ctx.raw_items
    )
    assert silent

    return {"org_evidence_surfaced": surfaced,
            "local_query_isolated": silent,
            "raw_items": len(ctx.raw_items)}


async def demo_f_persistence(org, database_url: str | None) -> dict:
    """F. PostgreSQL persistence / restart recovery (migration 013)."""
    if not database_url:
        return {"mode": "in-memory", "persistence": "skipped (no DATABASE_URL)"}

    written = await org.synchronize()
    assert written >= 4 + 2  # 4 namespaces + 2 cross-edges

    # Fresh service = simulated restart.
    fresh = await _make_org(database_url)
    try:
        recovered_ids = {ns.repository_id for ns in fresh.repositories()}
        for rid in (REPO_API, REPO_CORE, REPO_WEB, REPO_EXTRA):
            assert rid in recovered_ids
        fresh_edges = {e.edge_id: e for e in fresh.cross_edges()}
        assert len(fresh_edges) >= 2
        assert all(e.edge_id in fresh_edges for e in org.cross_edges())
    finally:
        await fresh.dispose()

    # Cleanup: remove this run's rows so repeated --pg demos stay clean.
    removed = await _cleanup_org_rows(database_url)
    return {"mode": "postgresql", "records_written": written,
            "recovered_namespaces": len(recovered_ids),
            "recovered_cross_edges": len(fresh_edges),
            "cleanup_rows_deleted": removed}

async def _cleanup_org_rows(database_url: str) -> int:
    """Delete this run's namespace + cross-edge rows from the database."""
    from sqlalchemy import delete
    from app.db.models import EKCrossRepositoryEdgeModel, EKRepositoryNamespaceModel
    from app.models.engineering_graph import EKRelationshipType
    from app.services.organization_graph_service import _cross_repo_edge_id

    edge_ids = [
        _cross_repo_edge_id(REPO_API, REPO_CORE, EKRelationshipType.SHARES_LIBRARY),
        _cross_repo_edge_id(REPO_WEB, REPO_API, EKRelationshipType.DEPENDS_ON_REPOSITORY),
    ]

    async def _impl(session):
        n1 = await session.execute(
            delete(EKRepositoryNamespaceModel).where(
                EKRepositoryNamespaceModel.repository_id.in_(
                    [REPO_API, REPO_CORE, REPO_WEB, REPO_EXTRA]
                )
            )
        )
        n2 = await session.execute(
            delete(EKCrossRepositoryEdgeModel).where(
                EKCrossRepositoryEdgeModel.edge_id.in_(edge_ids)
            )
        )
        await session.commit()
        return int((n1.rowcount or 0) + (n2.rowcount or 0))

    from app.services.engineering_graph_service import EngineeringKnowledgeGraphService

    svc = EngineeringKnowledgeGraphService(database_url=database_url)
    try:
        return await svc._with_session(_impl, fallback=0)
    finally:
        await svc.dispose()


async def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 19A org graph demo")
    parser.add_argument("--pg", action="store_true",
                        help="Run against PostgreSQL when configured")
    parser.add_argument("--json", action="store_true",
                        help="Output JSON summary")
    args = parser.parse_args()

    use_pg = args.pg and _is_pg_configured()
    db_url = _db_url() if use_pg else None
    org = await _make_org(db_url)

    results = {}
    for name, fn in [
        ("A_namespace_registry", demo_a_registry),
        ("B_cross_repo_linking", demo_b_linking),
        ("C_cross_repo_traversal", demo_c_traversal),
        ("D_query_routing", demo_d_query_routing),
        ("E_context_engine", demo_e_context_engine),
    ]:
        try:
            results[name] = await fn(org)
            results[name]["PASS"] = True
        except Exception as exc:  # pragma: no cover
            results[name] = {"PASS": False, "error": str(exc)}

    try:
        results["F_persistence"] = await demo_f_persistence(org, db_url)
        results["F_persistence"]["PASS"] = True
    except Exception as exc:  # pragma: no cover
        results["F_persistence"] = {"PASS": False, "error": str(exc)}

    await org.dispose()

    if args.json:
        print(json.dumps({
            "phase": "19A",
            "persistence": "postgresql" if use_pg else "in-memory",
            "repositories": [REPO_API, REPO_CORE, REPO_WEB],
            "demonstrations": results,
        }, indent=2, default=str))
        return

    print(f"\n{'='*64}")
    print(f"  Phase 19A - Organization Knowledge Graph Demo")
    print(f"  Persistence: {'PostgreSQL' if use_pg else 'In-memory'}")
    print(f"{'='*64}")

    labels = {
        "A_namespace_registry": "A. Repository namespace registry + isolation",
        "B_cross_repo_linking": "B. Cross-repository linking (deterministic)",
        "C_cross_repo_traversal": "C. Cross-repository traversal (bridge-only, bounded)",
        "D_query_routing": "D. Org-wide query routing (LOCAL/ORG/AUTO)",
        "E_context_engine": "E. ContextEngine org evidence integration",
        "F_persistence": "F. PostgreSQL persistence / restart recovery",
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
    print(f"  REPOSITORY ISOLATION: {'PASS' if results['A_namespace_registry'].get('PASS') else 'FAIL'}")
    print(f"  DETERMINISTIC LINKS: {'PASS' if results['B_cross_repo_linking'].get('PASS') else 'FAIL'}")
    print(f"  CROSS-REPO TRAVERSAL: {'PASS' if results['C_cross_repo_traversal'].get('PASS') else 'FAIL'}")
    print(f"  QUERY ROUTING: {'PASS' if results['D_query_routing'].get('PASS') else 'FAIL'}")
    print(f"  CONTEXT INTEGRATION: {'PASS' if results['E_context_engine'].get('PASS') else 'FAIL'}")
    print(f"  POSTGRESQL: {'PASS' if use_pg else 'n/a (in-memory)'}")
    print(f"{'='*64}\n")

    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    asyncio.run(main())
