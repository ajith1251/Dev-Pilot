"""
Vector Index — in-memory vector similarity search for semantic retrieval.

Provides a clean abstraction that can be replaced with a production
vector database in the future. Uses cosine similarity for ranking.
"""

from __future__ import annotations

import math
import time
from typing import Dict, List, Optional, Tuple

from app.models.rag import CodeChunk


class VectorIndex:
    """In-memory vector index with cosine similarity search.

    Thread-safe for reads. Not thread-safe for writes.
    """

    def __init__(self) -> None:
        # chunk_id -> embedding vector
        self._vectors: Dict[str, List[float]] = {}

        # chunk_id -> chunk reference
        self._chunks: Dict[str, CodeChunk] = {}

        # Content hash -> chunk_id (dedup cache)
        self._content_hash_to_id: Dict[str, str] = {}

        self._dimension: int = 0
        self._built: bool = False
        self._chunk_count: int = 0

    def add(
        self,
        chunk_id: str,
        embedding: List[float],
        chunk: CodeChunk,
    ) -> None:
        """Add a single vector to the index.

        Args:
            chunk_id: Unique chunk identifier.
            embedding: Embedding vector.
            chunk: Reference CodeChunk.
        """
        # Deduplicate
        if chunk.content_hash in self._content_hash_to_id:
            return

        if self._dimension == 0:
            self._dimension = len(embedding)
        elif len(embedding) != self._dimension:
            raise ValueError(
                f"Embedding dimension mismatch: expected {self._dimension}, got {len(embedding)}"
            )

        self._vectors[chunk_id] = embedding
        self._chunks[chunk_id] = chunk
        self._content_hash_to_id[chunk.content_hash] = chunk_id
        self._chunk_count += 1

    def add_batch(
        self,
        chunk_ids: List[str],
        embeddings: List[List[float]],
        chunks: List[CodeChunk],
    ) -> None:
        """Add multiple vectors at once.

        Args:
            chunk_ids: List of chunk IDs.
            embeddings: List of embedding vectors.
            chunks: List of CodeChunk references.
        """
        for cid, emb, chunk in zip(chunk_ids, embeddings, chunks):
            self.add(cid, emb, chunk)

        self._built = True

    def search(
        self,
        query_embedding: List[float],
        top_k: int = 10,
    ) -> List[Tuple[str, float]]:
        """Search for most similar vectors.

        Args:
            query_embedding: Query embedding vector.
            top_k: Maximum results to return.

        Returns:
            List of (chunk_id, score) tuples sorted by score descending.
        """
        if not self._built and self._chunk_count == 0:
            return []

        if len(query_embedding) != self._dimension:
            raise ValueError(
                f"Query dimension mismatch: expected {self._dimension}, got {len(query_embedding)}"
            )

        # Normalize query vector
        query_norm = math.sqrt(sum(v * v for v in query_embedding))
        if query_norm > 0:
            query_normalized = [v / query_norm for v in query_embedding]
        else:
            query_normalized = query_embedding

        # Calculate cosine similarity for each vector
        scores: List[Tuple[str, float]] = []

        for chunk_id, vector in self._vectors.items():
            vec_norm = math.sqrt(sum(v * v for v in vector))
            if vec_norm > 0:
                normalized = [v / vec_norm for v in vector]
            else:
                normalized = vector

            similarity = sum(q * v for q, v in zip(query_normalized, normalized))
            scores.append((chunk_id, max(0.0, similarity)))

        # Sort by score descending
        scores.sort(key=lambda x: x[1], reverse=True)

        return scores[:top_k]

    def get_chunk(self, chunk_id: str) -> Optional[CodeChunk]:
        """Get a chunk by ID."""
        return self._chunks.get(chunk_id)

    def get_embedding(self, chunk_id: str) -> Optional[List[float]]:
        """Get embedding vector by chunk ID."""
        return self._vectors.get(chunk_id)

    def clear(self) -> None:
        """Clear the index."""
        self._vectors.clear()
        self._chunks.clear()
        self._content_hash_to_id.clear()
        self._dimension = 0
        self._built = False
        self._chunk_count = 0

    @property
    def size(self) -> int:
        return self._chunk_count

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def built(self) -> bool:
        return self._built

    def stats(self) -> dict:
        return {
            "total_vectors": self._chunk_count,
            "dimension": self._dimension,
            "built": self._built,
        }
