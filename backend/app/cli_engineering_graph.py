"""
Phase 18 CLI commands for the Engineering Knowledge Graph (EKG).

Usage:
    python -m app.cli graph query <query>
    python -m app.cli graph explain <node_id>
    python -m app.cli graph history <node_id>
    python -m app.cli graph neighborhood <node_id>
    python -m app.cli graph version

Phase 19A — Organization Knowledge Graph:
    python -m app.cli graph org-stats
    python -m app.cli graph org-repositories
    python -m app.cli graph org-cross-edges
    python -m app.cli graph org-query <query> [--scope auto|local|organization] [--repository-id <id>]
    python -m app.cli graph org-traversal <node_id> [--depth N]
    python -m app.cli graph org-acquire-multi --manifest <json> [--ingest]
"""

from __future__ import annotations

import sys
from typing import Optional


def _ensure_utf8_stdout() -> None:
    """Force UTF-8 stdout so graph payloads cannot crash the CLI on a
    Windows cp1252 console."""
    try:
        sys.stdout.reconfigure(  # type: ignore[attr-defined]
            encoding="utf-8", errors="replace"
        )
    except (AttributeError, ValueError):
        pass


def add_cli_commands(parent_parser) -> None:
    """Add Phase 18 EKG CLI commands to the argument parser."""
    subparsers = parent_parser  # Passed as subparsers from main cli

    graph_parser = subparsers.add_parser(
        "graph", help="Engineering Knowledge Graph operations (Phase 18)"
    )
    graph_sub = graph_parser.add_subparsers(
        dest="graph_command", help="Graph sub-command"
    )

    q_parser = graph_sub.add_parser("query", help="Query the engineering knowledge graph")
    q_parser.add_argument("query", type=str, help="Natural-language or keyword query")
    q_parser.add_argument("--limit", type=int, default=10, help="Max result nodes")
    q_parser.add_argument("--json", action="store_true", help="Output raw JSON")

    e_parser = graph_sub.add_parser("explain", help="Provenance + related evidence for a node")
    e_parser.add_argument("node_id", type=str, help="Node ID, e.g. RUN-ABC123:goal")
    e_parser.add_argument("--json", action="store_true", help="Output raw JSON")

    h_parser = graph_sub.add_parser("history", help="Temporal history of a node")
    h_parser.add_argument("node_id", type=str, help="Node ID")
    h_parser.add_argument("--json", action="store_true", help="Output raw JSON")

    n_parser = graph_sub.add_parser("neighborhood", help="Bounded traversal around a node")
    n_parser.add_argument("node_id", type=str, help="Node ID")
    n_parser.add_argument("--depth", type=int, default=2, help="Traversal depth")
    n_parser.add_argument("--max-nodes", type=int, default=50, help="Max nodes")
    n_parser.add_argument("--json", action="store_true", help="Output raw JSON")

    v_parser = graph_sub.add_parser("version", help="Current graph version + stats")
    v_parser.add_argument("--json", action="store_true", help="Output raw JSON")

    # Phase 19A — Organization Knowledge Graph sub-commands.
    os_parser = graph_sub.add_parser("org-stats", help="Organization-wide graph stats")
    os_parser.add_argument("--json", action="store_true", help="Output raw JSON")

    or_parser = graph_sub.add_parser("org-repositories", help="List registered namespaces")
    or_parser.add_argument("--q", type=str, default="",
                           help="Case-insensitive substring filter on id/name/path")
    or_parser.add_argument("--organization", type=str, default="",
                           help="Filter by organization_id")
    or_parser.add_argument("--limit", type=int, default=50, help="Max rows (1..250)")
    or_parser.add_argument("--offset", type=int, default=0, help="Pagination offset")
    or_parser.add_argument("--json", action="store_true", help="Output raw JSON")

    oc_parser = graph_sub.add_parser("org-cross-edges", help="List cross-repository edges")
    oc_parser.add_argument("--json", action="store_true", help="Output raw JSON")

    oq_parser = graph_sub.add_parser("org-query", help="Organization-wide query")
    oq_parser.add_argument("query", type=str, help="Natural-language or keyword query")
    oq_parser.add_argument("--scope", type=str, default="auto",
                          choices=["auto", "local", "organization"],
                          help="Retrieval scope")
    oq_parser.add_argument("--repository-id", type=str, default=None,
                          help="Namespace filter (scope=local)")
    oq_parser.add_argument("--limit", type=int, default=10, help="Max result nodes")
    oq_parser.add_argument("--json", action="store_true", help="Output raw JSON")

    ot_parser = graph_sub.add_parser("org-traversal",
                                     help="Bounded cross-repository traversal")
    ot_parser.add_argument("node_id", type=str, help="Node ID")
    ot_parser.add_argument("--depth", type=int, default=2, help="Traversal depth")
    ot_parser.add_argument("--max-nodes", type=int, default=50, help="Max nodes")
    ot_parser.add_argument("--json", action="store_true", help="Output raw JSON")

    # Phase 19C — multi-repo acquisition → organization graph.
    oam_parser = graph_sub.add_parser(
        "org-acquire-multi",
        help="Acquire + link multiple repositories into the org graph (Phase 19C)",
    )
    oam_parser.add_argument(
        "--manifest", type=str, required=True,
        help="Path to a JSON manifest: list of repo spec objects "
             "(repository_id, source, path|owner+repo, relationships[]).",
    )
    oam_parser.add_argument("--ingest", action="store_true", default=False,
                            help="Seed per-repo FILE evidence from the checkout")
    oam_parser.add_argument("--json", action="store_true", help="Output raw JSON")


