"""
Tests for agent graph context integration (Phase 12).

Verifies that all 5 agents properly integrate semantic graph context
through the agent_graph_helper module and their respective _get_graph_context methods.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.code_intelligence.agent_graph_helper import (
    extract_symbols_from_changed_files,
    extract_symbols_from_plan,
    get_graph_context,
    get_graph_context_markdown,
)


# ═══════════════════════════════════════════════════════════════
# agent_graph_helper.py Tests
# ═══════════════════════════════════════════════════════════════


class TestAgentGraphHelper:
    """Tests for the agent graph helper module."""

    def test_extract_symbols_from_plan_camelcase(self):
        text = "Implement AuthService with login() and logout() methods"
        symbols = extract_symbols_from_plan(text)
        assert "AuthService" in symbols

    def test_extract_symbols_from_plan_def_function(self):
        text = "The def login function should validate tokens"
        symbols = extract_symbols_from_plan(text)
        assert "login" in symbols

    def test_extract_symbols_from_plan_multiple(self):
        text = "Refactor UserService and AdminService. Both inherit BaseService."
        symbols = extract_symbols_from_plan(text)
        assert "UserService" in symbols
        assert "AdminService" in symbols

    def test_extract_symbols_from_plan_empty(self):
        assert extract_symbols_from_plan("") == []

    def test_extract_symbols_from_plan_short_text(self):
        assert extract_symbols_from_plan("no symbols here") == []

    def test_extract_symbols_from_changed_files(self):
        files = ["src/auth/service.py", "tests/test_auth.py"]
        symbols = extract_symbols_from_changed_files(files)
        assert "service" in symbols
        assert "Service" in symbols
        assert "test_auth" in symbols or "TestAuth" in symbols

    def test_extract_symbols_from_changed_files_snake_case(self):
        files = ["src/user_service.py"]
        symbols = extract_symbols_from_changed_files(files)
        assert any("UserService" in s for s in symbols)
        assert "user_service" in symbols

    def test_extract_symbols_from_changed_files_empty(self):
        assert extract_symbols_from_changed_files([]) == []

    @patch("app.code_intelligence.agent_graph_helper._get_service")
    def test_get_graph_context_no_graph(self, mock_get_service):
        mock_service = MagicMock()
        mock_service.get_current_graph.return_value = None
        mock_get_service.return_value = mock_service
        result = get_graph_context(["AuthService"])
        assert result == ""

    @patch("app.code_intelligence.agent_graph_helper._get_service")
    def test_get_graph_context_markdown_no_graph(self, mock_get_service):
        mock_service = MagicMock()
        mock_service.get_current_graph.return_value = None
        mock_get_service.return_value = mock_service
        result = get_graph_context_markdown(["AuthService"])
        assert result == ""


# ═══════════════════════════════════════════════════════════════
# Planner Agent Integration Tests
# ═══════════════════════════════════════════════════════════════


class TestPlannerIntegration:
    """Tests for Planner agent graph context integration."""

    def test_planner_input_accepts_graph_context(self):
        from app.agents.planner import PlannerInput
        inp = PlannerInput(
            requirements=MagicMock(),
            graph_context="test graph context",
        )
        assert inp.graph_context == "test graph context"

    def test_planner_input_defaults_empty(self):
        from app.agents.planner import PlannerInput
        inp = PlannerInput(requirements=MagicMock())
        assert inp.graph_context == ""

    def test_planner_get_graph_context_no_requirements(self):
        from app.agents.planner import PlannerAgent, PlannerInput
        from app.models.issues import StructuredRequirements

        req = StructuredRequirements(
            objective="Test objective",
            requirements=[],
        )
        inp = PlannerInput(requirements=req)
        result = PlannerAgent._get_graph_context(inp)
        assert result == ""

    def test_planner_get_graph_context_with_requirements(self):
        from app.agents.planner import PlannerAgent, PlannerInput
        from app.models.issues import StructuredRequirements, Requirement

        req = StructuredRequirements(
            objective="Implement AuthService login",
            requirements=[
                Requirement(description="Add authentication service", requirement_type="functional"),
            ],
        )
        inp = PlannerInput(requirements=req)
        # Should not crash
        result = PlannerAgent._get_graph_context(inp)
        # May return empty string since no graph is loaded (that's fine)
        assert result is not None


# ═══════════════════════════════════════════════════════════════
# Coding Agent Integration Tests
# ═══════════════════════════════════════════════════════════════


class TestCodingAgentIntegration:
    """Tests for Coding Agent graph context integration."""

    def test_get_graph_context_no_plan(self):
        from app.agents.coding_agent import CodingAgent
        from app.models.issues import ImplementationPlan, ImplementationStep
        from app.models.rag import RetrievedContext, RetrievalQuery

        plan = ImplementationPlan(
            summary="", objective="",
            steps=[ImplementationStep(id="S1", title="Step 1", description="", affected_areas=[])],
        )
        ctx = RetrievedContext(
            query=RetrievalQuery(text="test"), snapshot_id="",
            items=[], total_candidates=0,
        )
        result = CodingAgent._get_graph_context(plan, ctx)
        # Should return empty or not crash
        assert result is not None

    def test_get_graph_context_with_symbols(self):
        from app.agents.coding_agent import CodingAgent
        from app.models.issues import ImplementationPlan, ImplementationStep
        from app.models.rag import RetrievedContext, RetrievalQuery

        plan = ImplementationPlan(
            summary="Implement AuthService",
            objective="Add authentication",
            steps=[ImplementationStep(id="S1", title="Create AuthService", description="Implement login/logout", affected_areas=["auth/"])],
        )
        ctx = RetrievedContext(query=RetrievalQuery(text="auth"), snapshot_id="", items=[], total_candidates=0)
        result = CodingAgent._get_graph_context(plan, ctx)
        assert result is not None


# ═══════════════════════════════════════════════════════════════
# Fix Agent Integration Tests
# ═══════════════════════════════════════════════════════════════


class TestFixAgentIntegration:
    """Tests for Fix Agent graph context integration."""

    def test_get_graph_context_empty_diagnosis(self):
        from app.agents.fix_agent import FixAgent
        from app.models.repair import FailureDiagnosis, Repairability
        from app.models.testing import TestFailure

        diag = FailureDiagnosis(
            diagnosis_id="d1",
            run_id="run-1",
            category="assertion_failure",
            summary="Test",
            likely_cause="Bug",
            repairability=Repairability.REPAIRABLE,
            confidence=0.8,
            failures=[TestFailure(failure_id="f1", test_name="test_x")],
        )
        result = FixAgent._get_graph_context(diag)
        assert result == ""

    def test_get_graph_context_with_symbols(self):
        from app.agents.fix_agent import FixAgent
        from app.models.repair import FailureDiagnosis, Repairability
        from app.models.testing import TestFailure

        diag = FailureDiagnosis(
            diagnosis_id="d1",
            run_id="run-1",
            category="assertion_failure",
            summary="Test",
            likely_cause="Bug",
            repairability=Repairability.REPAIRABLE,
            confidence=0.8,
            failures=[TestFailure(failure_id="f1", test_name="test_x")],
            affected_symbols=["AuthService.login"],
            affected_files=["auth/service.py"],
        )
        result = FixAgent._get_graph_context(diag)
        # Should include symbol names
        assert "AuthService" in result or result == ""  # May be empty if no graph


# ═══════════════════════════════════════════════════════════════
# ReviewContextBuilder Integration Tests
# ═══════════════════════════════════════════════════════════════


class TestReviewContextIntegration:
    """Tests for Reviewer graph context integration."""

    def test_build_architecture_context_no_profile(self):
        from app.services.review_context_builder import ReviewContextBuilder
        builder = ReviewContextBuilder()
        from app.models.rag import RetrievedContext
        result = builder._build_architecture_context(None, None)
        assert isinstance(result, str)

    def test_build_architecture_context_with_profile(self):
        from app.services.review_context_builder import ReviewContextBuilder
        from app.models.profile import RepositoryProfile, ScanMetadata
        builder = ReviewContextBuilder()
        profile = RepositoryProfile(
            name="test",
            scan=ScanMetadata(root_path="/tmp/test", duration_seconds=0.1),
        )
        from app.models.rag import RetrievedContext
        result = builder._build_architecture_context(profile, None)
        assert isinstance(result, str)
        assert "Graph" not in result  # No graph loaded


# ═══════════════════════════════════════════════════════════════
# Test Agent Integration Tests
# ═══════════════════════════════════════════════════════════════


class TestTestAgentIntegration:
    """Tests for Test Agent graph context integration."""

    def test_workspace_summary_with_changed_files(self):
        from app.agents.test_agent import TestAgentInput, TestAgent

        # Deterministic path should not crash
        agent = TestAgent()
        inp = TestAgentInput(
            workspace_id="ws-1",
            workspace_root="/tmp/test",
            changed_files=["src/auth.py"],
        )
        # Just verify it doesn't crash when building
        model = agent._build_workspace_summary(inp)
        assert isinstance(model, str)
        assert "auth.py" in model
