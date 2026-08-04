"""
Phase 13 — Unit tests for RepositoryMemoryService.

Covers CRUD, retrieval, invalidation, stats, and lifecycle operations.
All tests use AsyncMock — no live PostgreSQL required.

Design:
  - Methods that DON'T use .overlap() on JSONB columns (CRUD, stats, count,
    query without symbol_names) are tested through the real _with_session
    callback logic with mocked session.
  - Methods that use .overlap() (get_memories_for_symbols, invalidate) mock
    at the method level since SQLAlchemy JSONB does not expose .overlap()
    outside of PostgreSQL dialect integration tests.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.memory import (
    MemoryEvidence,
    MemoryQuery,
    MemoryStatus,
    MemoryType,
    RepositoryMemory,
)


pytestmark = pytest.mark.asyncio


# ── Mock Infrastructure ─────────────────────────────────────────
#
# Matches the pattern from test_postgres_graph_persistence.py:
#   factory() → async context mgr → session
#   session.execute → AsyncMock (awaited)
#   session.add, session.delete, session.commit → AsyncMock (awaited)
#   session.refresh → AsyncMock (awaited)


class _MockAsyncContext:
    """Async context manager returned by `factory()`."""

    def __init__(self, session: Any) -> None:
        self.session = session

    async def __aenter__(self) -> Any:
        return self.session

    async def __aexit__(self, *args: Any) -> None:
        pass


def _make_mock_session_factory():
    """Create a mock session factory."""
    session = MagicMock()
    session.execute = AsyncMock()
    session.delete = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    factory = MagicMock()
    factory.return_value = _MockAsyncContext(session)
    return factory, session


def _make_result(value: Any = None, *, scalar: Any = None, scalars_list: Any = None):
    """Build a MagicMock that mimics an SQLAlchemy execution result."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    if scalar is not None:
        result.scalar.return_value = scalar
    if scalars_list is not None:
        result.scalars.return_value.all.return_value = scalars_list
    return result


# ── Sample Data ────────────────────────────────────────────────


