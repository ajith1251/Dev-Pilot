"""Embedding service abstraction for semantic retrieval.

Provider-agnostic interface with factory function to create
the appropriate provider based on configuration.
"""

from typing import Optional

from app.rag.embeddings.base import EmbeddingResult, EmbeddingService
from app.rag.embeddings.fake_provider import FakeEmbeddingProvider


def create_embedding_service(
    provider: str = "fake",
    model: str = "text-embedding-3-small",
    dimension: int = 256,
) -> EmbeddingService:
    """Factory function to create an embedding service from configuration.

    Args:
        provider: Provider name ('fake', 'hashed', 'openai').
        model: Model name (e.g. 'text-embedding-3-small').
        dimension: Embedding vector dimension.

    Returns:
        An initialized EmbeddingService instance.

    Raises:
        ValueError: If the provider name is unknown or unsupported.
    """
    if provider == "fake":
        return FakeEmbeddingProvider(dimension=dimension, model=model)

    if provider == "hashed":
        from app.rag.embeddings.hashed_provider import HashedNGramEmbeddingProvider

        return HashedNGramEmbeddingProvider(dimension=dimension, model=model)

    if provider == "openai":
        from app.rag.embeddings.openai_provider import OpenAIEmbeddingProvider

        return OpenAIEmbeddingProvider(
            model=model,
            dimension=dimension,
        )

    if provider == "anthropic":
        raise NotImplementedError(
            "Anthropic embedding provider is not yet implemented. "
            "Use 'openai' or 'fake' instead."
        )

    raise ValueError(
        f"Unknown embedding provider: '{provider}'. "
        f"Supported providers: 'fake', 'hashed', 'openai'."
    )


# Lazy-load heavy providers only when imported directly
# OpenAIEmbeddingProvider requires the openai package (already a dependency)


__all__ = [
    "EmbeddingService",
    "EmbeddingResult",
    "FakeEmbeddingProvider",
    "create_embedding_service",
]
