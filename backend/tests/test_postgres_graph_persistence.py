"""
Tests for PostgresRunStore graph persistence methods (save_graph, load_graph, etc.).

Uses mocks — no live PostgreSQL required.
"""

from __future__ import annotations

from typing import Any

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.code_intelligence.semantic_graph import (
    ConfidenceLevel,
    GraphNode,
    RelationshipType,
    SemanticRepositoryGraph,
    make_symbol_id,
)

pytestmark = pytest.mark.asyncio


# ── Mock Infrastructure ─────────────────────────────────────────
#
# Architecture:
#   PostgresRunStore._with_session() does:
#       async with factory() as session:        # factory() returns ctx mgr
#           return await callback(session)       # session.execute() is awaited
#
#   ┌─────────────────────────────────────────────────────┐
#   │ factory = MagicMock()   ← NOT awaited               │
#   │   .return_value = _MockAsyncContext(session)        │
#   │     .__aenter__()  → session (used directly)        │
#   │     .__aexit__()                                     │
#   │                                                       │
#   │ session = MagicMock() ← NOT awaited                  │
#   │   .execute = AsyncMock()  ← IS awaited               │
#   │     .return_value = MagicMock()  ← sync result obj   │
#   │       .scalar_one_or_none() → value (sync)           │
#   │       .scalars().all() → list (sync)                 │
#   │   .add(), .delete(), .commit() → MagicMock (sync)    │
#   └─────────────────────────────────────────────────────┘


class _MockAsyncContext:
    """Async context manager returned by `factory()`.

    Defined as a class so __aenter__/__aexit__ are looked up on the
    *type*, not the instance — this is required by Python's magic-
    method dispatch.
    """

    def __init__(self, session: Any) -> None:
        self.session = session

    async def __aenter__(self) -> Any:
        return self.session

    async def __aexit__(self, *args: Any) -> None:
        pass


def _make_mock_session_factory():
    """Create a mock session factory that correctly handles `async with` + `await`.

    - session: MagicMock — NOT directly awaited
    - session.execute: AsyncMock — IS awaited by runstore internals
    - session.delete: AsyncMock — also awaited (delete_graph)
    - session.commit: AsyncMock — also awaited
    - session.flush: MagicMock — NOT awaited
    - session.add: MagicMock — NOT awaited
    """
    session = MagicMock()
    # Methods that ARE awaited must be AsyncMock
    session.execute = AsyncMock()
    session.delete = AsyncMock()
    session.commit = AsyncMock()
    # Methods NOT awaited can stay as MagicMock (default)
    factory = MagicMock()
    factory.return_value = _MockAsyncContext(session)
    return factory, session


# ── Sample Graph Builder ───────────────────────────────────────


def _make_sample_graph() -> SemanticRepositoryGraph:
    """Build a small graph with nodes and edges for persistence testing."""
    graph = SemanticRepositoryGraph()

    sid = lambda f, n: make_symbol_id(f, n)

    graph.add_node(GraphNode(
        id=sid("app/service.py", "app.service"),
        name="service.py", qualified_name="app.service",
        kind="file", file_path="app/service.py", language="Python",
    ))
    graph.add_node(GraphNode(
        id=sid("app/models.py", "app.models"),
        name="models.py", qualified_name="app.models",
        kind="file", file_path="app/models.py", language="Python",
    ))
    graph.add_node(GraphNode(
        id=sid("app/service.py", "app.service.AuthService"),
        name="AuthService", qualified_name="app.service.AuthService",
        kind="class", file_path="app/service.py", language="Python",
        start_line=10, end_line=50,
    ))
    graph.add_node(GraphNode(
        id=sid("app/service.py", "app.service.AuthService.login"),
        name="login", qualified_name="app.service.AuthService.login",
        kind="method", file_path="app/service.py", language="Python",
        parent_id=sid("app/service.py", "app.service.AuthService"),
        start_line=15, end_line=30,
    ))
    graph.add_node(GraphNode(
        id=sid("app/models.py", "app.models.User"),
        name="User", qualified_name="app.models.User",
        kind="class", file_path="app/models.py", language="Python",
        start_line=5, end_line=25,
    ))

    graph.add_edge(source_id=sid("app/service.py", "app.service"),
                   target_id=sid("app/service.py", "app.service.AuthService"),
                   relationship=RelationshipType.CONTAINS,
                   confidence=ConfidenceLevel.EXACT)
    graph.add_edge(source_id=sid("app/service.py", "app.service.AuthService"),
                   target_id=sid("app/service.py", "app.service.AuthService.login"),
                   relationship=RelationshipType.CONTAINS,
                   confidence=ConfidenceLevel.EXACT)
    graph.add_edge(source_id=sid("app/service.py", "app.service.AuthService.login"),
                   target_id=sid("app/models.py", "app.models.User"),
                   relationship=RelationshipType.REFERENCES,
                   confidence=ConfidenceLevel.HIGH,
                   source_lines=[18], resolution_detail="type annotation")
    graph.add_edge(source_id=sid("app/service.py", "app.service.AuthService"),
                   target_id=sid("app/models.py", "app.models.User"),
                   relationship=RelationshipType.DEPENDS_ON,
                   confidence=ConfidenceLevel.MEDIUM)
    return graph