def _get_service():
    from app.services.engineering_graph_service import (
        EngineeringKnowledgeGraphService,
    )

    return EngineeringKnowledgeGraphService()


def _get_org_service():
    from app.services.organization_graph_service import (
        OrganizationKnowledgeGraphService,
    )

    return OrganizationKnowledgeGraphService()


def _node_lines(node) -> str:
    return (
        f"{node.node_type.value:<12} {node.name[:44]:<46} "
        f"status={node.status.value:<9} v{node.graph_version}"
    )


async def run_graph_query(query: str, limit: int = 10, json_output: bool = False) -> None:
    """Query the engineering knowledge graph via the query planner."""
    _ensure_utf8_stdout()
    svc = _get_service()
    result = await svc.query(query, limit=limit)

    if json_output:
        import json

        print(json.dumps(result.model_dump(), indent=2, default=str))
        return

    print(f"\n{'='*60}")
    print(f"  Graph Query: {query[:70]}")
    print(f"  Strategy: {result.strategy.value} | Version: {result.version} | "
          f"Total: {result.total_nodes} nodes, {len(result.edges)} edges")
    if result.semantic_used:
        print(f"  Semantic: {result.semantic_matches} match(es), "
              f"top score {round(result.semantic_top_score, 4)}")
    print(f"  Plan: {result.plan.summary() if result.plan else 'n/a'}")
    print(f"{'='*60}\n")

    if not result.nodes:
        print("  No matching nodes.")
        print(f"{'='*60}\n")
        return

    for i, n in enumerate(result.nodes[:limit], 1):
        print(f"  [{i}] {_node_lines(n)}")
        src = (n.source_ref or "").split(":")[0][:60]
        if src:
            print(f"      source: {src}")
        prov = n.provenance or {}
        if prov:
            keys = ", ".join(sorted(prov.keys())[:6])
            print(f"      provenance: {keys}")

    print(f"  Truncated: {result.truncated}")
    print(f"{'='*60}\n")


async def run_graph_explain(node_id: str, json_output: bool = False) -> None:
    """Show provenance + related evidence for a graph node."""
    _ensure_utf8_stdout()
    svc = _get_service()
    explanation = svc.explain(node_id)

    if json_output:
        import json

        print(json.dumps(explanation, indent=2, default=str))
        return

    print(f"\n{'='*60}")
    print(f"  Explain: {node_id}")
    print(f"{'='*60}\n")

    if not explanation.get("found"):
        print(f"  Node {node_id} not found in the graph.")
        print(f"{'='*60}\n")
        return

    node = explanation.get("node") or {}
    print(f"  Name: {node.get('name', '')[:80]}")
    print(f"  Type: {node.get('node_type')} | Status: {node.get('status')} | "
          f"Version: {node.get('graph_version')}")

    prov = explanation.get("provenance") or {}
    print(f"\n  Provenance ({len(prov)} sources):")
    for src_type, items in prov.items():
        print(f"    [{src_type}]")
        for it in (items or [])[:5]:
            print(f"      - {str(it)[:150]}")

    related = explanation.get("related") or []
    print(f"\n  Related evidence ({len(related)}):")
    for r in related[:10]:
        print(f"    - {r.get('node_id')} "
              f"({r.get('relationship', '')}) {r.get('name', '')[:60]}")

    print(f"{'='*60}\n")


