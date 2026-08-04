"""
Phase 15 (12d) — Tests for Impact-Driven Test Selection.

Verifies TestSelectionService selects tests covering changed code via
the semantic graph's impact analysis, and that TestingService falls
back to heuristics when no selector is configured.
"""

from __future__ import annotations

from app.code_intelligence.semantic_graph import (
    ConfidenceLevel,
    GraphNode,
    RelationshipType,
    SemanticRepositoryGraph,
    make_symbol_id,
)
from app.services.test_selection_service import TestSelectionService


def _build_graph_with_tests() -> SemanticRepositoryGraph:
    """Build a graph: AuthService ← tested by test_auth, called by controller."""
    graph = SemanticRepositoryGraph()

    svc_id = make_symbol_id("auth_service.py", "auth_service.AuthService")
    graph.add_node(GraphNode(
        id=svc_id, name="AuthService", qualified_name="auth_service.AuthService",
        kind="class", file_path="auth_service.py", language="Python",
    ))
    login_id = make_symbol_id("auth_service.py", "auth_service.AuthService.login")
    graph.add_node(GraphNode(
        id=login_id, name="login", qualified_name="auth_service.AuthService.login",
        kind="method", file_path="auth_service.py", language="Python",
    ))
    graph.add_edge(svc_id, login_id, RelationshipType.CONTAINS, ConfidenceLevel.EXACT)

    # Test file with TESTS edge pointing at AuthService
    test_id = make_symbol_id("test_auth.py", "test_auth.TestAuthService")
    graph.add_node(GraphNode(
        id=test_id, name="TestAuthService", qualified_name="test_auth.TestAuthService",
        kind="test_class", file_path="test_auth.py", language="Python",
    ))
    graph.add_edge(test_id, svc_id, RelationshipType.TESTS, ConfidenceLevel.EXACT)

    # Controller depends on AuthService (impact propagates)
    ctrl_id = make_symbol_id("auth_controller.py", "auth_controller.AuthController")
    graph.add_node(GraphNode(
        id=ctrl_id, name="AuthController", qualified_name="auth_controller.AuthController",
        kind="class", file_path="auth_controller.py", language="Python",
    ))
    graph.add_edge(ctrl_id, svc_id, RelationshipType.DEPENDS_ON, ConfidenceLevel.HIGH)

    return graph


class TestSelectForChangedFiles:
    """Core selection logic."""

    def test_selects_tests_for_changed_file(self):
        svc = TestSelectionService(graph=_build_graph_with_tests())
        result = svc.select_for_changed_files(["auth_service.py"])
        assert result.file_paths, "Expected at least one test selected"
        assert "test_auth.py" in result.file_paths
        assert result.root_symbol_count >= 1
        assert result.impacted_files

    def test_no_graph_graceful(self):
        svc = TestSelectionService(graph=None)
        result = svc.select_for_changed_files(["auth_service.py"])
        assert result.selected_tests == []
        assert result.warning == "No graph loaded — test selection unavailable"

    def test_no_changed_files(self):
        svc = TestSelectionService(graph=_build_graph_with_tests())
        result = svc.select_for_changed_files([])
        assert result.selected_tests == []
        assert result.file_paths == []

    def test_changed_file_without_tests(self):
        graph = SemanticRepositoryGraph()
        svc = TestSelectionService(graph=graph)
        result = svc.select_for_changed_files(["unrelated.py"])
        assert result.selected_tests == []

    def test_summary(self):
        svc = TestSelectionService(graph=_build_graph_with_tests())
        result = svc.select_for_changed_files(["auth_service.py"])
        summary = svc.summarize(result)
        assert "Test selection:" in summary
        assert "test_auth.py" in summary


class TestFilePaths:
    """Unique file path extraction."""

    def test_file_paths_dedup(self):
        from app.services.test_selection_service import TestSelection

        result = _empty_result()
        result.selected_tests = [
            TestSelection(file_path="a.py", reason="1", distance=1),
            TestSelection(file_path="a.py", reason="2", distance=2),
            TestSelection(file_path="b.py", reason="3", distance=1),
        ]
        assert result.file_paths == ["a.py", "b.py"]


def _empty_result():
    from app.services.test_selection_service import TestSelectionResult

    return TestSelectionResult()


class TestTestingServiceIntegration:
    """TestingService uses the selector when provided, else heuristics."""

    def test_selector_preferred_over_heuristics(self):
        from app.services.testing_service import TestingService

        selector = TestSelectionService(graph=_build_graph_with_tests())
        service = TestingService(test_selector=selector)
        files = service.select_tests_for_changes(
            workspace_root="/tmp/repo",
            changed_files=["auth_service.py"],
        )
        assert "test_auth.py" in files

    def test_heuristic_fallback_without_selector(self):
        from app.services.testing_service import TestingService

        service = TestingService()  # no selector → heuristic path
        # Non-existent workspace → no heuristic matches
        files = service.select_tests_for_changes(
            workspace_root="/tmp/does-not-exist-xyz",
            changed_files=["auth_service.py"],
        )
        assert isinstance(files, list)
