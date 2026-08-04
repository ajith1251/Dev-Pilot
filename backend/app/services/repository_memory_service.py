"""
Phase 13-C — Repository Memory Service.

Manages durable engineering knowledge memory backed by PostgreSQL.
Supports CRUD, confidence-ranked retrieval, symbol-based invalidation,
and evidence-backed memory creation.

Memory lifecycle:
    Evidence → Candidate → Validation → Persist → Retrieval → Invalidation
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.core.logging import logger
from app.db.models import RepositoryMemoryModel
from app.db.session import create_session_factory
from app.models.memory import (
    MemoryEvidence,
    MemoryQuery,
    MemoryStatus,
    MemoryType,
    RepositoryMemory,
)


# ── Memory ID generation ────────────────────────────────────────


def _generate_memory_id(repository_id: str, content: str) -> str:
    """Generate a deterministic memory ID from content."""
    raw = f"{repository_id}::{content[:200]}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(iso_str: Optional[str]) -> Optional[datetime]:
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _format_dt(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.isoformat()


def _memory_to_dict(m: RepositoryMemoryModel) -> Dict[str, Any]:
    """Convert ORM model to dict for RepositoryMemory model creation."""
    return {
        "memory_id": m.memory_id,
        "repository_id": m.repository_id,
        "memory_type": m.memory_type,
        "status": m.status,
        "content": m.content,
        "confidence": m.confidence,
        "symbol_names": m.symbol_names or [],
        "file_paths": m.file_paths or [],
        "evidence": m.evidence or [],
        "source_run_id": m.source_run_id,
        "tags": m.tags or [],
        "version": m.version,
        "related_commit": m.related_commit,
        "created_at": _format_dt(m.created_at) or _utcnow().isoformat(),
        "updated_at": _format_dt(m.updated_at) or _utcnow().isoformat(),
        "last_used_at": _format_dt(m.last_used_at) if m.last_used_at else None,
    }


# ── RepositoryMemoryService ─────────────────────────────────────


class RepositoryMemoryService:
    """PostgreSQL-backed repository memory storage.

    Manages durable engineering knowledge derived from verified
    DevPilot activity. Memory is evidence-backed and confidence-ranked.

    Memory must NOT be created from raw LLM output or untrusted
    repository source code.
    """

    def __init__(self) -> None:
        self._session_factory: Optional[async_sessionmaker[AsyncSession]] = None

    def _get_session_factory(self) -> async_sessionmaker[AsyncSession]:
        if self._session_factory is None:
            self._session_factory = create_session_factory()
        return self._session_factory

    async def _with_session(self, callback):
        factory = self._get_session_factory()
        async with factory() as session:
            return await callback(session)

    # ── CRUD Operations ─────────────────────────────────────────

    async def create_memory(
        self,
        memory: RepositoryMemory,
    ) -> RepositoryMemory:
        """Persist a new repository memory.

        Args:
            memory: The memory to persist. Must have repository_id and content.

        Returns:
            The persisted memory with generated memory_id and timestamps.
        """
        if not memory.repository_id:
            raise ValueError("repository_id is required")
        if not memory.content:
            raise ValueError("content is required")

        memory_id = memory.memory_id or _generate_memory_id(
            memory.repository_id, memory.content
        )

        async def _impl(session: AsyncSession):
            now = _utcnow()

            model = RepositoryMemoryModel(
                memory_id=memory_id,
                repository_id=memory.repository_id,
                memory_type=memory.memory_type.value,
                status=memory.status.value,
                content=memory.content,
                confidence=memory.confidence,
                symbol_names=memory.symbol_names or None,
                file_paths=memory.file_paths or None,
                evidence=[e.model_dump() for e in memory.evidence] if memory.evidence else None,
                source_run_id=memory.source_run_id,
                tags=memory.tags or None,
                version=1,
                related_commit=memory.related_commit,
                created_at=now,
                updated_at=now,
            )
            session.add(model)
            await session.commit()
            await session.refresh(model)

            return RepositoryMemory(
                memory_id=model.memory_id,
                repository_id=model.repository_id,
                memory_type=MemoryType(model.memory_type),
                status=MemoryStatus(model.status),
                content=model.content,
                confidence=model.confidence,
                symbol_names=model.symbol_names or [],
                file_paths=model.file_paths or [],
                evidence=[
                    MemoryEvidence(**e) for e in model.evidence
                ] if model.evidence else [],
                source_run_id=model.source_run_id,
                tags=model.tags or [],
                version=model.version,
                related_commit=model.related_commit,
                created_at=_format_dt(model.created_at) or now.isoformat(),
                updated_at=_format_dt(model.updated_at) or now.isoformat(),
            )

        return await self._with_session(_impl)

    async def get_memory(self, memory_id: str) -> Optional[RepositoryMemory]:
        """Retrieve a memory by ID.

        Updates last_used_at on access (consistent with query_memories).
        """
        async def _impl(session: AsyncSession):
            stmt = select(RepositoryMemoryModel).where(
                RepositoryMemoryModel.memory_id == memory_id
            )
            result = await session.execute(stmt)
            model = result.scalar_one_or_none()
            if model is None:
                return None
            model.last_used_at = _utcnow()
            await session.commit()
            await session.refresh(model)
            return RepositoryMemory(**_memory_to_dict(model))

        return await self._with_session(_impl)

    async def update_memory(
        self,
        memory_id: str,
        updates: Dict[str, Any],
    ) -> Optional[RepositoryMemory]:
        """Update a memory's fields.

        Increments version on each update. Updates timestamps.
        Valid fields: status, confidence, content, symbol_names,
        file_paths, evidence, tags, last_used_at, related_commit.

        Args:
            memory_id: ID of the memory to update.
            updates: Dict of field names to new values.

        Returns:
            Updated RepositoryMemory or None if not found.
        """
        allowed = {
            "status", "confidence", "content", "symbol_names",
            "file_paths", "evidence", "tags", "last_used_at",
            "related_commit",
        }

        async def _impl(session: AsyncSession):
            stmt = select(RepositoryMemoryModel).where(
                RepositoryMemoryModel.memory_id == memory_id
            )
            result = await session.execute(stmt)
            model = result.scalar_one_or_none()
            if model is None:
                return None

            now = _utcnow()
            for key, value in updates.items():
                if key in allowed:
                    setattr(model, key, value)

            model.version = (model.version or 0) + 1
            model.updated_at = now
            await session.commit()
            await session.refresh(model)

            return RepositoryMemory(**_memory_to_dict(model))

        return await self._with_session(_impl)

    async def delete_memory(self, memory_id: str) -> bool:
        """Delete a memory by ID.

        Returns:
            True if deleted, False if not found.
        """
        async def _impl(session: AsyncSession):
            stmt = select(RepositoryMemoryModel).where(
                RepositoryMemoryModel.memory_id == memory_id
            )
            result = await session.execute(stmt)
            model = result.scalar_one_or_none()
            if model is None:
                return False
            await session.delete(model)
            await session.commit()
            return True

        return await self._with_session(_impl)

    # ── Retrieval ───────────────────────────────────────────────

    async def query_memories(
        self,
        query: MemoryQuery,
    ) -> List[RepositoryMemory]:
        """Query memories with filters and ranking.

        Results are ordered by:
        1. Status priority (VERIFIED first, then PROVISIONAL)
        2. Confidence descending
        3. Updated_at descending (recent first)

        Args:
            query: MemoryQuery with filters.

        Returns:
            Ranked list of RepositoryMemory.
        """
        async def _impl(session: AsyncSession):
            stmt = select(RepositoryMemoryModel).where(
                RepositoryMemoryModel.repository_id == query.repository_id
            )

            # Filter by memory types
            if query.memory_types:
                stmt = stmt.where(
                    RepositoryMemoryModel.memory_type.in_(
                        [t.value for t in query.memory_types]
                    )
                )

            # Filter by status
            if query.status_filter:
                stmt = stmt.where(
                    RepositoryMemoryModel.status.in_(
                        [s.value for s in query.status_filter]
                    )
                )
            elif not query.include_stale:
                # Default: exclude stale and invalid
                stmt = stmt.where(
                    RepositoryMemoryModel.status.in_(["verified", "provisional"])
                )

            # Filter by symbol names (JSONB array contains)
            if query.symbol_names:
                conditions = [
                    RepositoryMemoryModel.symbol_names.contains([sym])
                    for sym in query.symbol_names
                ]
                stmt = stmt.where(or_(*conditions))

            # Min confidence
            if query.min_confidence > 0.0:
                stmt = stmt.where(
                    RepositoryMemoryModel.confidence >= query.min_confidence
                )

            # Order: status priority (verified first), confidence desc, recent first
            status_order = func.array_position(
                ["verified", "provisional", "stale", "invalid"],
                RepositoryMemoryModel.status,
            )
            stmt = stmt.order_by(
                status_order.nullslast(),
                RepositoryMemoryModel.confidence.desc(),
                RepositoryMemoryModel.updated_at.desc(),
            )

            stmt = stmt.offset(query.offset).limit(query.limit)
            result = await session.execute(stmt)
            models = result.scalars().all()

            memories = []
            for m in models:
                mem = RepositoryMemory(**_memory_to_dict(m))

                # Update last_used_at
                m.last_used_at = _utcnow()

                memories.append(mem)

            if memories:
                await session.commit()

            return memories

        return await self._with_session(_impl)

    async def get_memories_for_symbols(
        self,
        repository_id: str,
        symbol_names: List[str],
        limit: int = 10,
    ) -> List[RepositoryMemory]:
        """Quick retrieval: get memories relevant to specific symbols.

        Convenience wrapper around query_memories for the most
        common access pattern — symbol-based memory retrieval.

        Args:
            repository_id: Repository to search.
            symbol_names: Symbols to match against.
            limit: Max results.

        Returns:
            Ranked list of relevant memories, VERIFIED first.
        """
        query = MemoryQuery(
            repository_id=repository_id,
            symbol_names=symbol_names,
            limit=limit,
            min_confidence=0.3,
        )
        return await self.query_memories(query)

    # ── Memory Lifecycle ────────────────────────────────────────

    async def invalidate_memories_for_symbols(
        self,
        repository_id: str,
        symbol_names: List[str],
        reason: str = "Referenced symbol changed",
    ) -> int:
        """Mark memories referencing specific symbols as STALE.

        Called during incremental indexing when symbols are
        modified or removed. Memories are not deleted — they are
        downgraded to STALE status.

        Args:
            repository_id: Repository scope.
            symbol_names: Symbols that have changed.
            reason: Reason for invalidation.

        Returns:
            Count of memories marked as STALE.
        """
        async def _impl(session: AsyncSession):
            now = _utcnow()

            stmt = (
                select(RepositoryMemoryModel)
                .where(
                    RepositoryMemoryModel.repository_id == repository_id,
                    RepositoryMemoryModel.status.in_(["verified", "provisional"]),
                    or_(
                        *[
                            RepositoryMemoryModel.symbol_names.contains([sym])
                            for sym in symbol_names
                        ]
                    ),
                )
            )
            result = await session.execute(stmt)
            models = result.scalars().all()

            count = 0
            for m in models:
                m.status = MemoryStatus.STALE.value
                m.updated_at = now
                count += 1

            if count > 0:
                logger.info(
                    "Invalidated %d memories for symbols %s in repo %s",
                    count, symbol_names[:5], repository_id,
                )
                await session.commit()

            return count

        return await self._with_session(_impl)

    async def mark_memory_used(self, memory_id: str) -> bool:
        """Update last_used_at timestamp.

        Called when memory is retrieved and consumed by an agent.

        Returns:
            True if updated, False if not found.
        """
        async def _impl(session: AsyncSession):
            stmt = select(RepositoryMemoryModel).where(
                RepositoryMemoryModel.memory_id == memory_id
            )
            result = await session.execute(stmt)
            model = result.scalar_one_or_none()
            if model is None:
                return False
            model.last_used_at = _utcnow()
            await session.commit()
            return True

        return await self._with_session(_impl)

    async def get_memory_stats(self, repository_id: str) -> Dict[str, Any]:
        """Get memory statistics for a repository.

        Returns counts by type, status, and average confidence.
        """
        async def _impl(session: AsyncSession):
            # Total count
            total_stmt = select(func.count(RepositoryMemoryModel.id)).where(
                RepositoryMemoryModel.repository_id == repository_id
            )
            total = (await session.execute(total_stmt)).scalar() or 0

            # Count by type
            type_stmt = (
                select(
                    RepositoryMemoryModel.memory_type,
                    func.count(RepositoryMemoryModel.id),
                )
                .where(RepositoryMemoryModel.repository_id == repository_id)
                .group_by(RepositoryMemoryModel.memory_type)
            )
            type_result = await session.execute(type_stmt)
            by_type = {row[0]: row[1] for row in type_result.all()}

            # Count by status
            status_stmt = (
                select(
                    RepositoryMemoryModel.status,
                    func.count(RepositoryMemoryModel.id),
                )
                .where(RepositoryMemoryModel.repository_id == repository_id)
                .group_by(RepositoryMemoryModel.status)
            )
            status_result = await session.execute(status_stmt)
            by_status = {row[0]: row[1] for row in status_result.all()}

            # Average confidence
            avg_stmt = select(func.avg(RepositoryMemoryModel.confidence)).where(
                RepositoryMemoryModel.repository_id == repository_id,
                RepositoryMemoryModel.status.in_(["verified", "provisional"]),
            )
            avg_result = await session.execute(avg_stmt)
            avg_confidence = avg_result.scalar() or 0.0

            return {
                "total": total,
                "by_type": by_type,
                "by_status": by_status,
                "avg_confidence": round(float(avg_confidence), 2),
            }

        return await self._with_session(_impl)

    async def list_memories(
        self,
        repository_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> List[RepositoryMemory]:
        """List all memories for a repository, newest first."""
        query = MemoryQuery(
            repository_id=repository_id,
            limit=limit,
            offset=offset,
            include_stale=True,
        )
        return await self.query_memories(query)

    async def count_memories(self, repository_id: str) -> int:
        """Count total memories for a repository."""
        async def _impl(session: AsyncSession):
            stmt = select(func.count(RepositoryMemoryModel.id)).where(
                RepositoryMemoryModel.repository_id == repository_id
            )
            result = await session.execute(stmt)
            return result.scalar() or 0

        return await self._with_session(_impl)

    async def list_repository_ids(self, limit: int = 50) -> List[str]:
        """List distinct repository IDs that have stored memories.

        Used by the frontend memory browser to discover which
        repositories have memories to inspect.
        """
        async def _impl(session: AsyncSession):
            stmt = (
                select(RepositoryMemoryModel.repository_id)
                .distinct()
                .order_by(RepositoryMemoryModel.repository_id)
                .limit(limit)
            )
            result = await session.execute(stmt)
            return [row[0] for row in result.all()]

        return await self._with_session(_impl)