def _make_result(value: Any = None, *, scalar: Any = None, scalars_list: Any = None):
    """Build a MagicMock that mimics an SQLAlchemy execution result.

    Args:
        value: Return value for .scalar_one_or_none() (None explicitly allowed)
        scalar: Return value for .scalar()
        scalars_list: Return value for .scalars().all()
    """
    result = MagicMock()
    # Always set (value=None is meaningful — means "no result")
    result.scalar_one_or_none.return_value = value
    if scalar is not None:
        result.scalar.return_value = scalar
    if scalars_list is not None:
        result.scalars.return_value.all.return_value = scalars_list
    return result


# ── Fixtures ────────────────────────────────────────────────────


@pytest.fixture
def sample_graph():
    return _make_sample_graph()


@pytest.fixture
def mock_store():
    """Return a PostgresRunStore with a mocked session factory."""
    from app.services.postgres_run_store import PostgresRunStore

    store = PostgresRunStore()
    factory, session = _make_mock_session_factory()
    store._session_factory = factory
    yield store, session


# ═══════════════════════════════════════════════════════════════
# save_graph Tests
# ═══════════════════════════════════════════════════════════════


class TestSaveGraph:

    async def test_save_graph_creates_index_metadata(self, mock_store, sample_graph):
        store, session = mock_store
        # save_graph calls execute 5 times (SELECT, DELETE×2, INSERT×2)
        # Use return_value so every call gets the same MagicMock result
        session.execute.return_value = _make_result(value=None)

        result = await store.save_graph(
            graph=sample_graph,
            repository_id="test-repo",
            index_id="idx_test_001",
            repository_path="/tmp/test-repo",
            file_count=2,
        )

        assert result["index_id"] == "idx_test_001"
        assert result["repository_id"] == "test-repo"
        assert result["symbol_count"] == 5
        assert result["relationship_count"] == 4
        assert result["status"] == "active"
        assert session.add.called
        assert session.commit.called

    async def test_save_graph_updates_existing_index(self, mock_store, sample_graph):
        store, session = mock_store
        existing = MagicMock(spec=["index_id", "status"])
        existing.index_id = "idx_test_001"
        existing.status = "stale"
        session.execute.return_value = _make_result(value=existing)

        result = await store.save_graph(
            graph=sample_graph,
            repository_id="test-repo",
            index_id="idx_test_001",
            repository_path="/tmp/test-repo",
            file_count=2,
        )

        assert result["status"] == "active"
        assert existing.status == "active"

    async def test_save_graph_empty_graph(self, mock_store):
        store, session = mock_store
        empty = SemanticRepositoryGraph()
        session.execute.return_value = _make_result(value=None)

        result = await store.save_graph(
            graph=empty,
            repository_id="test-repo",
            index_id="idx_empty",
            repository_path="/tmp/test-repo",
        )

        assert result["symbol_count"] == 0
        assert result["relationship_count"] == 0
        assert result["status"] == "active"

    async def test_save_graph_rejects_wrong_type(self, mock_store):
        store, _ = mock_store
        with pytest.raises(TypeError, match="Expected SemanticRepositoryGraph"):
            await store.save_graph(
                graph="not-a-graph",  # type: ignore
                repository_id="test-repo", index_id="idx_test",
                repository_path="/tmp/test-repo",
            )

    async def test_save_graph_bulk_insert_chunking(self, mock_store):
        store, session = mock_store
        big = SemanticRepositoryGraph()
        for i in range(601):
            big.add_node(GraphNode(
                id=f"f{i}::s{i}", name=f"s{i}",
                qualified_name=f"m.s{i}", kind="function",
                file_path=f"f{i}.py", language="Python",
            ))

        session.execute.return_value = _make_result(value=None)

        result = await store.save_graph(
            graph=big,
            repository_id="test-repo", index_id="idx_big",
            repository_path="/tmp/test-repo",
        )

        assert result["symbol_count"] == 601
        assert session.execute.called

    async def test_save_graph_with_language_coverage(self, mock_store, sample_graph):
        store, session = mock_store
        session.execute.return_value = _make_result(value=None)

        result = await store.save_graph(
            graph=sample_graph,
            repository_id="test-repo", index_id="idx_coverage",
            repository_path="/tmp/test-repo",
            language_coverage={"Python": 2}, file_count=2,
        )

        assert result["index_id"] == "idx_coverage"

    async def test_save_graph_upsert_clears_old_data(self, mock_store, sample_graph):
        store, session = mock_store
        existing = MagicMock(spec=["index_id", "status"])
        existing.index_id = "idx_upsert"
        existing.status = "active"
        session.execute.return_value = _make_result(value=existing)

        await store.save_graph(
            graph=sample_graph,
            repository_id="test-repo", index_id="idx_upsert",
            repository_path="/tmp/test-repo",
        )

        # Should have called session.execute multiple times
        assert len(session.execute.call_args_list) >= 5


