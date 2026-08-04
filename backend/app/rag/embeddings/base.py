"""
Embedding Service — abstract interface for generating text embeddings.

Provides a clean separation between retrieval business logic and
embedding provider implementations. Supports fake/test providers
and real providers through a common interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class EmbeddingResult:
    """Result of embedding one or more texts."""

    embeddings: List[List[float]]
    model: str
    dimension: int
    cache_hits: int = 0
    duration_seconds: float = 0.0


class EmbeddingService(ABC):
    """Abstract embedding service.

    Provider-agnostic interface for generating embeddings.
    All provider-specific details are encapsulated in implementations.
    """

    def __init__(self, model: str = "default"):
        self._model = model
        self._dimension: int = 0
        self._cache: Dict[str, List[float]] = {}

    @abstractmethod
    def embed_documents(self, texts: List[str]) -> EmbeddingResult:
        """Embed a list of document texts.

        Args:
            texts: List of text strings to embed.

        Returns:
            EmbeddingResult with embeddings array.
        """
        ...

    @abstractmethod
    def embed_query(self, text: str) -> List[float]:
        """Embed a single query text.

        Args:
            text: Query text string.

        Returns:
            Embedding vector as list of floats.
        """
        ...

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Dimension of the embedding vectors."""
        ...

    @property
    def model(self) -> str:
        return self._model

    def clear_cache(self) -> None:
        """Clear the embedding cache."""
        self._cache.clear()

    def _check_cache(self, text: str) -> Optional[List[float]]:
        """Check if a text's embedding is cached."""
        key = self._cache_key(text)
        return self._cache.get(key)

    def _set_cache(self, text: str, embedding: List[float]) -> None:
        """Cache an embedding."""
        key = self._cache_key(text)
        self._cache[key] = embedding

    @staticmethod
    def _cache_key(text: str) -> str:
        """Create a cache key from text."""
        import hashlib
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