async def run_graph_history(node_id: str, json_output: bool = False) -> None:
    """Show the temporal history of a graph node."""
    _ensure_utf8_stdout()
    svc = _get_service()
    history = svc.history(node_id)

    if json_output:
        import json

        print(json.dumps(history.model_dump(), indent=2, default=str))
        return

    print(f"\n{'='*60}")
    print(f"  History: {node_id}")
    print(f"{'='*60}\n")

    if not history.entries:
        print(f"  No history entries for {node_id}.")
        print(f"{'='*60}\n")
        return

    print(f"  Current version: {history.current_version} | "
          f"Entries: {len(history.entries)}")
    print()
    for e in history.entries[:30]:
        print(f"  v{e.graph_version:<4} {e.status.value:<10} {e.created_at}")

    print(f"{'='*60}\n")


async def run_graph_neighborhood(
    node_id: str, depth: int = 2, max_nodes: int = 50, json_output: bool = False
) -> None:
    """Bounded traversal around a graph node."""
    _ensure_utf8_stdout()
    svc = _get_service()
    result = svc.neighborhood(node_id, depth=depth, max_nodes=max_nodes)

    if json_output:
        import json

        print(json.dumps(result.model_dump(), indent=2, default=str))
        return

    print(f"\n{'='*60}")
    print(f"  Neighborhood: {node_id} (depth {depth})")
    print(f"  Version: {result.version} | Nodes: {result.total_nodes} | "
          f"Edges: {len(result.edges)}")
    print(f"{'='*60}\n")

    if not result.nodes:
        print(f"  Node {node_id} not found or no neighbors.")
        print(f"{'='*60}\n")
        return

    for i, n in enumerate(result.nodes[:max_nodes], 1):
        marker = "  *" if n.node_id == node_id else f"[{i}]"
        print(f"  {marker} {_node_lines(n)}")

    print(f"\n  Edges ({len(result.edges)}):")
    for e in result.edges[:25]:
        print(f"    {e.source_id} -[{e.relationship.value}]-> {e.target_id}")
    print(f"  Truncated: {result.truncated}")
    print(f"{'='*60}\n")


async def run_graph_version(json_output: bool = False) -> None:
    """Show the current graph version + statistics."""
    _ensure_utf8_stdout()
    svc = _get_service()
    stats = svc.stats()
    versions = svc.version_history(limit=10)

    if json_output:
        import json

        print(json.dumps(
            {
                "stats": stats.model_dump(),
                "history": [v.model_dump() for v in versions],
            },
            indent=2, default=str,
        ))
        return

    print(f"\n{'='*60}")
    print(f"  Engineering Knowledge Graph - Version {stats.version}")
    print(f"{'='*60}\n")
    print(f"  Nodes:       {stats.node_count}")
    print(f"  Edges:       {stats.edge_count}")
    print(f"  Runs:        {stats.run_count}")
    print(f"  Repos:       {stats.repository_count}")
    print(f"  By type:     {len(stats.node_types)} types")
    for t, c in sorted(stats.node_types.items(), key=lambda x: -x[1])[:10]:
        print(f"    {t:<16} {c}")
    print(f"  By relation: {len(stats.relationship_types)} relationships")
    sem = svc.semantic_stats()
    print(f"  Semantic index: {sem.get('embedded')}/{sem.get('nodes')} nodes "
          f"({sem.get('provider', '')}@{sem.get('dimension', 0)})")
    print(f"  Version history ({len(versions)}):")
    for v in versions:
        print(f"    v{v.version}  {v.timestamp}  +{len(v.updated_nodes)} nodes "
              f"+{len(v.updated_edges)} edges  run={v.run_id or '-'}")
    print(f"{'='*60}\n")


# ── Phase 19A: Organization Knowledge Graph ────────────────────