# ═══════════════════════════════════════════════════════════════
# load_graph Tests
# ═══════════════════════════════════════════════════════════════


class TestLoadGraph:

    def _make_index_model(self, **overrides):
        m = MagicMock(spec=[
            "index_id", "repository_id", "repository_path",
            "content_fingerprint", "language_coverage",
            "symbol_count", "relationship_count", "file_count",
            "status", "version", "created_at", "updated_at",
        ])
        defaults = {
            "index_id": "idx_test_001",
            "repository_id": "test-repo",
            "repository_path": "/tmp/test-repo",
            "content_fingerprint": None,
            "language_coverage": None,
            "symbol_count": 5,
            "relationship_count": 4,
            "file_count": 2,
            "status": "active",
            "version": "12.0",
            "created_at": None,
            "updated_at": None,
        }
        defaults.update(overrides)
        for k, v in defaults.items():
            setattr(m, k, v)
        return m

    def _make_symbol_model(self, node_id, name, qname, kind, file_path,
                           language="Python", start=0, end=0,
                           parent_id=None, sig=None, doc=None, meta=None):
        m = MagicMock(spec=[
            "symbol_id", "name", "qualified_name", "kind",
            "file_path", "language", "start_line", "end_line",
            "parent_symbol_id", "signature", "docstring", "metadata_json",
        ])
        m.symbol_id = node_id
        m.name = name
        m.qualified_name = qname
        m.kind = kind
        m.file_path = file_path
        m.language = language
        m.start_line = start
        m.end_line = end
        m.parent_symbol_id = parent_id
        m.signature = sig
        m.docstring = doc
        m.metadata_json = meta or {}
        return m

    def _make_rel_model(self, src, tgt, rel, conf="exact",
                        lines=None, detail=None, weight=1.0, meta=None):
        m = MagicMock(spec=[
            "source_symbol_id", "target_symbol_id", "relationship",
            "confidence", "source_lines", "resolution_detail",
            "weight", "metadata_json",
        ])
        m.source_symbol_id = src
        m.target_symbol_id = tgt
        m.relationship = rel
        m.confidence = conf
        m.source_lines = lines
        m.resolution_detail = detail
        m.weight = weight
        m.metadata_json = meta or {}
        return m

    async def test_load_graph_by_index_id(self, mock_store):
        store, session = mock_store

        sid = lambda f, n: make_symbol_id(f, n)

        mocks = [
            self._make_symbol_model(
                sid("s.py", "s"), "s.py", "s", "file", "s.py"),
            self._make_symbol_model(
                sid("s.py", "s.Foo"), "Foo", "s.Foo", "class", "s.py",
                start=10, end=50),
            self._make_symbol_model(
                sid("s.py", "s.Foo.bar"), "bar", "s.Foo.bar", "method", "s.py",
                start=15, end=30, parent_id=sid("s.py", "s.Foo")),
        ]
        rels = [
            self._make_rel_model(
                sid("s.py", "s"), sid("s.py", "s.Foo"), "contains"),
            self._make_rel_model(
                sid("s.py", "s.Foo"), sid("s.py", "s.Foo.bar"), "contains"),
        ]

        session.execute.side_effect = [
            _make_result(value=self._make_index_model()),
            _make_result(scalars_list=mocks),
            _make_result(scalars_list=rels),
        ]

        result = await store.load_graph(index_id="idx_test_001")

        assert result is not None
        assert result["index"]["index_id"] == "idx_test_001"
        assert result["index"]["repository_id"] == "test-repo"

        graph = result["graph"]
        assert graph.node_count() == 3
        assert graph.edge_count() == 2

    async def test_load_graph_by_repository_id(self, mock_store):
        store, session = mock_store

        session.execute.side_effect = [
            _make_result(value=self._make_index_model(index_id="idx_latest")),
            _make_result(scalars_list=[]),
            _make_result(scalars_list=[]),
        ]

        result = await store.load_graph(repository_id="test-repo")
        assert result is not None
        assert result["index"]["index_id"] == "idx_latest"

    async def test_load_graph_not_found(self, mock_store):
        store, session = mock_store
        session.execute.return_value = _make_result(value=None)

        result = await store.load_graph(index_id="nonexistent")
        assert result is None

    async def test_load_graph_no_args(self, mock_store):
        store, _ = mock_store
        result = await store.load_graph()
        assert result is None

    async def test_load_graph_stale_not_found(self, mock_store):
        store, session = mock_store
        session.execute.return_value = _make_result(value=None)

        result = await store.load_graph(index_id="idx_stale")
        assert result is None

    async def test_load_graph_empty_graph(self, mock_store):
        store, session = mock_store

        session.execute.side_effect = [
            _make_result(value=self._make_index_model()),
            _make_result(scalars_list=[]),
            _make_result(scalars_list=[]),
        ]

        result = await store.load_graph(index_id="idx_empty")
        assert result is not None
        graph = result["graph"]
        assert graph.node_count() == 0
        assert graph.edge_count() == 0


