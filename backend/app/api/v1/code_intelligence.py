"""
Phase 5 API endpoints for code intelligence (indexing + retrieval).

Follows the existing API conventions from Phases 2-4.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException

from app.models.base import Response
from app.models.rag import (
    PlanAwareRetrievalInput,
    PlanAwareRetrievalResult,
    RetrievalFilter,
    RetrievalQuery,
    RepositoryCodeIndex,
    RetrievedContext,
)
from app.services.index_builder import RepositoryIndexBuilder
from app.rag.retrieval.hybrid_retriever import HybridRetriever
from app.rag.retrieval.plan_context_retriever import PlanContextRetriever

router = APIRouter(
    prefix="/api/v1/code-intelligence",
    tags=["code-intelligence"],
)


@router.post("/index/build", response_model=Response)
async def build_index(
    path: str,
    ref: Optional[str] = None,
    enable_embeddings: bool = False,
) -> Response:
    """Build a repository code index.

    Args:
        path: Local repository path.
        ref: Optional branch/ref name.
        enable_embeddings: Whether to generate embeddings.

    Returns:
        Response with index statistics.
    """
    try:
        builder = RepositoryIndexBuilder(enable_embeddings=enable_embeddings)
        index = builder.build(path, ref=ref)

        return Response(
            success=True,
            data={
                "snapshot_id": index.snapshot.snapshot_id,
                "files_indexed": index.statistics.files_indexed,
                "files_skipped": index.statistics.files_skipped,
                "symbols_extracted": index.statistics.symbols_extracted,
                "chunks_created": index.statistics.chunks_created,
                "embedding_count": index.statistics.embedding_count,
                "duration_seconds": index.statistics.duration_seconds,
                "warnings": index.statistics.warnings,
                "errors": index.statistics.errors,
            },
            message="Index build completed",
        )
    except Exception as exc:
        return Response(
            success=False,
            error="RepositoryIndexError",
            message=str(exc),
        )


@router.post("/retrieval/search", response_model=Response)
async def search(
    path: str,
    query: str,
    top_k: int = 10,
    languages: Optional[str] = None,  # Comma-separated
    path_prefix: Optional[str] = None,
    include_tests: bool = True,
    weight_lexical: Optional[float] = None,
    weight_semantic: Optional[float] = None,
    weight_symbol: Optional[float] = None,
    weight_structural: Optional[float] = None,
    enable_embeddings: bool = False,
) -> Response:
    """Search the repository code index.

    Args:
        path: Local repository path.
        query: Search query text.
        top_k: Number of results (1-50).
        languages: Optional comma-separated language filter.
        path_prefix: Optional path prefix filter.
        include_tests: Include test files in results.
        weight_lexical: Lexical search weight (0-1).
        weight_semantic: Semantic search weight (0-1).
        weight_symbol: Symbol search weight (0-1).
        weight_structural: Structural search weight (0-1).
        enable_embeddings: Whether to use semantic search.

    Returns:
        Response with retrieved context items.
    """
    try:
        top_k = max(1, min(50, top_k))
        builder = RepositoryIndexBuilder(enable_embeddings=enable_embeddings)
        code_index, lex_idx, sym_idx, vec_idx = builder.build_with_indexes(path)

        retriever = HybridRetriever(
            lexical_index=lex_idx,
            symbol_index=sym_idx,
            vector_index=vec_idx,
            embedding_service=builder.embedding_service if enable_embeddings else None,
            weight_lexical=weight_lexical or 0.30,
            weight_semantic=weight_semantic or 0.25,
            weight_symbol=weight_symbol or 0.25,
            weight_structural=weight_structural or 0.20,
        )
        retriever.set_indexes(lex_idx, sym_idx, vec_idx, code_index.chunks)

        filters = None
        if languages or path_prefix:
            filters = RetrievalFilter(
                languages=languages.split(",") if languages else [],
                path_prefix=path_prefix,
                include_tests=include_tests,
            )

        retrieval_query = RetrievalQuery(
            text=query,
            snapshot_id=code_index.snapshot.snapshot_id,
            top_k=top_k,
            filters=filters,
            weight_lexical=weight_lexical,
            weight_semantic=weight_semantic,
            weight_symbol=weight_symbol,
            weight_structural=weight_structural,
        )

        result = retriever.retrieve(retrieval_query)

        items_data = []
        for item in result.items:
            items_data.append({
                "chunk_id": item.chunk.chunk_id,
                "file_path": item.chunk.file_path,
                "symbol_name": item.chunk.symbol_name,
                "symbol_kind": item.chunk.symbol_kind.value if item.chunk.symbol_kind else None,
                "start_line": item.chunk.start_line,
                "end_line": item.chunk.end_line,
                "score": item.score,
                "lexical_score": item.lexical_score,
                "semantic_score": item.semantic_score,
                "symbol_score": item.symbol_score,
                "structural_score": item.structural_score,
                "reasons": item.reasons,
                "content_preview": item.chunk.content[:500] if item.chunk.content else "",
            })

        return Response(
            success=True,
            data={
                "snapshot_id": result.snapshot_id,
                "query": query,
                "total_items": len(items_data),
                "total_candidates": result.total_candidates,
                "duration_seconds": result.duration_seconds,
                "items": items_data,
                "warnings": result.warnings,
            },
            message=f"Search completed: {len(items_data)} results",
        )

    except Exception as exc:
        return Response(
            success=False,
            error="RetrievalError",
            message=str(exc),
        )


@router.post("/retrieval/plan-context", response_model=Response)
async def plan_context(
    inp: PlanAwareRetrievalInput,
) -> Response:
    """Retrieve context relevant to an implementation plan.

    Args:
        inp: PlanAwareRetrievalInput with plan and repository path.

    Returns:
        Response with plan-aware retrieval results.
    """
    try:
        plan_retriever = PlanContextRetriever()
        result = await plan_retriever.retrieve_for_plan(
            plan=inp.plan,
            requirements=inp.requirements,
            repository_path=inp.repository_path,
            top_k_per_step=inp.top_k_per_step,
        )

        steps_data = []
        for step in result.steps:
            items_data = []
            for item in step.context.items:
                items_data.append({
                    "chunk_id": item.chunk.chunk_id,
                    "file_path": item.chunk.file_path,
                    "symbol_name": item.chunk.symbol_name,
                    "start_line": item.chunk.start_line,
                    "end_line": item.chunk.end_line,
                    "score": item.score,
                    "score_breakdown": {
                        "lexical": item.lexical_score,
                        "semantic": item.semantic_score,
                        "symbol": item.symbol_score,
                        "structural": item.structural_score,
                    },
                    "reasons": item.reasons,
                })

            steps_data.append({
                "step_id": step.step_id,
                "step_title": step.step_title,
                "query": step.query,
                "items": items_data,
                "total_candidates": step.context.total_candidates,
            })

        return Response(
            success=True,
            data={
                "steps": steps_data,
                "total_chunks": result.total_chunks,
                "warnings": result.warnings,
            },
            message=f"Plan context retrieved: {result.total_chunks} chunks across {len(result.steps)} steps",
        )

    except Exception as exc:
        return Response(
            success=False,
            error="RetrievalError",
            message=str(exc),
        )


@router.get("/retrieval/capabilities", response_model=Response)
async def retrieval_capabilities() -> Response:
    """List retrieval capabilities."""
    return Response(
        success=True,
        data={
            "search_methods": ["lexical", "symbol", "semantic", "structural", "hybrid"],
            "filters": ["languages", "path_prefix", "include_tests", "symbol_kinds"],
            "max_top_k": 100,
            "plan_aware": True,
            "embeddings": False,  # Set to True when embedding provider configured
            "supported_parsers": ["Python (AST)", "TypeScript (fallback)", "JavaScript (fallback)", "Other (fallback)"],
        },
    )
