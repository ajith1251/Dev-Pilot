"""
Phase 15 API endpoints for Repository Memory browsing and invalidation.

Follows existing API conventions from Phases 1-14. All queries are bounded.
Enables the frontend /devpilot-context diagnostic view to:
  - discover repositories that have memories
  - browse memories with filters (status, type, symbols)
  - view memory statistics
  - invalidate memories by symbol names
  - invalidate or delete individual memories

Security: read-only operations never mutate repositories; invalidation only
downgrades memory status (never deletes source code).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query

from app.models.base import Response
from app.models.memory import MemoryQuery, MemoryStatus, MemoryType

router = APIRouter(
    prefix="/api/v1/memory",
    tags=["memory"],
)

# Global RepositoryMemoryService (created on demand; gracefully degrades)
_service: Optional[Any] = None


def _get_service() -> Any:
    """Get or create the global RepositoryMemoryService instance."""
    global _service
    if _service is None:
        from app.services.repository_memory_service import RepositoryMemoryService

        _service = RepositoryMemoryService()
    return _service


def _memory_to_api(memory: Any) -> Dict[str, Any]:
    """Convert a RepositoryMemory to a bounded API dict (no raw secrets)."""
    return {
        "memory_id": memory.memory_id,
        "repository_id": memory.repository_id,
        "memory_type": memory.memory_type.value,
        "status": memory.status.value,
        "content": memory.content[:500],
        "confidence": round(float(memory.confidence), 2),
        "symbol_names": (memory.symbol_names or [])[:10],
        "file_paths": (memory.file_paths or [])[:10],
        "source_run_id": memory.source_run_id,
        "version": memory.version,
        "created_at": memory.created_at,
        "updated_at": memory.updated_at,
        "last_used_at": memory.last_used_at,
        "evidence": [
            {
                "source_type": e.source_type,
                "source_id": e.source_id,
                "description": e.description[:200],
            }
            for e in (memory.evidence or [])[:5]
        ],
    }


@router.get("/repositories", response_model=Response)
async def list_repositories(
    limit: int = Query(50, ge=1, le=500),
) -> Response:
    """List repository IDs that have stored memories."""
    try:
        svc = _get_service()
        repo_ids = await svc.list_repository_ids(limit=limit)
        return Response(
            success=True,
            data={"repositories": repo_ids, "count": len(repo_ids)},
            message=f"{len(repo_ids)} repositories with memories",
        )
    except Exception as exc:
        return Response(
            success=False,
            error="MemoryError",
            message=f"Failed to list repositories: {exc}",
        )


@router.get("/{repository_id}", response_model=Response)
async def list_memories(
    repository_id: str,
    status: Optional[str] = Query(None, description="Filter: verified|provisional|stale|invalid"),
    memory_type: Optional[str] = Query(None, description="Filter by memory type"),
    symbol: Optional[str] = Query(None, description="Filter by symbol name"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    include_stale: bool = Query(True, description="Include stale/invalid memories"),
) -> Response:
    """Browse memories for a repository with filters."""
    try:
        svc = _get_service()

        status_filter = None
        if status:
            statuses = [s.strip() for s in status.split(",") if s.strip()]
            status_filter = [MemoryStatus(s) for s in statuses]

        type_filter = None
        if memory_type:
            types = [t.strip() for t in memory_type.split(",") if t.strip()]
            type_filter = [MemoryType(t) for t in types]

        query = MemoryQuery(
            repository_id=repository_id,
            status_filter=status_filter,
            memory_types=type_filter,
            symbol_names=[symbol] if symbol else None,
            limit=limit,
            offset=offset,
            include_stale=include_stale,
        )
        memories = await svc.query_memories(query=query)

        return Response(
            success=True,
            data={
                "repository_id": repository_id,
                "count": len(memories),
                "memories": [_memory_to_api(m) for m in memories],
            },
            message=f"{len(memories)} memories for {repository_id}",
        )
    except Exception as exc:
        return Response(
            success=False,
            error="MemoryError",
            message=f"Failed to list memories: {exc}",
        )


@router.get("/{repository_id}/stats", response_model=Response)
async def memory_stats(repository_id: str) -> Response:
    """Get memory statistics for a repository."""
    try:
        svc = _get_service()
        stats = await svc.get_memory_stats(repository_id)
        return Response(
            success=True,
            data={"repository_id": repository_id, **stats},
            message=f"Memory stats for {repository_id}",
        )
    except Exception as exc:
        return Response(
            success=False,
            error="MemoryError",
            message=f"Failed to fetch memory stats: {exc}",
        )


@router.post("/{repository_id}/invalidate-symbols", response_model=Response)
async def invalidate_symbols(
    repository_id: str,
    symbols: str = Query(..., description="Comma-separated symbol names that changed"),
    reason: str = Query("Referenced symbol changed", description="Invalidation reason"),
) -> Response:
    """Mark memories referencing the given symbols as STALE.

    Called when the underlying symbols changed (e.g., after a patch
    modified them) so downstream agents don't consume outdated memory.
    """
    try:
        svc = _get_service()
        symbol_list = [s.strip() for s in symbols.split(",") if s.strip()]
        if not symbol_list:
            return Response(success=False, error="ValueError", message="No symbols provided")

        invalidated = await svc.invalidate_memories_for_symbols(
            repository_id=repository_id,
            symbol_names=symbol_list,
            reason=reason,
        )
        return Response(
            success=True,
            data={"invalidated": invalidated, "symbols": symbol_list},
            message=f"Invalidated {invalidated} memories for {len(symbol_list)} symbol(s)",
        )
    except Exception as exc:
        return Response(
            success=False,
            error="MemoryError",
            message=f"Failed to invalidate memories: {exc}",
        )


@router.post("/{memory_id}/invalidate", response_model=Response)
async def invalidate_memory(
    memory_id: str,
    reason: str = Query("Manually invalidated", description="Invalidation reason"),
) -> Response:
    """Invalidate a single memory by ID (downgrades status to STALE)."""
    try:
        svc = _get_service()
        memory = await svc.get_memory(memory_id)
        if memory is None:
            return Response(success=False, error="NotFound", message=f"Memory not found: {memory_id}")

        updated = await svc.update_memory(
            memory_id=memory_id,
            updates={"status": MemoryStatus.STALE.value},
        )
        return Response(
            success=True,
            data={"memory_id": memory_id, "status": updated.status.value},
            message=f"Invalidated memory {memory_id} ({reason})",
        )
    except Exception as exc:
        return Response(
            success=False,
            error="MemoryError",
            message=f"Failed to invalidate memory: {exc}",
        )


@router.delete("/{memory_id}", response_model=Response)
async def delete_memory(memory_id: str) -> Response:
    """Delete a single memory by ID."""
    try:
        svc = _get_service()
        deleted = await svc.delete_memory(memory_id)
        if not deleted:
            return Response(success=False, error="NotFound", message=f"Memory not found: {memory_id}")
        return Response(
            success=True,
            data={"memory_id": memory_id},
            message=f"Deleted memory {memory_id}",
        )
    except Exception as exc:
        return Response(
            success=False,
            error="MemoryError",
            message=f"Failed to delete memory: {exc}",
        )
