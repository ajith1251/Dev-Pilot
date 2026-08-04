"""
Phase 15 (12d) — Tests for Cross-File Symbol Resolution.

Verifies that import nodes are linked to their actual definitions
across file boundaries with REFERENCES edges, and that the resolver
degrades gracefully on empty/partial graphs.
"""

from __future__ import annotations

from app.code_intelligence.semantic_graph import (
    ConfidenceLevel,
    GraphNode,
    RelationshipType,
    SemanticRepositoryGraph,
    make_symbol_id,
    normalize_qualified_name,
)
from app.code_intelligence.symbol_resolver import CrossFileSymbolResolver


def _build_two_file_graph() -> SemanticRepositoryGraph:
    """Graph: auth_service.py imports UserRepository from users/repository.py."""
    graph = SemanticRepositoryGraph()

    # users/repository.py defines UserRepository
    repo_file_id = make_symbol_id("users/repository.py", "users.repository")
    graph.add_node(GraphNode(
        id=repo_file_id, name="repository.py", qualified_name="users.repository",
        kind="module", file_path="users/repository.py", language="Python",
    ))
    user_repo_id = make_symbol_id("users/repository.py", "users.repository.UserRepository")
    graph.add_node(GraphNode(
        id=user_repo_id, name="UserRepository",
        qualified_name="users.repository.UserRepository",
        kind="class", file_path="users/repository.py", language="Python",
    ))
    graph.add_edge(repo_file_id, user_repo_id, RelationshipType.CONTAINS, ConfidenceLevel.EXACT)

    # auth_service.py imports it
    auth_file_id = make_symbol_id("auth_service.py", "auth_service")
    graph.add_node(GraphNode(
        id=auth_file_id, name="auth_service.py", qualified_name="auth_service",
        kind="module", file_path="auth_service.py", language="Python",
    ))
    import_id = make_symbol_id("auth_service.py", "auth_service.imports.users.repository.UserRepository")
    graph.add_node(GraphNode(
        id=import_id, name="UserRepository", qualified_name=import_id,
        kind="import", file_path="auth_service.py", language="Python",
        signature="from users.repository import UserRepository",
        metadata={"module": "users.repository", "name": "UserRepository", "as_name": None},
    ))
    graph.add_edge(auth_file_id, import_id, RelationshipType.IMPORTS, ConfidenceLevel.EXACT)

    return graph


class TestCrossFileResolution:
    """Core resolution behavior."""

    def test_resolves_cross_file_import(self):
        graph = _build_two_file_graph()
        resolver = CrossFileSymbolResolver(graph=graph)
        stats = resolver.resolve()

        assert stats.import_nodes_seen == 1
        assert stats.resolved >= 1
        assert stats.edges_added >= 1

        # The import node should now have a REFERENCES edge to the definition
        import_id = make_symbol_id(
            "auth_service.py",
            "auth_service.imports.users.repository.UserRepository",
        )
        edges = graph.get_edges(import_id)
        ref_edges = [
            e for e in edges
            if e.metadata.relationship == RelationshipType.REFERENCES
        ]
        assert ref_edges, "Expected a REFERENCES edge from import → definition"
        assert ref_edges[0].target_id == make_symbol_id(
            "users/repository.py", "users.repository.UserRepository"
        )
        assert ref_edges[0].metadata.confidence == ConfidenceLevel.EXACT

    def test_no_graph_graceful(self):
        resolver = CrossFileSymbolResolver(graph=None)
        stats = resolver.resolve()
        assert stats.import_nodes_seen == 0
        assert stats.edges_added == 0
        assert stats.warnings

    def test_unresolved_import_counted(self):
        graph = SemanticRepositoryGraph()
        # import with no matching definition anywhere
        import_id = make_symbol_id("x.py", "x.imports.nonexistent.Thing")
        graph.add_node(GraphNode(
            id=import_id, name="Thing", qualified_name=import_id,
            kind="import", file_path="x.py", language="Python",
            signature="from nonexistent import Thing",
            metadata={"module": "nonexistent", "name": "Thing"},
        ))
        resolver = CrossFileSymbolResolver(graph=graph)
        stats = resolver.resolve()
        assert stats.import_nodes_seen == 1
        assert stats.resolved == 0
        assert stats.unresolved == 1

    def test_no_duplicate_edges(self):
        graph = _build_two_file_graph()
        resolver = CrossFileSymbolResolver(graph=graph)
        resolver.resolve()
        resolver.resolve()  # second pass should not add duplicates

        import_id = make_symbol_id(
            "auth_service.py",
            "auth_service.imports.users.repository.UserRepository",
        )
        ref_edges = [
            e for e in graph.get_edges(import_id)
            if e.metadata.relationship == RelationshipType.REFERENCES
        ]
        assert len(ref_edges) == 1


class TestModuleHelper:
    """Module path derivation."""

    def test_module_for_file(self):
        resolver = CrossFileSymbolResolver(graph=SemanticRepositoryGraph())
        assert resolver._module_for_file("users/repository.py") == "users.repository"
        assert resolver._module_for_file("auth_service.py") == "auth_service"
        assert resolver._module_for_file("app/services/auth.py") == "app.services.auth"
        assert resolver._module_for_file("a\\b\\c.py") == "a.b.c"
