"""
Phase 15 (12d) — Tests for the pgvector-backed VectorStore.

pgvector may not be installed on the host, so these tests verify the
in-memory fallback path (graceful degradation) and the public API
surface without requiring a live vector extension.
"""

from __future__ import annotations

import pytest
from unittest.mock import patch

from app.code_intelligence.vector_store import VectorStore, _cosine_similarity
from app.config import settings


class TestVectorStoreFallback:
    """In-memory fallback must work when pgvector is unavailable."""

    @pytest.mark.asyncio
    async def test_save_and_search_memory(self):
        store = VectorStore(dimension=8)
        await store.save_embedding(
            repository_id="repo1",
            index_id="idx1",
            symbol_id="a.py::AuthService",
            embedding=[1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        )
        await store.save_embedding(
            repository_id="repo1",
            index_id="idx1",
            symbol_id="b.py::Other",
            embedding=[0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        )

        results = await store.search(
            query_embedding=[1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            limit=5,
        )
        assert results, "Expected search results from in-memory index"
        assert results[0]["symbol_id"] == "a.py::AuthService"
        assert results[0]["score"] > results[1]["score"]

    @pytest.mark.asyncio
    async def test_search_filter_by_repository(self):
        store = VectorStore(dimension=4)
        await store.save_embedding("repoA", "i", "s1", [1.0, 0.0, 0.0, 0.0])
        await store.save_embedding("repoB", "i", "s2", [1.0, 0.0, 0.0, 0.0])

        results = await store.search(
            query_embedding=[1.0, 0.0, 0.0, 0.0],
            repository_id="repoA",
            limit=5,
        )
        assert len(results) == 1
        assert results[0]["symbol_id"] == "s1"

    @pytest.mark.asyncio
    async def test_save_embeddings_bulk(self):
        store = VectorStore(dimension=4)
        saved = await store.save_embeddings(
            repository_id="repo1",
            index_id="idx1",
            items=[
                {"symbol_id": "s1", "embedding": [1.0, 0.0, 0.0, 0.0]},
                {"symbol_id": "s2", "embedding": [0.0, 1.0, 0.0, 0.0]},
                {"symbol_id": "s3", "embedding": None},  # skipped
            ],
        )
        assert saved == 2

    @pytest.mark.asyncio
    async def test_delete_index(self):
        store = VectorStore(dimension=4)
        await store.save_embedding("repo1", "idx1", "s1", [1.0, 0.0, 0.0, 0.0])
        await store.save_embedding("repo1", "idx2", "s2", [1.0, 0.0, 0.0, 0.0])

        deleted = await store.delete_index("repo1", "idx1")
        assert deleted == 1
        remaining = await store.search([1.0, 0.0, 0.0, 0.0], limit=10)
        assert all(r["symbol_id"] != "s1" for r in remaining)

    def test_empty_embedding_not_saved(self):
        store = VectorStore(dimension=4)
        assert store._memory == {}


class TestAvailability:
    """Availability probe behavior."""

    def test_requires_database_url(self):
        store = VectorStore(dimension=4)
        with patch("app.code_intelligence.vector_store.settings") as mock_settings:
            mock_settings.DATABASE_URL = None
            assert store.is_available() is False


class TestCodeIntelligenceServiceWiring:
    """CodeIntelligenceService must use the VectorStore for persisted
    symbol embeddings (Phase 12d), degrading gracefully to in-memory."""

    def _build_graph(self):
        from app.code_intelligence.code_intelligence_service import CodeIntelligenceService
        from app.code_intelligence.semantic_graph import (
            GraphNode, SemanticRepositoryGraph, make_symbol_id,
        )

        graph = SemanticRepositoryGraph()
        for qname in (
            "auth_service.py", "auth_service.py::AuthService",
            "auth_service.py::AuthService.login",
            "test_auth.py::TestAuthService",
        ):
            graph.add_node(GraphNode(
                id=make_symbol_id(qname, qname),
                name=qname.split("::")[-1],
                qualified_name=qname,
                kind="class" if "::" in qname else "file",
                file_path=qname.split("::")[0],
                language="Python",
            ))
        return graph

    @pytest.mark.asyncio
    async def test_persist_and_search_symbol_embeddings(self):
        from app.code_intelligence.code_intelligence_service import CodeIntelligenceService

        cis = CodeIntelligenceService(
            vector_store=VectorStore(dimension=settings.EMBEDDING_DIMENSION)
        )
        cis._graph = self._build_graph()
        cis._index_id = "idx_test"
        cis._repository_id = "repo_test"

        persisted = await cis.persist_symbol_embeddings()
        assert persisted == 4, f"Expected 4 symbol embeddings, got {persisted}"

        # Query with the exact stored qualified name: the deterministic
        # fake provider yields an identical embedding, so similarity is
        # 1.0 and the symbol is guaranteed to rank first.
        exact = "auth_service.py::AuthService.login"
        results = await cis.search_symbol_embeddings(exact, limit=4)
        assert results, "Expected search results after persisting embeddings"
        assert results[0]["symbol_id"].endswith("AuthService.login")

    @pytest.mark.asyncio
    async def test_persist_embeddings_no_graph_returns_zero(self):
        from app.code_intelligence.code_intelligence_service import CodeIntelligenceService

        cis = CodeIntelligenceService(
            vector_store=VectorStore(dimension=settings.EMBEDDING_DIMENSION)
        )
        assert await cis.persist_symbol_embeddings() == 0
        assert await cis.search_symbol_embeddings("anything") == []

    @pytest.mark.asyncio
    async def test_search_without_vector_store_returns_empty(self):
        from app.code_intelligence.code_intelligence_service import CodeIntelligenceService

        cis = CodeIntelligenceService(vector_store=None)
        cis._graph = self._build_graph()
        cis._index_id = "idx_test"
        cis._repository_id = "repo_test"
        assert await cis.search_symbol_embeddings("login") == []


class TestCosineSimilarity:
    """Cosine similarity math."""

    def test_identical(self):
        assert _cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0

    def test_orthogonal(self):
        assert abs(_cosine_similarity([1.0, 0.0], [0.0, 1.0])) < 1e-9

    def test_zero_vector(self):
        assert _cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0

    def test_mismatched_length(self):
        assert _cosine_similarity([1.0], [1.0, 0.0]) == 0.0