async def run_graph_org_stats(json_output: bool = False) -> None:
    """Show organization-wide graph statistics."""
    _ensure_utf8_stdout()
    org = _get_org_service()
    stats = org.stats()

    if json_output:
        import json

        print(json.dumps(stats.model_dump(), indent=2, default=str))
        return

    print(f"\n{'='*60}")
    print(f"  Organization Knowledge Graph")
    print(f"{'='*60}\n")
    print(f"  Organizations: 1 (default)")
    print(f"  Repositories:  {stats.repository_count}")
    print(f"  Nodes:         {stats.node_count}")
    print(f"  Edges:         {stats.edge_count}")
    print(f"  Cross-repo edges: {stats.cross_edge_count}")
    for rel, count in sorted(
        stats.cross_relationship_types.items(), key=lambda x: -x[1]
    )[:12]:
        print(f"    {rel:<32} {count}")
    print(f"  Repositories:  {', '.join(stats.repositories[:20]) or '-'}")
    print(f"  Updated:       {stats.last_updated}")
    print(f"{'='*60}\n")


async def run_graph_org_repositories(
    json_output: bool = False,
    q: str = "",
    organization: str = "",
    limit: int = 50,
    offset: int = 0,
) -> None:
    """List registered repository namespaces (search + pagination).

    Phase 20A6 dashboard selector support: ``q`` filters by repository_id /
    name / path (case-insensitive substring); ``organization`` filters by
    organization_id; ``limit``/``offset`` paginate large orgs.
    """
    _ensure_utf8_stdout()
    org = _get_org_service()
    repos = org.repositories()
    query = q.strip().lower()
    org_filter = organization.strip().lower()
    matched = []
    for ns in repos:
        if org_filter and (ns.organization_id or "").lower() != org_filter:
            continue
        if query:
            haystack = " ".join([
                ns.repository_id, ns.name or "", ns.path or "",
            ]).lower()
            if query not in haystack:
                continue
        matched.append(ns)
    limit = min(max(limit, 1), 250)
    offset = max(offset, 0)
    page = matched[offset:offset + limit]

    if json_output:
        import json

        print(json.dumps(
            {
                "repositories": [r.model_dump() for r in page],
                "count": len(page),
                "total": len(matched),
                "limit": limit,
                "offset": offset,
            },
            indent=2, default=str,
        ))
        return

    print(f"\n{'='*60}")
    print(f"  Registered Repositories ({len(matched)} matched, showing "
          f"{offset + 1}-{offset + len(page)})")
    print(f"{'='*60}\n")
    for ns in page:
        print(f"  {ns.repository_id:<20} {ns.name[:40]:<42} {ns.source_type}")
        if ns.path:
            print(f"    path: {ns.path[:100]}")
    print(f"{'='*60}\n")


async def run_graph_org_cross_edges(json_output: bool = False) -> None:
    """List explicit cross-repository edges."""
    _ensure_utf8_stdout()
    org = _get_org_service()
    edges = org.cross_edges()

    if json_output:
        import json

        print(json.dumps({"cross_edges": [e.model_dump() for e in edges]},
                         indent=2, default=str))
        return

    print(f"\n{'='*60}")
    print(f"  Cross-Repository Edges ({len(edges)})")
    print(f"{'='*60}\n")
    for e in edges[:50]:
        print(f"  {e.source_repository_id:<20} -[{e.relationship.value}]-> "
              f"{e.target_repository_id:<20} w={round(e.weight, 2)}")
    if not edges:
        print("  No cross-repository edges. Register + link repositories first.")
    print(f"{'='*60}\n")


async def run_graph_org_query(
    query: str, scope: str = "auto", repository_id: Optional[str] = None,
    limit: int = 10, json_output: bool = False,
) -> None:
    """Organization-wide query with scope routing."""
    _ensure_utf8_stdout()
    from app.models.engineering_graph import QueryScope

    org = _get_org_service()
    result = await org.query(
        query,
        limit=limit,
        scope=QueryScope(scope.lower()),
        repository_ids=[repository_id] if repository_id else None,
    )

    if json_output:
        import json

        print(json.dumps(result.model_dump(), indent=2, default=str))
        return

    print(f"\n{'='*60}")
    print(f"  Org Query: {query[:70]}")
    print(f"  Scope: {result.scope.value} | Strategy: {result.strategy.value} | "
          f"Version: {result.version} | Total: {result.total_nodes}")
    contrib = ", ".join(f"{r}:{c}" for r, c in list(result.repositories.items())[:10])
    if contrib:
        print(f"  Repositories contributing: {contrib}")
    print(f"  Plan: {result.plan.summary() if result.plan else 'n/a'}")
    print(f"{'='*60}\n")

    if not result.nodes:
        print("  No matching nodes.")
        print(f"{'='*60}\n")
        return

    for i, n in enumerate(result.nodes[:limit], 1):
        repo = getattr(n, "repository_id", "default")
        print(f"  [{i}] [{n.node_type.value}] {n.name[:44]} (repo:{repo})")

    print(f"  Truncated: {result.truncated}")
    print(f"{'='*60}\n")


