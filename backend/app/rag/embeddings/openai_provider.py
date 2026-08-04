"""
OpenAI embedding provider — uses OpenAI's embedding API.

Relies on the existing LLM provider configuration for API keys.
Supports text-embedding-3-small, text-embedding-3-large, and
any compatible embeddings endpoint.
"""

from __future__ import annotations

import time
from typing import List, Optional

from openai import OpenAI

from app.config import settings
from app.rag.embeddings.base import EmbeddingResult, EmbeddingService


class OpenAIEmbeddingProvider(EmbeddingService):
    """Embedding provider using OpenAI-compatible API.

    Uses the existing OPENAI_API_KEY and OPENAI_BASE_URL configuration.
    """

    def __init__(
        self,
        model: str = "text-embedding-3-small",
        dimension: int = 256,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        super().__init__(model=model)
        self._dimension = dimension
        self._client = OpenAI(
            api_key=api_key or settings.OPENAI_API_KEY,
            base_url=base_url or settings.OPENAI_BASE_URL,
        )

    def embed_documents(self, texts: List[str]) -> EmbeddingResult:
        """Embed a list of documents using OpenAI's API.

        Args:
            texts: List of text strings to embed.

        Returns:
            EmbeddingResult with embeddings array.
        """
        start = time.time()

        # Check cache first
        embeddings: List[List[float]] = []
        uncached_texts: List[str] = []
        uncached_indices: List[int] = []
        cache_hits = 0

        for i, text in enumerate(texts):
            cached = self._check_cache(text)
            if cached is not None:
                embeddings.append(cached)
                cache_hits += 1
            else:
                embeddings.append([])  # Placeholder
                uncached_texts.append(text)
                uncached_indices.append(i)

        # Call API for uncached texts
        if uncached_texts:
            response = self._client.embeddings.create(
                model=self._model,
                input=uncached_texts,
                dimensions=self._dimension,
            )
            # Sort by index to maintain order
            sorted_data = sorted(response.data, key=lambda x: x.index)
            for idx, data in zip(uncached_indices, sorted_data):
                embedding = data.embedding
                embeddings[idx] = embedding
                self._set_cache(uncached_texts[uncached_indices.index(idx)], embedding)

        duration = round(time.time() - start, 4)

        return EmbeddingResult(
            embeddings=embeddings,
            model=self._model,
            dimension=self._dimension,
            cache_hits=cache_hits,
            duration_seconds=duration,
        )

    def embed_query(self, text: str) -> List[float]:
        """Embed a single query string.

        Args:
            text: Query text to embed.

        Returns:
            Embedding vector as list of floats.
        """
        cached = self._check_cache(text)
        if cached is not None:
            return cached

        response = self._client.embeddings.create(
            model=self._model,
            input=[text],
            dimensions=self._dimension,
        )
        embedding = response.data[0].embedding
        self._set_cache(text, embedding)
        return embedding

    @property
    def dimension(self) -> int:
        return self._dimension
