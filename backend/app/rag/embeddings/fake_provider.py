"""
Fake embedding provider — deterministic embeddings for testing.

Returns reproducible pseudo-embeddings based on text content so tests
can verify semantic retrieval behavior without any external API.
"""

from __future__ import annotations

import hashlib
import math
import time
from typing import Dict, List, Optional

from app.rag.embeddings.base import EmbeddingResult, EmbeddingService


class FakeEmbeddingProvider(EmbeddingService):
    """Deterministic fake embedding provider for testing.

    Generates pseudo-random but deterministic embeddings based on
    text content hash. Embeddings are reproducible across calls
    with the same text.
    """

    def __init__(self, dimension: int = 64, model: str = "fake-embedding-model"):
        super().__init__(model=model)
        self._dimension = dimension
        self._call_count = 0

    def embed_documents(self, texts: List[str]) -> EmbeddingResult:
        """Embed documents deterministically."""
        start = time.time()
        embeddings: List[List[float]] = []
        cache_hits = 0

        for text in texts:
            cached = self._check_cache(text)
            if cached is not None:
                embeddings.append(cached)
                cache_hits += 1
            else:
                emb = self._generate_embedding(text)
                self._set_cache(text, emb)
                embeddings.append(emb)

        self._call_count += 1
        duration = round(time.time() - start, 4)

        return EmbeddingResult(
            embeddings=embeddings,
            model=self._model,
            dimension=self._dimension,
            cache_hits=cache_hits,
            duration_seconds=duration,
        )

    def embed_query(self, text: str) -> List[float]:
        """Embed a single query."""
        cached = self._check_cache(text)
        if cached is not None:
            return cached
        embedding = self._generate_embedding(text)
        self._set_cache(text, embedding)
        return embedding

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def call_count(self) -> int:
        return self._call_count

    def _generate_embedding(self, text: str) -> List[float]:
        """Generate a deterministic embedding from text content.

        Uses SHA-256 hash to seed a deterministic pseudo-random
        embedding vector. Similar texts get similar-ish vectors
        due to hash properties.
        """
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        seed = int(text_hash[:16], 16)

        # Use modular arithmetic to create deterministic values
        embedding = []
        for i in range(self._dimension):
            value = ((seed // (i + 1)) % 10000) / 10000.0
            value = (value - 0.5) * 2  # Scale to [-1, 1]
            embedding.append(value)

        # Normalize to unit length
        norm = math.sqrt(sum(v * v for v in embedding))
        if norm > 0:
            embedding = [v / norm for v in embedding]

        return embedding
