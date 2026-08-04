"""
Phase 12 API endpoints for Advanced Code Intelligence.

Follows existing API conventions from Phases 1-11.
All graph queries are bounded by depth, limit, and result count.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

from app.code_intelligence import (
    CodeIntelligenceService,
    ImpactAnalysisResult,
    ImpactAnalysisService,
    IndexResult,
)
from app.code_intelligence.graph_retriever import GraphAwareRetriever
from app.code_intelligence.semantic_graph import SemanticRepositoryGraph
from app.models.base import Response

router = APIRouter(
    prefix="/api/v1/code-intelligence-v2",
    tags=["code-intelligence-v2"],
)

# Global service instance (matching Phase 5 pattern)
_service = CodeIntelligenceService()


# ── Index Management ───────────────────────────────────────────


@router.post("/index", response_model=Response)
async def build_index(
    path: str,
) -> Response:
    """Build a full semantic repository index (Phase 12).

    Parses all source files, extracts symbols and relationships,
    and builds the semantic repository graph.
    Persists the graph to PostgreSQL if a store is configured.

    Args:
        path: Local repository path.

    Returns:
        Response with index statistics.
    """
    try:
        result = _service.index_repository(path)

        # Persist graph to PostgreSQL (non-blocking on failure)
        persistence_result = None
        try:
            persistence_result = await _service.persist_graph()
        except Exception:
            pass

        data = {
            "index_id": result.index_id,
            "repository_id": result.repository_id,
            "files_scanned": result.stats.files_scanned,
            "files_parsed": result.stats.files_parsed,
            "files_failed": result.stats.files_failed,
            "symbols_extracted": result.stats.symbols_extracted,
            "edges_created": result.stats.edges_created,
            "duration_seconds": result.stats.duration_seconds,
            "content_fingerprint": result.content_fingerprint,
            "languages": result.stats.languages,
            "warnings": result.stats.warnings[:20],
            "errors": result.stats.errors[:10],
        }

        if persistence_result:
            data["persisted"] = True
            data["persisted_symbols"] = persistence_result.get("symbol_count", 0)
            data["persisted_relationships"] = persistence_result.get("relationship_count", 0)

        return Response(
            success=True,
            data=data,
            message=f"Indexed {result.stats.files_parsed} files, "
                    f"{result.stats.symbols_extracted} symbols, "
                    f"{result.stats.edges_created} edges",
        )
    except ValueError as exc:
        return Response(success=False, error="ValueError", message=str(exc))
    except Exception as exc:
        return Response(success=False, error="IndexError", message=str(exc))


@router.get("/status", response_model=Response)
async def get_index_status() -> Response:
    """Get the current index status."""
    graph = _service.get_current_graph()
    if not graph:
        return Response(
            success=True,
            data={"indexed": False, "message": "No index loaded"},
        )

    stats = graph.stats()
    return Response(
        success=True,
        data={
            "indexed": True,
            "index_id": _service.get_index_id(),
            "repository_path": _service.get_repository_path(),
            "node_count": stats["node_count"],
            "edge_count": stats["edge_count"],
            "file_count": stats["file_count"],
            "kinds": stats["kinds"],
            "relationships": stats["relationships"],
        },
    )


@router.post("/index/reset", response_model=Response)
async def reset_index() -> Response:
    """Reset the current index."""
    _service.reset()
    return Response(success=True, message="Index reset")


# ── Symbol Queries ─────────────────────────────────────────────


@router.get("/symbols", response_model=Response)
async def list_symbols(
    kind: Optional[str] = Query(None, description="Filter by symbol kind"),
    file_path: Optional[str] = Query(None, description="Filter by file path"),
    name: Optional[str] = Query(None, description="Search by name"),
    limit: int = Query(50, ge=1, le=500),
) -> Response:
    """List symbols in the indexed repository.

    All queries are bounded.
    """
    graph = _service.get_current_graph()
    if not graph:
        return Response(success=False, error="NoIndex", message="No index loaded")

    symbols = graph.all_nodes()

    if kind:
        symbols = [s for s in symbols if s.kind == kind]
    if file_path:
        symbols = [s for s in symbols if s.file_path == file_path]
    if name:
        symbols = [s for s in symbols if name.lower() in s.name.lower()]

    symbols = symbols[:limit]

    return Response(
        success=True,
        data={
            "total": len(symbols),
            "limited": len(symbols) == limit,
            "symbols": [
                {
                    "id": s.id,
                    "name": s.name,
                    "qualified_name": s.qualified_name,
                    "kind": s.kind,
                    "file_path": s.file_path,
                    "language": s.language,
                    "start_line": s.start_line,
                    "end_line": s.end_line,
                    "parent_id": s.parent_id,
                    "signature": s.signature,
                    "docstring": s.docstring,
                }
                for s in symbols
            ],
        },
    )


@router.get("/symbol/{symbol_id}", response_model=Response)
async def get_symbol(
    symbol_id: str,
    depth: int = Query(2, ge=0, le=5, description="Graph traversal depth"),
    limit: int = Query(30, ge=1, le=100, description="Max related symbols"),
) -> Response:
    """Get a symbol with its graph context (dependencies + dependents).

    Args:
        symbol_id: The symbol ID (file::qualified_name).
        depth: Graph traversal depth (0 = no expansion).
        limit: Max related symbols.

    Returns:
        Response with symbol details and related symbols.
    """
    graph = _service.get_current_graph()
    if not graph:
        return Response(success=False, error="NoIndex", message="No index loaded")

    node = graph.get_node(symbol_id)
    if not node:
        return Response(success=False, error="NotFound", message=f"Symbol not found: {symbol_id}")

    result: Dict[str, Any] = {
        "symbol": {
            "id": node.id,
            "name": node.name,
            "qualified_name": node.qualified_name,
            "kind": node.kind,
            "file_path": node.file_path,
            "language": node.language,
            "start_line": node.start_line,
            "end_line": node.end_line,
            "parent_id": node.parent_id,
            "signature": node.signature,
            "docstring": node.docstring,
            "metadata": node.metadata,
        },
    }

    if depth > 0:
        # Dependencies (outgoing)
        deps = []
        for edge in graph.get_edges(symbol_id)[:limit]:
            target = graph.get_node(edge.target_id)
            deps.append({
                "symbol_id": edge.target_id,
                "name": target.name if target else edge.target_id,
                "relationship": edge.metadata.relationship.value,
                "confidence": edge.metadata.confidence.value,
                "evidence": edge.metadata.resolution_detail,
            })
        result["dependencies"] = deps

        # Dependents (incoming)
        depts = []
        for edge in graph.get_reverse_edges(symbol_id)[:limit]:
            source = graph.get_node(edge.source_id)
            depts.append({
                "symbol_id": edge.source_id,
                "name": source.name if source else edge.source_id,
                "relationship": edge.metadata.relationship.value,
                "confidence": edge.metadata.confidence.value,
                "evidence": edge.metadata.resolution_detail,
            })
        result["dependents"] = depts

        # Neighborhood traversal
        if depth > 1:
            neighbor_result = graph.traverse_neighborhood(
                node_id=symbol_id, depth=depth, max_nodes=limit
            )
            result["neighborhood"] = {
                "nodes": [{"id": n.id, "name": n.name, "kind": n.kind, "file_path": n.file_path}
                         for n in neighbor_result.nodes[:limit]],
                "truncated": neighbor_result.truncated,
            }

    return Response(success=True, data=result)


# ── Impact Analysis ────────────────────────────────────────────


@router.post("/impact", response_model=Response)
async def analyze_impact(
    symbol_ids: List[str] = Query(description="Symbol IDs to analyze"),
    max_depth: int = Query(3, ge=1, le=5),
    max_nodes: int = Query(100, ge=1, le=500),
) -> Response:
    """Analyze the impact of changing specified symbols.

    Returns affected symbols, tests, and risk assessments.
    All traversals are bounded.
    """
    graph = _service.get_current_graph()
    if not graph:
        return Response(success=False, error="NoIndex", message="No index loaded")

    service = ImpactAnalysisService(graph=graph, max_depth=max_depth, max_nodes=max_nodes)
    result = service.analyze(symbol_ids)

    return Response(
        success=True,
        data={
            "root_symbols": [
                {"id": s.id, "name": s.name, "kind": s.kind, "file_path": s.file_path}
                for s in result.root_symbols
            ],
            "direct_impact": [
                {
                    "symbol_id": item.node.id,
                    "name": item.node.name,
                    "kind": item.node.kind,
                    "file_path": item.node.file_path,
                    "relationship": item.relationship,
                    "distance": item.distance,
                    "confidence": item.confidence.value,
                    "risk": item.risk.value,
                    "evidence": item.evidence[:3],
                }
                for item in result.direct_impact[:50]
            ],
            "indirect_impact": [
                {
                    "symbol_id": item.node.id,
                    "name": item.node.name,
                    "kind": item.node.kind,
                    "file_path": item.node.file_path,
                    "distance": item.distance,
                    "risk": item.risk.value,
                }
                for item in result.indirect_impact[:50]
            ],
            "related_tests": [
                {"id": t.id, "name": t.name, "file_path": t.file_path}
                for t in result.related_tests[:20]
            ],
            "risk_summary": result.risk_summary,
            "affected_files": result.affected_files[:50],
            "truncated": result.truncated,
        },
        message=(
            f"Impact: {len(result.direct_impact)} direct, "
            f"{len(result.indirect_impact)} indirect, "
            f"{len(result.related_tests)} tests"
        ),
    )


# ── Graph-Aware Retrieval ──────────────────────────────────────


@router.post("/retrieve", response_model=Response)
async def graph_retrieve(
    symbol_names: List[str] = Query(description="Symbol names to find"),
    file_paths: Optional[List[str]] = Query(None, description="File paths to include"),
    expand_depth: int = Query(2, ge=0, le=5),
    max_expanded: int = Query(30, ge=1, le=200),
) -> Response:
    """Retrieve graph-aware context for a set of symbols.

    Returns direct matches plus expanded graph context (callers,
    dependencies, tests).

    This is the primary endpoint for agent context integration.
    """
    graph = _service.get_current_graph()
    if not graph:
        return Response(success=False, error="NoIndex", message="No index loaded")

    retriever = GraphAwareRetriever(graph=graph)
    result = retriever.retrieve_for_symbols(
        symbol_ids=[
            sym.id for name in symbol_names
            for sym in graph.find_symbols_by_name(name)
        ],
        expand_depth=expand_depth,
        max_expanded=max_expanded,
    )

    return Response(
        success=True,
        data={
            "direct_matches": [
                {"id": i.node.id, "name": i.node.name, "kind": i.node.kind,
                 "file_path": i.node.file_path, "signature": i.node.signature}
                for i in result.direct_matches
            ],
            "graph_context": [
                {"id": i.node.id, "name": i.node.name, "kind": i.node.kind,
                 "file_path": i.node.file_path, "distance": i.graph_distance,
                 "relationships": i.relationship_types}
                for i in result.graph_context
            ],
            "total_symbols": result.total_symbols,
            "truncated": result.truncated,
        },
        message=(
            f"Retrieved {len(result.direct_matches)} direct + "
            f"{len(result.graph_context)} related symbols"
        ),
    )


# ── Capabilities ───────────────────────────────────────────────


@router.get("/capabilities", response_model=Response)
async def code_intelligence_capabilities() -> Response:
    """List Phase 12 code intelligence capabilities."""
    return Response(
        success=True,
        data={
            "version": "12.0",
            "semantic_graph": True,
            "impact_analysis": True,
            "incremental_indexing": True,
            "graph_aware_retrieval": True,
            "supported_parsers": ["Python (AST)", "TypeScript", "JavaScript"],
            "relationship_types": [
                "contains", "imports", "exports", "defines",
                "calls", "references", "inherits", "implements",
                "depends_on", "tests", "composes",
            ],
            "confidence_levels": ["exact", "high", "medium", "unresolved"],
            "max_graph_depth": 5,
            "max_impact_nodes": 500,
            "bounded_traversal": True,
        },
    )