async def run_graph_org_traversal(
    node_id: str, depth: int = 2, max_nodes: int = 50, json_output: bool = False
) -> None:
    """Bounded cross-repository traversal from a node."""
    _ensure_utf8_stdout()
    org = _get_org_service()
    result = org.cross_repository_traversal(node_id, depth=depth, max_nodes=max_nodes)

    if json_output:
        import json

        print(json.dumps(result.model_dump(), indent=2, default=str))
        return

    print(f"\n{'='*60}")
    print(f"  Cross-Repo Traversal: {node_id} (depth {depth})")
    print(f"  Version: {result.version} | Nodes: {result.total_nodes} | "
          f"Edges: {len(result.edges)}")
    print(f"{'='*60}\n")

    if not result.nodes:
        print(f"  Node {node_id} not found.")
        print(f"{'='*60}\n")
        return

    for i, n in enumerate(result.nodes[:max_nodes], 1):
        repo = getattr(n, "repository_id", "default")
        print(f"  [{i}] [{n.node_type.value}] {n.name[:44]} (repo:{repo})")

    print(f"  Truncated: {result.truncated}")
    print(f"{'='*60}\n")


async def run_graph_org_acquire_multi(
    manifest_path: str, ingest: bool = False, json_output: bool = False
) -> None:
    """Acquire + link multiple repositories into the org graph (Phase 19C).

    The manifest is a JSON list of repository spec objects::
        [
          {"repository_id": "acme-api", "source": "local", "path": "/path/to/api"},
          {"repository_id": "acme-web", "source": "local", "path": "/path/to/web",
           "relationships": [{"target_repository_id": "acme-api",
                              "relationship": "depends_on_repository"}]}
        ]
    """
    _ensure_utf8_stdout()
    import json as _json
    import os as _os
    from app.models.engineering_graph import MultiRepoAcquisitionSpec

    if not _os.path.isfile(manifest_path):
        print(f"Manifest not found: {manifest_path}")
        return
    try:
        with open(manifest_path, "r", encoding="utf-8") as fh:
            raw = _json.load(fh)
    except _json.JSONDecodeError as exc:
        print(f"Invalid manifest JSON: {exc}")
        return
    if not isinstance(raw, list):
        print("Manifest must be a JSON list of repository specs.")
        return
    try:
        specs = [MultiRepoAcquisitionSpec(**s) for s in raw]
    except Exception as exc:
        print(f"Invalid manifest: {exc}")
        return

    org = _get_org_service()
    from app.services.acquisition import RepositoryAcquisitionService

    result = await org.acquire_and_link_repositories(
        specs, acquisition_service=RepositoryAcquisitionService(), ingest=ingest
    )

    if json_output:
        print(_json.dumps(result, indent=2, default=str))
        return

    print(f"\n{'='*60}")
    print(f"  Multi-Repo Acquisition — Organization Graph")
    print(f"{'='*60}\n")
    print(f"  Repositories acquired: {result['repositories_acquired']}")
    print(f"  Cross-repository edges: {result['relationships']}")
    print(f"  Evidence files seeded: {result['ingested_files']}")
    print(f"  Persisted records: {result['persisted_records']}")
    print(f"\n  Registered namespaces:")
    for ns in result["namespaces"]:
        print(f"    {ns['repository_id']:<20} {ns.get('name', '')[:30]} ({ns.get('source', '')})")
    if result["cross_edges"]:
        print(f"\n  Explicit bridges:")
        for e in result["cross_edges"]:
            print(f"    {e['source_repository_id']:<18} -[{e['relationship']}]-> "
                  f"{e['target_repository_id']:<18} w={round(e.get('weight', 1.0), 2)}")
    print(f"\n  Org version: {org.current_version()}")
    print(f"{'='*60}\n")