# ═══════════════════════════════════════════════════════════════
# delete_graph Tests
# ═══════════════════════════════════════════════════════════════


class TestDeleteGraph:

    async def test_delete_graph_existing(self, mock_store):
        store, session = mock_store
        # delete_graph calls execute 3 times (SELECT, DELETE symbols, DELETE rels)
        session.execute.return_value = _make_result(value=MagicMock())

        result = await store.delete_graph("idx_test_001")
        assert result is True
        assert session.delete.called
        assert session.commit.called

    async def test_delete_graph_not_found(self, mock_store):
        store, session = mock_store
        session.execute.return_value = _make_result(value=None)

        result = await store.delete_graph("nonexistent")
        assert result is False
        assert not session.delete.called


# ═══════════════════════════════════════════════════════════════
# list_graph_indexes Tests
# ═══════════════════════════════════════════════════════════════


class TestListGraphIndexes:

    def _make_index_list_item(self, **overrides):
        m = MagicMock(spec=[
            "index_id", "repository_id", "repository_path",
            "content_fingerprint", "symbol_count", "relationship_count",
            "file_count", "status", "version", "created_at", "updated_at",
        ])
        defaults = {
            "index_id": "idx_001", "repository_id": "test-repo",
            "repository_path": "/tmp/test-repo",
            "content_fingerprint": None, "symbol_count": 5,
            "relationship_count": 4, "file_count": 2,
            "status": "active", "version": "12.0",
            "created_at": None, "updated_at": None,
        }
        defaults.update(overrides)
        for k, v in defaults.items():
            setattr(m, k, v)
        return m

    async def test_list_graph_indexes_all(self, mock_store):
        store, session = mock_store
        session.execute.side_effect = [
            _make_result(scalars_list=[self._make_index_list_item()]),
        ]

        result = await store.list_graph_indexes()
        assert len(result) == 1
        assert result[0]["index_id"] == "idx_001"
        assert result[0]["status"] == "active"

    async def test_list_graph_indexes_filtered(self, mock_store):
        store, session = mock_store
        session.execute.side_effect = [_make_result(scalars_list=[])]

        result = await store.list_graph_indexes(repository_id="other-repo")
        assert len(result) == 0

    async def test_list_graph_indexes_empty(self, mock_store):
        store, session = mock_store
        session.execute.side_effect = [_make_result(scalars_list=[])]

        result = await store.list_graph_indexes()
        assert result == []


