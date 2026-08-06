"""
Phase 18 — Engineering Knowledge Graph API.

Bounded, evidence-only endpoints (§16):

    GET /graph/query          — query the graph via KnowledgeQueryPlanner
    GET /graph/node/{id}      — node information
    GET /graph/history/{id}   — temporal history of a node
    GET /graph/neighborhood/{id} — bounded bidirectional traversal
    GET /graph/explain/{id}   — provenance + related evidence
    GET /graph/version        — current graph version + stats

Responses expose ONLY verified engineering evidence, decisions, and
provenance — never chain-of-thought or internal reasoning.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException

from app.models.base import Response
from app.models.engineering_graph import (
    MAX_EXPLAIN_EVIDENCE,
    MAX_NEIGHBORHOOD_NODES,
    MAX_QUERY_RESULTS,
    MAX_REPOSITORIES_PER_ORG,
)

router = APIRouter(prefix="/api/v1/graph", tags=["engineering-graph"])

_service = None


def _get_service() -> Any:
    """Lazily instantiate the EngineeringKnowledgeGraphService."""
    global _service
    if _service is None:
        from app.services.engineering_graph_service import (
            EngineeringKnowledgeGraphService,
        )

        _service = EngineeringKnowledgeGraphService()
    return _service


# ── Bounded serializers ─────────────────────────────────────────


def _node_to_api(node: Any) -> Dict[str, Any]:
    return {
        "node_id": node.node_id,
        "node_type": node.node_type.value,
        "name": node.name[:200],
        "qualified_name": node.qualified_name[:200],
        "kind": node.kind[:50],
        "source_ref": node.source_ref[:100],
        "source_type": node.source_type,
        "status": node.status.value,
        "graph_version": node.graph_version,
        "repository_id": getattr(node, "repository_id", "default"),
        "payload": {
            k: (str(v)[:200] if not isinstance(v, (int, float, bool)) else v)
            for k, v in (node.payload or {}).items()
        } if node.payload else {},
        "provenance": {
            k: (str(v)[:200] if not isinstance(v, (int, float, bool)) else v)
            for k, v in (node.provenance or {}).items()
        } if node.provenance else {},
        "created_at": node.created_at,
    }


def _edge_to_api(edge: Any) -> Dict[str, Any]:
    return {
        "edge_id": edge.edge_id,
        "source_id": edge.source_id,
        "target_id": edge.target_id,
        "relationship": edge.relationship.value,
        "weight": round(edge.weight, 2),
        "graph_version": edge.graph_version,
        "created_at": edge.created_at,
    }


def _version_to_api(version: Any) -> Dict[str, Any]:
    return {
        "version": version.version,
        "run_id": version.run_id,
        "summary": version.summary[:200],
        "updated_nodes": len(version.updated_nodes),
        "updated_edges": len(version.updated_edges),
        "superseded_nodes": len(version.superseded_node_ids),
        "timestamp": version.timestamp,
    }


# ── Endpoints ───────────────────────────────────────────────────


@router.get("/query", response_model=Response)
async def graph_query(
    q: str = "",
    limit: int = 10,
) -> Dict[str, Any]:
    """Query the engineering knowledge graph (bounded, evidence-only)."""
    try:
        service = _get_service()
        limit = min(max(limit, 1), MAX_QUERY_RESULTS)
        result = await service.query(q, limit=limit)
        return {
            "success": True,
            "data": {
                "query": result.query[:200],
                "strategy": result.strategy.value,
                "nodes": [_node_to_api(n) for n in result.nodes[:MAX_QUERY_RESULTS]],
                "edges": [_edge_to_api(e) for e in result.edges[:100]],
                "truncated": result.truncated,
                "total_nodes": result.total_nodes,
                "version": result.version,
                "plan": result.plan.summary() if result.plan else None,
                "semantic": {
                    "used": result.semantic_used,
                    "matches": result.semantic_matches,
                    "top_score": round(result.semantic_top_score, 4),
                },
            },
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/node/{node_id}", response_model=Response)
async def graph_node(node_id: str) -> Dict[str, Any]:
    """Get information about a single graph node."""
    try:
        service = _get_service()
        node = service.get_node(node_id)
        if node is None:
            raise HTTPException(status_code=404, detail=f"Node {node_id} not found")
        edges_out = [_edge_to_api(e) for e in service.get_edges(node_id)[:20]]
        edges_in = [_edge_to_api(e) for e in service.get_reverse_edges(node_id)[:20]]
        return {
            "success": True,
            "data": {
                "node": _node_to_api(node),
                "outgoing_edges": edges_out,
                "incoming_edges": edges_in,
            },
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/history/{node_id}", response_model=Response)
async def graph_history(node_id: str) -> Dict[str, Any]:
    """Get the temporal history of a node across graph versions."""
    try:
        service = _get_service()
        node = service.get_node(node_id)
        if node is None:
            raise HTTPException(status_code=404, detail=f"Node {node_id} not found")
        history = service.history(node_id)
        return {
            "success": True,
            "data": {
                "node_id": node_id,
                "current": _node_to_api(node),
                "entries": [
                    {
                        "node_id": h.node_id,
                        "graph_version": h.graph_version,
                        "status": h.status.value,
                        "payload_keys": sorted((h.payload or {}).keys())[:20],
                        "created_at": h.created_at,
                    }
                    for h in history.entries[:100]
                ],
            },
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/neighborhood/{node_id}", response_model=Response)
async def graph_neighborhood(
    node_id: str,
    depth: int = 2,
    max_nodes: int = 50,
) -> Dict[str, Any]:
    """Bounded bidirectional traversal around a node."""
    try:
        service = _get_service()
        node = service.get_node(node_id)
        if node is None:
            raise HTTPException(status_code=404, detail=f"Node {node_id} not found")
        depth = min(max(depth, 1), 5)
        max_nodes = min(max(max_nodes, 1), MAX_NEIGHBORHOOD_NODES)
        result = service.neighborhood(node_id, depth=depth, max_nodes=max_nodes)
        return {
            "success": True,
            "data": {
                "root": node_id,
                "depth": depth,
                "nodes": [_node_to_api(n) for n in result.nodes[:MAX_QUERY_RESULTS]],
                "edges": [_edge_to_api(e) for e in result.edges[:100]],
                "truncated": result.truncated,
                "total_nodes": result.total_nodes,
                "version": result.version,
            },
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/explain/{node_id}", response_model=Response)
async def graph_explain(node_id: str) -> Dict[str, Any]:
    """Provenance + related evidence for a node (§9, §11)."""
    try:
        service = _get_service()
        explanation = service.explain(node_id)
        if not explanation.get("found"):
            raise HTTPException(status_code=404, detail=f"Node {node_id} not found")
        explanation["related"] = explanation.get("related", [])[:MAX_EXPLAIN_EVIDENCE]
        return {"success": True, "data": explanation}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/version", response_model=Response)
async def graph_version() -> Dict[str, Any]:
    """Current graph version + stats + recent version history."""
    try:
        service = _get_service()
        stats = service.stats()
        return {
            "success": True,
            "data": {
                "version": stats.summary(),
                "history": [
                    _version_to_api(v) for v in service.version_history(limit=10)
                ],
            },
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/diff", response_model=Response)
async def graph_diff(
    from_version: int,
    to_version: Optional[int] = None,
) -> Dict[str, Any]:
    """Change-set between two graph versions (timeline comparison).

    Bounded, evidence-only: added/removed nodes + changed edges + a
    per-version breakdown. Used by the frontend graph timeline view.
    """
    try:
        service = _get_service()
        diff = service.diff_versions(from_version, to_version)
        return {"success": True, "data": diff}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Phase 19A: Organization Knowledge Graph ─────────────────────

_org_service = None


def _get_org_service() -> Any:
    """Lazily instantiate the OrganizationKnowledgeGraphService."""
    global _org_service
    if _org_service is None:
        from app.services.organization_graph_service import (
            OrganizationKnowledgeGraphService,
        )

        _org_service = OrganizationKnowledgeGraphService()
    return _org_service


@router.get("/org/stats", response_model=Response)
async def org_graph_stats() -> Dict[str, Any]:
    """Organization-wide graph statistics (bounded, evidence-only)."""
    try:
        stats = _get_org_service().stats()
        return {"success": True, "data": stats.summary()}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/org/repositories", response_model=Response)
async def org_graph_repositories(
    q: str = "",
    organization_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    """List registered repository namespaces with search + pagination.

    Phase 20A6 dashboard repository selector: ``q`` filters by name /
    repository_id / path (case-insensitive substring); ``organization_id``
    filters by namespace organization; ``limit``/``offset`` paginate so a
    large org never loads every repository at once. Returns a bounded,
    evidence-only payload (no credentials or hidden metadata).
    """
    try:
        org = _get_org_service()
        limit = min(max(limit, 1), MAX_QUERY_RESULTS)
        offset = max(offset, 0)
        query = q.strip().lower()
        org_filter = (organization_id or "").strip().lower()
        all_repos = org.repositories()
        matched = []
        for ns in all_repos:
            summary = ns.summary()
            if org_filter and summary.get("organization_id", "").lower() != org_filter:
                continue
            if query:
                haystack = " ".join([
                    summary.get("repository_id", ""),
                    summary.get("name", ""),
                    summary.get("path", ""),
                ]).lower()
                if query not in haystack:
                    continue
            matched.append(summary)
        page = matched[offset:offset + limit]
        return {
            "success": True,
            "data": {
                "repositories": page,
                "count": len(page),
                "total": len(matched),
                "limit": limit,
                "offset": offset,
            },
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/org/repositories/{repository_id}", response_model=Response)
async def org_graph_repository_stats(repository_id: str) -> Dict[str, Any]:
    """Per-repository EKG stats + dependency links (Phase 20A6).

    Powers the repository status cards' Engineering Knowledge Graph panel:
    node/edge/run counts scoped to the repository's own namespace, plus the
    explicit cross-repository links (dependencies) in both directions.
    Bounded + evidence-only; a namespace that is not registered returns 404.
    """
    try:
        org = _get_org_service()
        stats = org.repository_stats(repository_id)
        if stats is None:
            raise HTTPException(
                status_code=404, detail=f"Repository {repository_id} not registered"
            )
        ns = stats.get("namespace")
        namespace = {
            "repository_id": getattr(ns, "repository_id", repository_id),
            "organization_id": getattr(ns, "organization_id", "default"),
            "name": getattr(ns, "name", ""),
            "path": getattr(ns, "path", ""),
            "source_type": getattr(ns, "source_type", "local"),
        } if ns is not None else None
        return {
            "success": True,
            "data": {
                "repository_id": repository_id,
                "namespace": namespace,
                "node_count": stats.get("node_count", 0),
                "edge_count": stats.get("edge_count", 0),
                "run_count": stats.get("run_count", 0),
                "node_types": stats.get("node_types", {}),
                "outgoing_links": stats.get("outgoing_links", [])[:20],
                "incoming_links": stats.get("incoming_links", [])[:20],
            },
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/org/cross-edges", response_model=Response)
async def org_graph_cross_edges() -> Dict[str, Any]:
    """List explicit cross-repository edges (the only repo bridges)."""
    try:
        org = _get_org_service()
        edges = [e.summary() for e in org.cross_edges()[:MAX_QUERY_RESULTS]]
        return {"success": True, "data": {"cross_edges": edges}}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/org/query", response_model=Response)
async def org_graph_query(
    q: str = "",
    scope: str = "auto",
    repository_id: Optional[str] = None,
    limit: int = 10,
) -> Dict[str, Any]:
    """Organization-wide query with scope routing.

    scope: auto | local | organization. When `repository_id` is provided
    with scope=local the query is strictly isolated to that namespace.
    """
    try:
        org = _get_org_service()
        limit = min(max(limit, 1), MAX_QUERY_RESULTS)
        from app.models.engineering_graph import QueryScope

        scope_enum = QueryScope(scope.lower())
        repo_ids = [repository_id] if repository_id else None
        result = await org.query(
            q, limit=limit, scope=scope_enum, repository_ids=repo_ids,
        )
        return {
            "success": True,
            "data": {
                "query": result.query[:200],
                "strategy": result.strategy.value,
                "scope": result.scope.value,
                "repository_ids": result.repository_ids or [],
                "repositories": result.repositories,
                "nodes": [_node_to_api(n) for n in result.nodes[:MAX_QUERY_RESULTS]],
                "edges": [_edge_to_api(e) for e in result.edges[:100]],
                "truncated": result.truncated,
                "total_nodes": result.total_nodes,
                "version": result.version,
                "plan": result.plan.summary() if result.plan else None,
            },
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid scope: {exc}")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/org/traversal/{node_id}", response_model=Response)
async def org_graph_traversal(
    node_id: str,
    depth: int = 2,
    max_nodes: int = 50,
) -> Dict[str, Any]:
    """Bounded cross-repository traversal from a node."""
    try:
        org = _get_org_service()
        depth = min(max(depth, 1), 5)
        max_nodes = min(max(max_nodes, 1), MAX_NEIGHBORHOOD_NODES)
        result = org.cross_repository_traversal(
            node_id, depth=depth, max_nodes=max_nodes,
        )
        if not result.nodes:
            raise HTTPException(status_code=404, detail=f"Node {node_id} not found")
        return {
            "success": True,
            "data": {
                "root": node_id,
                "depth": depth,
                "nodes": [_node_to_api(n) for n in result.nodes[:MAX_QUERY_RESULTS]],
                "edges": [_edge_to_api(e) for e in result.edges[:100]],
                "truncated": result.truncated,
                "total_nodes": result.total_nodes,
                "version": result.version,
            },
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/org/repositories", response_model=Response)
async def org_graph_register_repository(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Register a repository namespace (deterministic, evidence-only)."""
    try:
        repository_id = str(payload.get("repository_id", "")).strip()
        if not repository_id:
            raise HTTPException(status_code=400, detail="repository_id is required")
        org = _get_org_service()
        ns = org.register_repository(
            repository_id,
            name=str(payload.get("name", ""))[:200],
            path=str(payload.get("path", ""))[:1024],
            source_type=str(payload.get("source_type", "local"))[:32],
            organization_id=str(payload.get("organization_id", "default"))[:64],
            metadata=payload.get("metadata") or {},
        )
        return {"success": True, "data": {"namespace": ns.summary()}}
    except HTTPException:
        raise
    except (ValueError, RuntimeError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/org/link", response_model=Response)
async def org_graph_link(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Create an explicit cross-repository edge (deterministic only).

    Only relationships in the explicit cross-repo registry are accepted —
    never LLM-inferred links.
    """
    try:
        source = str(payload.get("source_repository_id", ""))
        target = str(payload.get("target_repository_id", ""))
        relationship = str(payload.get("relationship", ""))
        weight = float(payload.get("weight", 1.0))
        org = _get_org_service()
        from app.models.engineering_graph import EKRelationshipType

        edge = org.link_repositories(
            source, target, EKRelationshipType(relationship),
            weight=weight,
            metadata=payload.get("metadata") or {},
            provenance=payload.get("provenance") or {},
        )
        return {"success": True, "data": {"cross_edge": edge.summary()}}
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/org/acquire-multi", response_model=Response)
async def org_graph_acquire_multi(payload: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Acquire multiple repositories and wire them into the organization graph.

    Manifest: a list of repository specs (Phase 19C). ``source=local`` uses an
    existing checkout at ``path`` (deterministic, no network); ``source=github``
    clones via the acquisition service. Each spec may declare explicit
    cross-repository relationships to link once registered.

    Evidence-only: only repository namespaces, declared links, and filesystem
    evidence nodes are surfaced — never chain-of-thought or credentials.
    """
    try:
        if not payload or len(payload) > MAX_REPOSITORIES_PER_ORG:
            raise HTTPException(
                status_code=400,
                detail=f"manifest must contain 1..{MAX_REPOSITORIES_PER_ORG} repositories",
            )
        from app.models.engineering_graph import MultiRepoAcquisitionSpec

        specs = [MultiRepoAcquisitionSpec(**s) for s in payload]
        from app.services.acquisition import RepositoryAcquisitionService

        org = _get_org_service()
        result = await org.acquire_and_link_repositories(
            specs, acquisition_service=RepositoryAcquisitionService(),
        )
        return {"success": True, "data": result}
    except HTTPException:
        raise
    except (ValueError, KeyError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
