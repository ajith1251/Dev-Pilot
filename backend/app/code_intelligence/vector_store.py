"""
Phase 15 (Phase 12d) — Persisted Vector Index with graceful degradation.

The Phase 5 VectorIndex is in-memory only; Phase 12d roadmap called for
pgvector-backed persistence. pgvector requires the PostgreSQL `vector`
extension, which may not be installed on a given deployment. This store:

1. Detects whether pgvector is available (extension + table).
2. When available: persists embeddings to a `code_embeddings` table and
   supports cosine-similarity search via pgvector operators.
3. When unavailable: degrades gracefully to an in-memory dict index
   (Phase 5 behavior), so the application never crashes.

Embeddings are stored as vector(256) matching settings.EMBEDDING_DIMENSION.
"""

from __future__ import annotations

import asyncio
import math
from typing import Any, Dict, List, Optional, Sequence

from app.config import settings
from app.core.logging import logger


class VectorStore:
    """Optional pgvector-backed embedding store with in-memory fallback."""

    TABLE = "code_embeddings"

    def __init__(self, dimension: Optional[int] = None) -> None:
        self.dimension = dimension or settings.EMBEDDING_DIMENSION
        self._available: Optional[bool] = None  # lazily probed
        # In-memory fallback: (repository_id, index_id, symbol_id) -> embedding
        self._memory: Dict[str, List[float]] = {}
        self._meta: Dict[str, Dict[str, Any]] = {}

    # ── Availability ────────────────────────────────────────────

    def is_available(self) -> bool:
        """Check whether pgvector storage is usable (cached probe)."""
        if self._available is not None:
            return self._available
        try:
            import asyncpg  # noqa: F401

            if not settings.DATABASE_URL:
                self._available = False
                return False
            # Light check: does the vector extension exist in the DB?
            url = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
            import asyncio

            loop = asyncio.new_event_loop()
            try:
                conn = loop.run_until_complete(asyncpg.connect(url))
                try:
                    row = loop.run_until_complete(
                        conn.fetchrow(
                            "SELECT 1 FROM pg_extension WHERE extname='vector'"
                        )
                    )
                    self._available = row is not None
                finally:
                    loop.run_until_complete(conn.close())
            finally:
                loop.close()
            return self._available
        except Exception as exc:
            logger.debug("pgvector probe failed (in-memory fallback): %s", exc)
            self._available = False
            return False

    def is_available_async(self) -> bool:
        """Sync probe that does not block (used by tests); mirrors is_available."""
        return self.is_available()

    # ── Persistence ─────────────────────────────────────────────

    async def save_embedding(
        self,
        repository_id: str,
        index_id: str,
        symbol_id: str,
        embedding: Sequence[float],
    ) -> bool:
        """Persist a single embedding. Returns True when stored."""
        if not embedding:
            return False
        key = f"{repository_id}:{index_id}:{symbol_id}"

        if self.is_available():
            try:
                return await self._save_pg(
                    repository_id=repository_id,
                    index_id=index_id,
                    symbol_id=symbol_id,
                    embedding=list(embedding),
                )
            except Exception as exc:
                logger.debug("pgvector save failed, falling back: %s", exc)

        # In-memory fallback
        self._memory[key] = list(embedding)
        self._meta[key] = {
            "repository_id": repository_id,
            "index_id": index_id,
            "symbol_id": symbol_id,
        }
        return True

    async def save_embeddings(
        self,
        repository_id: str,
        index_id: str,
        items: List[Dict[str, Any]],
    ) -> int:
        """Persist multiple embeddings. Returns count stored."""
        saved = 0
        for item in items:
            symbol_id = item.get("symbol_id")
            embedding = item.get("embedding")
            if symbol_id and embedding:
                if await self.save_embedding(
                    repository_id=repository_id,
                    index_id=index_id,
                    symbol_id=symbol_id,
                    embedding=embedding,
                ):
                    saved += 1
        return saved

    async def search(
        self,
        query_embedding: Sequence[float],
        repository_id: Optional[str] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Cosine-similarity search. Returns [{symbol_id, score, ...}]."""
        if self.is_available():
            try:
                return await self._search_pg(
                    query_embedding=list(query_embedding),
                    repository_id=repository_id,
                    limit=limit,
                )
            except Exception as exc:
                logger.debug("pgvector search failed, falling back: %s", exc)

        return self._search_memory(query_embedding, repository_id, limit)

    async def delete_index(self, repository_id: str, index_id: str) -> int:
        """Delete all embeddings for an index. Returns count deleted."""
        prefix = f"{repository_id}:{index_id}:"
        keys = [k for k in self._memory if k.startswith(prefix)]
        for k in keys:
            del self._memory[k]
            self._meta.pop(k, None)
        count = len(keys)

        if self.is_available():
            try:
                await self._delete_pg(repository_id, index_id)
            except Exception as exc:
                logger.debug("pgvector delete failed (ignored): %s", exc)
        return count

    # ── pgvector implementation ─────────────────────────────────

    async def _save_pg(
        self,
        repository_id: str,
        index_id: str,
        symbol_id: str,
        embedding: List[float],
    ) -> bool:
        import asyncpg

        url = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
        conn = await asyncpg.connect(url)
        try:
            vec = f"[{','.join(str(round(float(v), 6)) for v in embedding)}]"
            await conn.execute(
                f"""
                INSERT INTO {self.TABLE} (repository_id, index_id, symbol_id, embedding)
                VALUES ($1, $2, $3, $4::vector)
                ON CONFLICT (repository_id, index_id, symbol_id)
                DO UPDATE SET embedding = EXCLUDED.embedding
                """,
                repository_id,
                index_id,
                symbol_id,
                vec,
            )
            return True
        finally:
            await conn.close()

    async def _search_pg(
        self,
        query_embedding: List[float],
        repository_id: Optional[str],
        limit: int,
    ) -> List[Dict[str, Any]]:
        import asyncpg

        url = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
        conn = await asyncpg.connect(url)
        try:
            vec = f"[{','.join(str(round(float(v), 6)) for v in query_embedding)}]"
            if repository_id:
                rows = await conn.fetch(
                    f"""
                    SELECT symbol_id, 1 - (embedding <=> $1::vector) AS score
                    FROM {self.TABLE}
                    WHERE repository_id = $2
                    ORDER BY embedding <=> $1::vector
                    LIMIT $3
                    """,
                    vec,
                    repository_id,
                    limit,
                )
            else:
                rows = await conn.fetch(
                    f"""
                    SELECT symbol_id, 1 - (embedding <=> $1::vector) AS score
                    FROM {self.TABLE}
                    ORDER BY embedding <=> $1::vector
                    LIMIT $2
                    """,
                    vec,
                    limit,
                )
            return [
                {"symbol_id": r["symbol_id"], "score": round(float(r["score"]), 4)}
                for r in rows
            ]
        finally:
            await conn.close()

    async def _delete_pg(self, repository_id: str, index_id: str) -> None:
        import asyncpg

        url = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
        conn = await asyncpg.connect(url)
        try:
            await conn.execute(
                f"DELETE FROM {self.TABLE} WHERE repository_id=$1 AND index_id=$2",
                repository_id,
                index_id,
            )
        finally:
            await conn.close()

    # ── In-memory fallback implementation ───────────────────────

    def _search_memory(
        self,
        query_embedding: Sequence[float],
        repository_id: Optional[str],
        limit: int,
    ) -> List[Dict[str, Any]]:
        q = list(query_embedding)
        scored: List[Dict[str, Any]] = []
        for key, emb in self._memory.items():
            meta = self._meta.get(key, {})
            if repository_id and meta.get("repository_id") != repository_id:
                continue
            sim = _cosine_similarity(q, emb)
            scored.append(
                {
                    "symbol_id": meta.get("symbol_id", key),
                    "score": round(sim, 4),
                }
            )
        scored.sort(key=lambda x: -x["score"])
        return scored[:limit]


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """Cosine similarity with a safe guard against zero vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