# ═══════════════════════════════════════════════════════════════
# Round-trip Tests
# ═══════════════════════════════════════════════════════════════


class TestGraphRoundTrip:

    async def test_save_and_load_round_trip(self, mock_store, sample_graph):
        """Verify save → load preserves symbols, edges, and metadata."""
        store, session = mock_store

        # ── Save ──
        session.execute.return_value = _make_result(value=None)

        save_result = await store.save_graph(
            graph=sample_graph,
            repository_id="test-repo",
            index_id="idx_roundtrip",
            repository_path="/tmp/test-repo",
            file_count=2,
        )

        assert save_result["symbol_count"] == 5
        assert save_result["relationship_count"] == 4

        # ── Build mock models matching the saved graph ──
        nodes = sample_graph.all_nodes()
        mock_symbols = []
        for n in nodes:
            sm = MagicMock(spec=[
                "symbol_id", "name", "qualified_name", "kind",
                "file_path", "language", "start_line", "end_line",
                "parent_symbol_id", "signature", "docstring", "metadata_json",
            ])
            sm.symbol_id = n.id
            sm.name = n.name
            sm.qualified_name = n.qualified_name
            sm.kind = n.kind
            sm.file_path = n.file_path
            sm.language = n.language
            sm.start_line = n.start_line
            sm.end_line = n.end_line
            sm.parent_symbol_id = n.parent_id
            sm.signature = n.signature
            sm.docstring = n.docstring
            sm.metadata_json = n.metadata
            mock_symbols.append(sm)

        graph_dict = sample_graph.to_dict()
        mock_rels = []
        for ed in graph_dict["edges"]:
            rm = MagicMock(spec=[
                "source_symbol_id", "target_symbol_id", "relationship",
                "confidence", "source_lines", "resolution_detail",
                "weight", "metadata_json",
            ])
            rm.source_symbol_id = ed["source_id"]
            rm.target_symbol_id = ed["target_id"]
            rm.relationship = ed["relationship"]
            rm.confidence = ed["confidence"]
            rm.source_lines = ed.get("source_lines")
            rm.resolution_detail = ed.get("resolution_detail")
            rm.weight = ed.get("weight", 1.0)
            rm.metadata_json = ed.get("metadata", {})
            mock_rels.append(rm)

        # ── Load ──
        idx = MagicMock(spec=[
            "index_id", "repository_id", "repository_path",
            "content_fingerprint", "language_coverage",
            "symbol_count", "relationship_count", "file_count",
            "status", "version", "created_at", "updated_at",
        ])
        idx.index_id = "idx_roundtrip"
        idx.repository_id = "test-repo"
        idx.repository_path = "/tmp/test-repo"
        idx.content_fingerprint = None
        idx.language_coverage = None
        idx.symbol_count = 5
        idx.relationship_count = 4
        idx.file_count = 2
        idx.status = "active"
        idx.version = "12.0"
        idx.created_at = None
        idx.updated_at = None

        session.execute = AsyncMock(side_effect=[
            _make_result(value=idx),
            _make_result(scalars_list=mock_symbols),
            _make_result(scalars_list=mock_rels),
        ])

        load_result = await store.load_graph(index_id="idx_roundtrip")

        assert load_result is not None
        loaded_graph = load_result["graph"]
        assert loaded_graph.node_count() == 5
        assert loaded_graph.edge_count() == 4

        auth_service = loaded_graph.get_node(
            "app/service.py::app.service.AuthService"
        )
        assert auth_service is not None
        assert auth_service.kind == "class"
        assert auth_service.start_line == 10
        assert auth_service.end_line == 50

        auth_service_edges = loaded_graph.get_edges(
            "app/service.py::app.service.AuthService"
        )
        target_ids = {e.target_id for e in auth_service_edges}
        assert "app/service.py::app.service.AuthService.login" in target_ids
        assert "app/models.py::app.models.User" in target_ids

        assert load_result["index"]["index_id"] == "idx_roundtrip"
        assert load_result["index"]["symbol_count"] == 5
        assert load_result["index"]["relationship_count"] == 4