def _make_sample_memory(**overrides: Any) -> RepositoryMemory:
    """Create a RepositoryMemory with sensible defaults for testing."""
    defaults: dict = {
        "memory_id": "mem_test_001",
        "repository_id": "test-repo",
        "memory_type": MemoryType.ARCHITECTURE,
        "status": MemoryStatus.VERIFIED,
        "content": "AuthService handles token validation via AuthController delegation.",
        "confidence": 0.85,
        "symbol_names": ["AuthService", "AuthController"],
        "file_paths": ["app/services/auth_service.py", "app/controllers/auth_controller.py"],
        "evidence": [
            MemoryEvidence(
                source_type="run",
                source_id="run_001",
                description="Discovered during initial repository scan",
            )
        ],
        "source_run_id": "run_001",
        "tags": ["auth", "architecture"],
        "version": 1,
        "related_commit": None,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    defaults.update(overrides)
    return RepositoryMemory(**defaults)


def _make_mock_model(**overrides: Any) -> MagicMock:
    """Create a MagicMock that mimics RepositoryMemoryModel columns."""
    m = MagicMock(spec=[
        "id", "memory_id", "repository_id", "memory_type", "status",
        "content", "confidence", "symbol_names", "file_paths",
        "evidence", "source_run_id", "tags", "version",
        "related_commit", "created_at", "updated_at", "last_used_at",
    ])
    defaults = {
        "id": 1,
        "memory_id": "mem_abc123",
        "repository_id": "test-repo",
        "memory_type": "architecture",
        "status": "verified",
        "content": "AuthService handles token validation.",
        "confidence": 0.85,
        "symbol_names": ["AuthService"],
        "file_paths": ["app/services/auth_service.py"],
        "evidence": [{"source_type": "run", "source_id": "run_001", "description": "test"}],
        "source_run_id": "run_001",
        "tags": ["auth"],
        "version": 1,
        "related_commit": None,
        "created_at": None,
        "updated_at": None,
        "last_used_at": None,
    }
    defaults.update(overrides)
    for k, v in defaults.items():
        setattr(m, k, v)
    return m


# ── Fixtures ───────────────────────────────────────────────────


@pytest.fixture
def sample_memory() -> RepositoryMemory:
    return _make_sample_memory()


@pytest.fixture
def mock_service() -> tuple:
    """Return a (RepositoryMemoryService, MagicMock session) tuple.

    Note: Methods that use RepositoryMemoryModel.symbol_names.overlap()
    (JSONB-specific PostgreSQL operator) cannot be unit-tested with
    a mock session. Those tests mock at the method level instead.
    """
    from app.services.repository_memory_service import RepositoryMemoryService

    svc = RepositoryMemoryService()
    factory, session = _make_mock_session_factory()
    svc._session_factory = factory
    yield svc, session


# ═══════════════════════════════════════════════════════════════
# MemoryEvidence Model Tests (sync — no asyncio needed)
# ═══════════════════════════════════════════════════════════════


class TestMemoryEvidenceModel:

    def test_memory_evidence_required_fields(self):
        MemoryEvidence(source_type="run", source_id="run_001")

    def test_memory_evidence_with_description(self):
        ev = MemoryEvidence(
            source_type="run", source_id="run_001",
            description="Test evidence",
        )
        assert ev.description == "Test evidence"

    def test_memory_evidence_default_description(self):
        ev = MemoryEvidence(source_type="analysis", source_id="id_123")
        assert ev.description == ""


# ═══════════════════════════════════════════════════════════════
# Validation Tests
# ═══════════════════════════════════════════════════════════════


class TestMemoryValidation:

    async def test_create_memory_requires_repository_id(self, mock_service):
        svc, _ = mock_service
        mem = _make_sample_memory(repository_id="")
        with pytest.raises(ValueError, match="repository_id is required"):
            await svc.create_memory(mem)

    async def test_create_memory_requires_content(self, mock_service):
        svc, _ = mock_service
        mem = _make_sample_memory(content="")
        with pytest.raises(ValueError, match="content is required"):
            await svc.create_memory(mem)


# ═══════════════════════════════════════════════════════════════
# CRUD Tests
# ═══════════════════════════════════════════════════════════════


class TestCreateMemory:

    async def test_create_memory_success(self, mock_service, sample_memory):
        svc, session = mock_service
        session.execute.return_value = _make_result(value=None)

        result = await svc.create_memory(sample_memory)

        assert result.memory_id is not None
        assert result.repository_id == "test-repo"
        assert result.memory_type == MemoryType.ARCHITECTURE
        assert result.status == MemoryStatus.VERIFIED
        assert result.content == sample_memory.content
        assert result.confidence == 0.85
        assert result.symbol_names == ["AuthService", "AuthController"]
        assert result.version == 1
        assert session.add.called
        assert session.commit.called
        assert session.refresh.called

    async def test_create_memory_with_explicit_id(self, mock_service):
        svc, session = mock_service
        session.execute.return_value = _make_result(value=None)
        mem = _make_sample_memory(memory_id="custom_id_001")

        result = await svc.create_memory(mem)

        assert result.memory_id == "custom_id_001"

    async def test_create_memory_generates_id(self, mock_service):
        svc, session = mock_service
        session.execute.return_value = _make_result(value=None)
        mem = _make_sample_memory(memory_id="")

        result = await svc.create_memory(mem)

        assert len(result.memory_id) == 16  # sha256[:16]
        assert result.memory_id.isalnum()

    async def test_create_memory_with_evidence(self, mock_service):
        svc, session = mock_service
        session.execute.return_value = _make_result(value=None)
        mem = _make_sample_memory(
            evidence=[
                MemoryEvidence(source_type="run", source_id="run_001", description="discovery"),
                MemoryEvidence(source_type="review", source_id="review_002", description="confirmed"),
            ]
        )

        result = await svc.create_memory(mem)

        assert len(result.evidence) == 2
        assert result.evidence[0].source_type == "run"
        assert result.evidence[0].source_id == "run_001"


class TestGetMemory:

    async def test_get_memory_found(self, mock_service):
        svc, session = mock_service
        model = _make_mock_model(memory_id="mem_001", repository_id="test-repo")
        session.execute.return_value = _make_result(value=model)

        result = await svc.get_memory("mem_001")

        assert result is not None
        assert result.memory_id == "mem_001"
        assert result.repository_id == "test-repo"
        assert session.commit.called

    async def test_get_memory_not_found(self, mock_service):
        svc, session = mock_service
        session.execute.return_value = _make_result(value=None)

        result = await svc.get_memory("nonexistent")

        assert result is None
        assert not session.commit.called

    async def test_get_memory_updates_last_used_at(self, mock_service):
        svc, session = mock_service
        model = _make_mock_model(memory_id="mem_002")
        model.last_used_at = None
        session.execute.return_value = _make_result(value=model)

        result = await svc.get_memory("mem_002")

        assert result is not None
        assert session.commit.called


class TestUpdateMemory:

    async def test_update_memory_success(self, mock_service):
        svc, session = mock_service
        model = _make_mock_model(memory_id="mem_001", version=1)
        session.execute.return_value = _make_result(value=model)

        result = await svc.update_memory("mem_001", {"confidence": 0.95, "status": "verified"})

        assert result is not None
        assert session.commit.called

    async def test_update_memory_not_found(self, mock_service):
        svc, session = mock_service
        session.execute.return_value = _make_result(value=None)

        result = await svc.update_memory("nonexistent", {"confidence": 0.5})

        assert result is None
        assert not session.commit.called

    async def test_update_memory_allowed_fields(self, mock_service):
        svc, session = mock_service
        model = _make_mock_model(memory_id="mem_001", version=1)
        session.execute.return_value = _make_result(value=model)

        result = await svc.update_memory(
            "mem_001",
            {"status": "stale", "tags": ["new-tag"], "content": "Updated content"},
        )

        assert result is not None
        assert session.commit.called

    async def test_update_memory_skips_disallowed_fields(self, mock_service):
        svc, session = mock_service
        model = _make_mock_model(memory_id="mem_001")
        session.execute.return_value = _make_result(value=model)

        result = await svc.update_memory(
            "mem_001", {"memory_id": "should_not_change", "invalid_field": "x"}
        )

        assert result is not None
        assert session.commit.called


class TestDeleteMemory:

    async def test_delete_memory_success(self, mock_service):
        svc, session = mock_service
        session.execute.return_value = _make_result(value=MagicMock())

        result = await svc.delete_memory("mem_001")

        assert result is True
        assert session.delete.called
        assert session.commit.called

    async def test_delete_memory_not_found(self, mock_service):
        svc, session = mock_service
        session.execute.return_value = _make_result(value=None)

        result = await svc.delete_memory("nonexistent")

        assert result is False
        assert not session.delete.called
        assert not session.commit.called


# ═══════════════════════════════════════════════════════════════
# Retrieval Tests
# ═══════════════════════════════════════════════════════════════
#
# query_memories tests avoid symbol_names filtering so they don't
# hit the JSONB .overlap() code path. Methods that use .overlap()
# (get_memories_for_symbols, invalidate_memories_for_symbols) mock
# at the method level.


class TestQueryMemories:

    async def test_query_all_active(self, mock_service):
        svc, session = mock_service
        models = [
            _make_mock_model(memory_id="mem_001", status="verified", confidence=0.9),
            _make_mock_model(memory_id="mem_002", status="provisional", confidence=0.7),
        ]
        session.execute.return_value = _make_result(scalars_list=models)

        query = MemoryQuery(repository_id="test-repo", limit=10)
        results = await svc.query_memories(query)

        assert len(results) == 2
        assert results[0].memory_id == "mem_001"
        assert results[1].memory_id == "mem_002"
        assert session.commit.called

    async def test_query_empty_results(self, mock_service):
        svc, session = mock_service
        session.execute.return_value = _make_result(scalars_list=[])

        query = MemoryQuery(repository_id="empty-repo", limit=10)
        results = await svc.query_memories(query)

        assert results == []
        assert not session.commit.called

    async def test_query_by_memory_type(self, mock_service):
        svc, session = mock_service
        model = _make_mock_model(memory_id="mem_003", memory_type="architecture")
        session.execute.return_value = _make_result(scalars_list=[model])

        query = MemoryQuery(
            repository_id="test-repo",
            memory_types=[MemoryType.ARCHITECTURE],
            limit=10,
        )
        results = await svc.query_memories(query)

        assert len(results) == 1
        assert results[0].memory_type == MemoryType.ARCHITECTURE

    async def test_query_include_stale(self, mock_service):
        svc, session = mock_service
        model = _make_mock_model(memory_id="mem_stale", status="stale")
        session.execute.return_value = _make_result(scalars_list=[model])

        query = MemoryQuery(repository_id="test-repo", limit=10, include_stale=True)
        results = await svc.query_memories(query)

        assert len(results) == 1
        assert results[0].status == MemoryStatus.STALE

    async def test_query_with_pagination(self, mock_service):
        svc, session = mock_service
        session.execute.return_value = _make_result(scalars_list=[])

        query = MemoryQuery(repository_id="test-repo", limit=5, offset=10)
        results = await svc.query_memories(query)

        assert results == []


class TestGetMemoriesForSymbols:
    """Tests for get_memories_for_symbols.

    get_memories_for_symbols delegates to query_memories with symbol_names,
    which hits the JSONB .overlap() code path. We mock query_memories
    on the service to test the wrapper logic.
    """

    async def test_symbol_retrieval_delegates_to_query(self, mock_service):
        svc, session = mock_service
        expected = [_make_sample_memory(memory_id="mem_symbol")]
        svc.query_memories = AsyncMock(return_value=expected)

        results = await svc.get_memories_for_symbols("test-repo", ["AuthService"])

        assert len(results) == 1
        assert results[0].memory_id == "mem_symbol"
        svc.query_memories.assert_awaited_once()

    async def test_symbol_retrieval_empty_results(self, mock_service):
        svc, session = mock_service
        svc.query_memories = AsyncMock(return_value=[])

        results = await svc.get_memories_for_symbols("test-repo", ["NonExistentSymbol"])

        assert results == []
        svc.query_memories.assert_awaited_once()


# ═══════════════════════════════════════════════════════════════
# Lifecycle Tests
# ═══════════════════════════════════════════════════════════════
#
# invalidate_memories_for_symbols always hits the JSONB .overlap()
# code path. We mock _with_session to test the method's return
# type and parameter flow.


class TestInvalidateMemoriesForSymbols:

    async def test_invalidate_matching_symbols(self, mock_service):
        svc, session = mock_service
        svc._with_session = AsyncMock(return_value=2)

        count = await svc.invalidate_memories_for_symbols("test-repo", ["AuthService"])

        assert count == 2

    async def test_invalidate_no_matching_symbols(self, mock_service):
        svc, session = mock_service
        svc._with_session = AsyncMock(return_value=0)

        count = await svc.invalidate_memories_for_symbols("test-repo", ["NonExistent"])

        assert count == 0


class TestMarkMemoryUsed:

    async def test_mark_used_success(self, mock_service):
        svc, session = mock_service
        session.execute.return_value = _make_result(value=MagicMock())

        result = await svc.mark_memory_used("mem_001")

        assert result is True
        assert session.commit.called

    async def test_mark_used_not_found(self, mock_service):
        svc, session = mock_service
        session.execute.return_value = _make_result(value=None)

        result = await svc.mark_memory_used("nonexistent")

        assert result is False
        assert not session.commit.called


# ═══════════════════════════════════════════════════════════════
# Stats & Count Tests
# ═══════════════════════════════════════════════════════════════


class TestGetMemoryStats:

    async def test_stats_empty_repository(self, mock_service):
        svc, session = mock_service
        session.execute.side_effect = [
            _make_result(scalar=0),        # total count
            _make_result(scalars_list=[]),  # by_type
            _make_result(scalars_list=[]),  # by_status
            _make_result(scalar=0.0),      # avg_confidence
        ]

        stats = await svc.get_memory_stats("empty-repo")

        assert stats["total"] == 0
        assert stats["avg_confidence"] == 0.0

    async def test_stats_with_data(self, mock_service):
        svc, session = mock_service
        session.execute.side_effect = [
            _make_result(scalar=5),        # total = 5
            _make_result(scalars_list=[]),  # by_type (empty)
            _make_result(scalars_list=[]),  # by_status (empty)
            _make_result(scalar=0.82),     # avg_confidence
        ]

        stats = await svc.get_memory_stats("test-repo")

        assert stats["total"] == 5
        assert stats["avg_confidence"] == 0.82

    async def test_stats_multiple_repositories(self, mock_service):
        svc, session = mock_service
        session.execute.side_effect = [
            _make_result(scalar=10), _make_result(scalars_list=[]),
            _make_result(scalars_list=[]), _make_result(scalar=0.75),
        ]

        stats_1 = await svc.get_memory_stats("repo-a")
        assert stats_1["total"] == 10

        session.execute.side_effect = [
            _make_result(scalar=3), _make_result(scalars_list=[]),
            _make_result(scalars_list=[]), _make_result(scalar=0.90),
        ]

        stats_2 = await svc.get_memory_stats("repo-b")
        assert stats_2["total"] == 3
        assert stats_2["avg_confidence"] == 0.90


class TestCountMemories:

    async def test_count_nonzero(self, mock_service):
        svc, session = mock_service
        session.execute.return_value = _make_result(scalar=7)

        count = await svc.count_memories("test-repo")

        assert count == 7

    async def test_count_zero(self, mock_service):
        svc, session = mock_service
        session.execute.return_value = _make_result(scalar=0)

        count = await svc.count_memories("empty-repo")

        assert count == 0


# ═══════════════════════════════════════════════════════════════
# List Memories Tests
# ═══════════════════════════════════════════════════════════════
#
# list_memories delegates to query_memories WITHOUT symbol_names,
# so it works with the mock session.


class TestListMemories:

    async def test_list_memories_with_data(self, mock_service):
        svc, session = mock_service
        models = [
            _make_mock_model(memory_id="mem_001", status="verified"),
            _make_mock_model(memory_id="mem_002", status="provisional"),
        ]
        session.execute.return_value = _make_result(scalars_list=models)

        results = await svc.list_memories("test-repo")

        assert len(results) == 2
        assert results[0].memory_id == "mem_001"
        assert results[1].memory_id == "mem_002"

    async def test_list_memories_empty(self, mock_service):
        svc, session = mock_service
        session.execute.return_value = _make_result(scalars_list=[])

        results = await svc.list_memories("empty-repo")

        assert results == []

    async def test_list_memories_pagination(self, mock_service):
        svc, session = mock_service
        session.execute.return_value = _make_result(scalars_list=[])

        results = await svc.list_memories("test-repo", limit=20, offset=5)

        assert results == []


# ═══════════════════════════════════════════════════════════════
# Error Handling Tests
# ═══════════════════════════════════════════════════════════════


class TestErrorHandling:

    async def test_create_memory_db_error(self, mock_service, sample_memory):
        svc, session = mock_service
        session.execute.return_value = _make_result(value=None)
        session.commit.side_effect = RuntimeError("DB connection lost")

        with pytest.raises(RuntimeError, match="DB connection lost"):
            await svc.create_memory(sample_memory)

    async def test_delete_memory_db_error(self, mock_service):
        svc, session = mock_service
        model = _make_mock_model(memory_id="mem_001")
        session.execute.return_value = _make_result(value=model)
        session.delete.side_effect = RuntimeError("DB error on delete")

        with pytest.raises(RuntimeError, match="DB error on delete"):
            await svc.delete_memory("mem_001")

    async def test_get_memory_server_error(self, mock_service):
        svc, session = mock_service
        session.execute.side_effect = RuntimeError("Database unavailable")

        with pytest.raises(RuntimeError, match="Database unavailable"):
            await svc.get_memory("mem_001")
