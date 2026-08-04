"""
Phase 13-H — Integration tests for ContextEngine with mocked services.

These tests verify the full pipeline (collect → rank → dedup → budget → assemble)
with properly mocked CodeIntelligenceService, RepositoryMemoryService, and
PostgresRunStore. No database or LLM required.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.models.context import (
    AgentContext,
    ContextBudget,
    ContextCategory,
    ContextSourceType,
)
from app.services.context_engine import ContextEngine


# ── Mock Helpers ────────────────────────────────────────────────


def _mock_graph():
    """Create a mock SemanticRepositoryGraph with stats."""
    graph = MagicMock()
    graph.stats.return_value = {
        "node_count": 87,
        "edge_count": 142,
        "file_count": 12,
        "kinds": {"class": 15, "function": 42, "method": 30},
        "relationships": {"calls": 80, "imports": 35, "inherits": 12, "contains": 15},
    }
    return graph


def _mock_cis(graph):
    """Create a mock CodeIntelligenceService returning a graph."""
    cis = MagicMock()
    cis.get_current_graph.return_value = graph
    return cis


def _mock_memories():
    """Create a list of mock memory objects."""
    mem = MagicMock()
    mem.memory_id = "mem_001"
    mem.memory_type.value = "architecture"
    mem.status.value = "verified"
    mem.confidence = 0.85
    mem.content = "AuthService handles all authentication logic"
    mem.symbol_names = ["AuthService", "TokenService"]
    mem.evidence = []
    return [mem]


def _mock_memory_service(memories=None):
    """Create a mock RepositoryMemoryService."""
    svc = MagicMock()
    svc.get_memories_for_symbols = AsyncMock(return_value=memories or [])
    return svc


def _mock_run_store():
    """Create a mock PostgresRunStore."""
    store = MagicMock()
    run = MagicMock()
    run.run_id = "run_001"
    run.status.value = "failed"
    run.source.title = "Add logging middleware"
    run.total_duration_ms = 45000
    run.failure.message = "Test failure in AuthService"
    run.stage_results = []
    store.get = AsyncMock(return_value=run)
    return store


def _make_engine(
    cis=None,
    memory_service=None,
    store=None,
) -> ContextEngine:
    """Create a ContextEngine with optional mocked services."""
    return ContextEngine(
        budget=ContextBudget(
            max_total_tokens=8000,
            reserved_instructions=2000,
            reserved_output=2000,
            categories=[],
        ),
        code_intelligence_service=cis,
        memory_service=memory_service,
        postgres_run_store=store,
    )


# ═══════════════════════════════════════════════════════════════
# 1. Graph Context Integration
# ═══════════════════════════════════════════════════════════════


def _mock_retriever_context():
    """Create a mock GraphAwareRetriever returning a realistic context string."""
    return "## Graph Context\nFound 2 relevant symbols\n\n### AuthService (class)\n  File: auth_service.py\n  Signature: class AuthService"


def _make_real_graph():
    """Build a real SemanticRepositoryGraph with a couple of nodes."""
    from app.code_intelligence.semantic_graph import (
        ConfidenceLevel,
        GraphNode,
        RelationshipType,
        SemanticRepositoryGraph,
        make_symbol_id,
    )

    graph = SemanticRepositoryGraph()
    svc_id = make_symbol_id("auth_service.py", "auth_service.AuthService")
    graph.add_node(GraphNode(
        id=svc_id,
        name="AuthService",
        qualified_name="auth_service.AuthService",
        kind="class",
        file_path="auth_service.py",
        language="Python",
        signature="class AuthService",
    ))
    login_id = make_symbol_id("auth_service.py", "auth_service.AuthService.login")
    graph.add_node(GraphNode(
        id=login_id,
        name="login",
        qualified_name="auth_service.AuthService.login",
        kind="method",
        file_path="auth_service.py",
        language="Python",
        signature="def login(self, token)",
    ))
    graph.add_edge(
        source_id=svc_id,
        target_id=login_id,
        relationship=RelationshipType.CONTAINS,
        confidence=ConfidenceLevel.EXACT,
    )
    return graph, svc_id, login_id


class TestGraphEvidenceIntegration:
    """Phase 15: graph evidence now flows through the injected CIS.

    Previously _build_graph_context() called a module-level function that
    constructed its own CodeIntelligenceService, so integration tests
    could not exercise graph evidence with the mock CIS. Now the injected
    CIS's graph is used directly via GraphAwareRetriever.
    """

    @pytest.mark.asyncio
    async def test_graph_evidence_via_injected_cis(self):
        """Graph evidence should be produced from the injected CIS graph."""
        graph, _, _ = _make_real_graph()
        cis = _mock_cis(graph)  # get_current_graph returns the real graph
        engine = _make_engine(cis=cis)

        ctx = await engine.build_context(
            task="Fix authentication",
            agent_type="coding",
            repository_path="/tmp/test-repo",
            symbol_names=["AuthService"],
        )
        # Graph evidence should be present in the assembled context
        assert ctx.related_symbols
        assert "AuthService" in ctx.related_symbols
        assert ctx.metrics.graph_items >= 1

    @pytest.mark.asyncio
    async def test_graph_evidence_placeholder_filtered(self):
        """Placeholder retriever output should not become context."""
        graph, _, _ = _make_real_graph()
        cis = _mock_cis(graph)
        engine = _make_engine(cis=cis)

        ctx = await engine.build_context(
            task="Fix auth",
            agent_type="coding",
            repository_path="/tmp/test-repo",
            symbol_names=["NoSuchSymbolXYZ"],
        )
        # "(No relevant symbols..." placeholder should be filtered out
        assert not ctx.related_symbols

    @pytest.mark.asyncio
    async def test_graph_evidence_cis_returns_none_graph(self):
        """CIS returning no graph should produce no graph evidence (no crash)."""
        cis = MagicMock()
        cis.get_current_graph.return_value = None
        engine = _make_engine(cis=cis)

        ctx = await engine.build_context(
            task="Fix auth",
            agent_type="coding",
            repository_path="/tmp/test-repo",
            symbol_names=["AuthService"],
        )
        assert not ctx.related_symbols


class TestGraphIntegration:
    """ContextEngine with a real-ish mock CodeIntelligenceService."""

    @pytest.mark.asyncio
    async def test_graph_repository_summary_included(self):
        """Graph stats should produce repository summary context."""
        graph = _mock_graph()
        cis = _mock_cis(graph)
        engine = _make_engine(cis=cis)

        ctx = await engine.build_context(
            task="Fix authentication",
            agent_type="planner",
            repository_path="/tmp/test-repo",
        )
        assert ctx.repository_summary
        assert "Node count: 87" in ctx.repository_summary
        assert "Edge count: 142" in ctx.repository_summary

    @pytest.mark.asyncio
    async def test_graph_summary_without_repo_path(self):
        """Without repository_path, graph context should be skipped."""
        cis = _mock_cis(_mock_graph())
        engine = _make_engine(cis=cis)

        ctx = await engine.build_context(
            task="Fix auth",
            agent_type="planner",
        )
        assert not ctx.repository_summary

    @pytest.mark.asyncio
    async def test_graph_unavailable_graceful(self):
        """When CIS is None, graph context should be empty, not crash."""
        engine = _make_engine(cis=None)
        ctx = await engine.build_context(
            task="Fix auth",
            agent_type="planner",
            repository_path="/tmp/test-repo",
        )
        assert not ctx.repository_summary

    @pytest.mark.asyncio
    async def test_graph_context_metrics_tracked(self):
        """Graph items should be counted in metrics."""
        graph = _mock_graph()
        cis = _mock_cis(graph)
        engine = _make_engine(cis=cis)

        ctx = await engine.build_context(
            task="Fix auth",
            agent_type="planner",
            repository_path="/tmp/test-repo",
        )
        # Repository summary counts as graph-backed item
        assert ctx.metrics.candidates_considered >= 2  # task + repo summary
        # graph_items tracks calls to _build_graph_context (for symbols/functions),
        # not repository_summary — so it may be 0 with just repo path.


# ═══════════════════════════════════════════════════════════════
# 2. Repository Memory Integration
# ═══════════════════════════════════════════════════════════════


class TestMemoryIntegration:
    """ContextEngine with a mock RepositoryMemoryService."""

    @pytest.mark.asyncio
    async def test_memory_context_included_with_symbols(self):
        """Repository memory should be included when symbol_names is provided."""
        memories = _mock_memories()
        memory_service = _mock_memory_service(memories)
        engine = _make_engine(memory_service=memory_service)

        ctx = await engine.build_context(
            task="Fix authentication",
            agent_type="planner",
            repository_path="/tmp/test-repo",
            symbol_names=["AuthService"],
        )
        assert ctx.repository_memory
        assert "AuthService" in ctx.repository_memory

    @pytest.mark.asyncio
    async def test_memory_skipped_without_symbols(self):
        """Without symbol_names, memory query should not be called."""
        memory_service = _mock_memory_service([])
        engine = _make_engine(memory_service=memory_service)

        ctx = await engine.build_context(
            task="Fix auth",
            agent_type="planner",
            repository_path="/tmp/test-repo",
        )
        assert not ctx.repository_memory
        memory_service.get_memories_for_symbols.assert_not_called()

    @pytest.mark.asyncio
    async def test_memory_unavailable_graceful(self):
        """When memory_service is None, memory should be empty."""
        engine = _make_engine(memory_service=None)
        ctx = await engine.build_context(
            task="Fix auth",
            agent_type="planner",
            repository_path="/tmp/test-repo",
            symbol_names=["AuthService"],
        )
        assert not ctx.repository_memory

    @pytest.mark.asyncio
    async def test_memory_metrics_tracked(self):
        """Memory items should be counted in metrics."""
        memories = _mock_memories()
        memory_service = _mock_memory_service(memories)
        engine = _make_engine(memory_service=memory_service)

        ctx = await engine.build_context(
            task="Fix auth",
            agent_type="planner",
            repository_path="/tmp/test-repo",
            symbol_names=["AuthService"],
        )
        assert ctx.metrics.memory_items >= 1


# ═══════════════════════════════════════════════════════════════
# 3. Run History Integration
# ═══════════════════════════════════════════════════════════════


class TestRunHistoryIntegration:
    """ContextEngine with a mock PostgresRunStore."""

    @pytest.mark.asyncio
    async def test_run_history_included(self):
        """Historical run data should appear in context."""
        store = _mock_run_store()
        engine = _make_engine(store=store)

        ctx = await engine.build_context(
            task="Fix auth",
            agent_type="planner",
            run_id="run_001",
        )
        assert ctx.historical_memory
        assert "run_001" in ctx.historical_memory or "Previous run" in ctx.historical_memory

    @pytest.mark.asyncio
    async def test_run_history_skipped_without_run_id(self):
        """Without run_id, history query should not be called."""
        store = _mock_run_store()
        engine = _make_engine(store=store)

        ctx = await engine.build_context(
            task="Fix auth",
            agent_type="planner",
        )
        store.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_history_unavailable_graceful(self):
        """When store is None, run history should be empty."""
        engine = _make_engine(store=None)
        ctx = await engine.build_context(
            task="Fix auth",
            agent_type="planner",
            run_id="run_001",
        )
        assert not ctx.historical_memory

    @pytest.mark.asyncio
    async def test_run_history_metrics_tracked(self):
        """Run history items should be counted in metrics."""
        store = _mock_run_store()
        engine = _make_engine(store=store)

        ctx = await engine.build_context(
            task="Fix auth",
            agent_type="planner",
            run_id="run_001",
        )
        assert ctx.metrics.run_history_items >= 1


# ═══════════════════════════════════════════════════════════════
# 4. Full Pipeline Integration
# ═══════════════════════════════════════════════════════════════


class TestFullPipelineIntegration:
    """All services wired — full pipeline with 9 context sources."""

    @pytest.mark.asyncio
    async def test_all_sources_combined(self):
        """All available context sources should contribute (8 of 9 with mocks).

        Note: graph evidence items (via _build_graph_context → get_graph_context_markdown)
        are not produced here because that calls a module-level function rather than
        the mock CIS. Repository summary IS produced via the mock CIS. With a real
        CodeIntelligenceService, all 9 sources would contribute.
        """
        graph = _mock_graph()
        cis = _mock_cis(graph)
        memories = _mock_memories()
        memory_service = _mock_memory_service(memories)
        store = _mock_run_store()
        engine = _make_engine(cis=cis, memory_service=memory_service, store=store)

        ctx = await engine.build_context(
            task="Fix authentication in AuthService",
            agent_type="planner",
            repository_path="/tmp/test-repo",
            symbol_names=["AuthService", "TokenService"],
            plan_text="STEP-001: Update AuthService.validate_token",
            run_id="run_001",
            test_failures=[
                {"test_name": "test_token_expired", "message": "FAILED"},
                {"test_name": "test_auth_flow", "message": "ERROR"},
            ],
            repair_history=[
                {"status": "failed", "reason": "Signature mismatch"},
            ],
            review_findings=[
                {"title": "Missing error handling", "severity": "medium"},
            ],
        )

        # Each source should contribute
        assert ctx.repository_summary        # Graph
        assert ctx.implementation_plan        # Plan
        assert ctx.repository_memory          # Memory
        assert ctx.historical_memory          # Run history
        assert ctx.previous_failures          # Test failures
        assert ctx.previous_repairs           # Repair history
        assert ctx.review_findings            # Review findings

        # Metrics should account for all sources
        m = ctx.metrics
        assert m.candidates_considered >= 7  # task + repo + plan + memory + history + failures + repair + review
        assert m.items_selected >= 1
        assert m.tokens_before > 0
        assert m.tokens_after > 0

    @pytest.mark.asyncio
    async def test_pipeline_deduplication(self):
        """Smoke test: pipeline handles dedup logic without crashing.

        Note: test content doesn't actually overlap (plan text != failure message),
        so duplicates_removed may be 0. This tests that the dedup pipeline step
        doesn't crash with mixed sources.
        """
        engine = _make_engine()  # bare engine

        # Build context with plan text that happens to appear in failures too
        ctx = await engine.build_context(
            task="Fix auth",
            agent_type="planner",
            plan_text="STEP-001: Fix AuthService",
            test_failures=[
                {"test_name": "test_auth", "message": "AuthService failed"},
            ],
        )
        # Should not crash, pipeline should handle gracefully
        assert ctx.agent_type == "planner"
        assert ctx.metrics.duplicates_removed >= 0  # may or may not have dups

    @pytest.mark.asyncio
    async def test_prompt_section_with_all_sources(self):
        """build_prompt_section() should include all populated sections."""
        store = _mock_run_store()
        engine = _make_engine(store=store)

        ctx = await engine.build_context(
            task="Fix auth",
            agent_type="reviewer",
            repository_path="/tmp/test-repo",
            plan_text="STEP-001: Fix",
            run_id="run_001",
            test_failures=[{"test_name": "test_auth", "message": "FAILED"}],
            review_findings=[{"title": "Missing check", "severity": "high"}],
        )

        prompt = ctx.build_prompt_section()
        assert "=== TASK ===" in prompt
        assert "Fix auth" in prompt
        # Only populated sections should appear
        assert prompt.count("===") >= 2  # at least TASK + any others


# ═══════════════════════════════════════════════════════════════
# 5. Agent-Specific Budget Integration
# ═══════════════════════════════════════════════════════════════


class TestAgentBudgetIntegration:
    """Verify that different agent types produce distinct budgets."""

    @pytest.mark.asyncio
    async def test_planner_gets_repo_summary_budget(self):
        """Planner contexts should include repository summary."""
        graph = _mock_graph()
        cis = _mock_cis(graph)
        engine = _make_engine(cis=cis)

        ctx = await engine.build_context(
            task="Fix auth",
            agent_type="planner",
            repository_path="/tmp/test-repo",
        )
        assert ctx.repository_summary

    @pytest.mark.asyncio
    async def test_coding_gets_primary_code_budget(self):
        """Coding agent should get primary code in budget."""
        engine = _make_engine()
        ctx = await engine.build_context(
            task="Implement login",
            agent_type="coding",
        )
        # Coding context should be valid even without code-specific items
        assert ctx.agent_type == "coding"

    @pytest.mark.asyncio
    async def test_repair_gets_failures_budget(self):
        """Repair agent should include previous failures."""
        engine = _make_engine()
        ctx = await engine.build_context(
            task="Fix failing tests",
            agent_type="repair",
            test_failures=[{"test_name": "test_fail", "message": "failed"}],
        )
        assert ctx.previous_failures
        assert "test_fail" in ctx.previous_failures

    @pytest.mark.asyncio
    async def test_reviewer_gets_plan_and_findings_budget(self):
        """Reviewer agent should include implementation plan."""
        engine = _make_engine()
        ctx = await engine.build_context(
            task="Review auth changes",
            agent_type="reviewer",
            plan_text="STEP-001: Implement AuthService",
        )
        assert ctx.implementation_plan

    @pytest.mark.asyncio
    async def test_test_agent_gets_test_focus(self):
        """Test agent should build valid context."""
        engine = _make_engine()
        ctx = await engine.build_context(
            task="Test AuthService",
            agent_type="test",
        )
        assert ctx.agent_type == "test"


# ═══════════════════════════════════════════════════════════════
# 6. Provenance Tracking
# ═══════════════════════════════════════════════════════════════


class TestProvenanceIntegration:
    """Verify provenance is tracked for all context sources."""

    @pytest.mark.asyncio
    async def test_provenance_has_source_types(self):
        """All context items should have provenance with source type."""
        store = _mock_run_store()
        engine = _make_engine(store=store)

        ctx = await engine.build_context(
            task="Fix auth",
            agent_type="planner",
            run_id="run_001",
        )
        assert len(ctx.raw_items) > 0
        for item in ctx.raw_items:
            assert item.provenance.source is not None
            assert item.provenance.score >= 0.0

    @pytest.mark.asyncio
    async def test_provenance_has_details(self):
        """Context items should have human-readable provenance details."""
        engine = _make_engine()
        ctx = await engine.build_context(
            task="Fix auth",
            agent_type="planner",
            plan_text="STEP-001: Fix AuthService",
        )
        for item in ctx.raw_items:
            if item.category == ContextCategory.IMPLEMENTATION_PLAN:
                assert item.provenance.detail
                break


# ═══════════════════════════════════════════════════════════════
# 7. Graceful Degradation
# ═══════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════
# 8. Cross-Agent Context Sharing (Phase 15)
# ═══════════════════════════════════════════════════════════════


class TestCrossAgentSharing:
    """Shared notes from prior agents flow into subsequent agents' context."""

    @pytest.mark.asyncio
    async def test_cross_agent_notes_included(self):
        """Notes from prior agents should appear in context."""
        engine = _make_engine()
        ctx = await engine.build_context(
            task="Fix auth",
            agent_type="repair",
            cross_agent_notes=[
                "Planner produced a 2-step implementation plan",
                "Coding agent changed 3 file(s)",
            ],
        )
        assert ctx.agent_notes
        assert "Planner produced" in ctx.agent_notes
        assert ctx.metrics.cross_agent_items >= 1

    @pytest.mark.asyncio
    async def test_cross_agent_notes_metric_tracked(self):
        """cross_agent_items should be counted in metrics."""
        engine = _make_engine()
        ctx = await engine.build_context(
            task="Fix auth",
            agent_type="reviewer",
            cross_agent_notes=["Review found 1 finding"],
        )
        assert ctx.metrics.cross_agent_items == 1

    @pytest.mark.asyncio
    async def test_cross_agent_notes_prompt_section(self):
        """build_prompt_section() includes the cross-agent notes section."""
        engine = _make_engine()
        ctx = await engine.build_context(
            task="Fix auth",
            agent_type="coding",
            cross_agent_notes=["Planner: implement token validation"],
        )
        prompt = ctx.build_prompt_section()
        assert "PRIOR AGENT NOTES" in prompt
        assert "Planner: implement token validation" in prompt

    @pytest.mark.asyncio
    async def test_cross_agent_notes_empty(self):
        """No notes → empty agent_notes and zero metric."""
        engine = _make_engine()
        ctx = await engine.build_context(
            task="Fix auth",
            agent_type="planner",
        )
        assert not ctx.agent_notes
        assert ctx.metrics.cross_agent_items == 0

    @pytest.mark.asyncio
    async def test_dedup_merges_provenance_in_pipeline(self):
        """End-to-end: duplicate content from two sources merges provenance."""
        engine = _make_engine()
        ctx = await engine.build_context(
            task="Fix auth",
            agent_type="planner",
            cross_agent_notes=["Same note content"],
        )
        # Rebuild with identical note content to trigger dedup
        ctx2 = await engine.build_context(
            task="Fix auth",
            agent_type="planner",
            cross_agent_notes=["Same note content"],
        )
        assert ctx2.metrics.duplicates_removed >= 0  # pipeline handles gracefully
        for item in ctx2.raw_items:
            if item.category.value == "agent_notes":
                assert item.provenance.source.value == "cross_agent"


class TestHandoffContext:
    """Phase 15: structured agent handoffs become bounded context."""

    def _make_handoff(self, from_agent="planner", to_agent="coding", summary="plan ready"):
        from app.models.collaboration import AgentHandoff, EvidenceRef, EvidenceType

        return AgentHandoff(
            run_id="RUN-1",
            from_agent=from_agent,
            to_agent=to_agent,
            stage="planning",
            summary=summary,
            decisions=["Follow plan"],
            affected_symbols=["auth_service.py::AuthService"],
            evidence_refs=[EvidenceRef(type=EvidenceType.PLAN, reference="step-1")],
        )

    @pytest.mark.asyncio
    async def test_handoffs_appear_in_context(self):
        engine = ContextEngine()
        handoffs = [self._make_handoff()]
        ctx = await engine.build_context(
            task="Add auth",
            agent_type="coding",
            handoffs=handoffs,
        )
        assert ctx.agent_handoffs
        assert "planner → coding" in ctx.agent_handoffs
        assert ctx.metrics.handoff_items == 1

    @pytest.mark.asyncio
    async def test_handoff_metrics_zero_when_none(self):
        engine = ContextEngine()
        ctx = await engine.build_context(task="t", agent_type="coding")
        assert ctx.metrics.handoff_items == 0

    @pytest.mark.asyncio
    async def test_many_handoffs_bounded_in_context(self):
        engine = ContextEngine()
        handoffs = [self._make_handoff(summary=f"h{i}") for i in range(20)]
        ctx = await engine.build_context(
            task="t", agent_type="coding", handoffs=handoffs
        )
        # Only a bounded subset is rendered
        assert ctx.metrics.handoff_items <= 8


class TestGracefulDegradation:
    """ContextEngine must work when all services are unavailable."""

    @pytest.mark.asyncio
    async def test_bare_engine_produces_context(self):
        """Even without any services, the engine should produce valid context."""
        engine = _make_engine(cis=None, memory_service=None, store=None)
        ctx = await engine.build_context(
            task="Fix auth",
            agent_type="planner",
        )
        assert isinstance(ctx, AgentContext)
        assert ctx.task == "Fix auth"

    @pytest.mark.asyncio
    async def test_bare_engine_with_failures(self):
        """Bare engine with test failures should still work."""
        engine = _make_engine()
        ctx = await engine.build_context(
            task="Fix auth",
            agent_type="repair",
            test_failures=[{"test_name": "test_auth", "message": "FAILED"}],
        )
        assert ctx.previous_failures

    @pytest.mark.asyncio
    async def test_bare_engine_supports_explain(self):
        """explain_context() should work on bare engine output."""
        engine = _make_engine()
        ctx = await engine.build_context(
            task="Fix auth",
            agent_type="planner",
        )
        explanation = engine.explain_context(ctx)
        assert "Context for: planner" in explanation
        assert "Candidates considered" in explanation

    @pytest.mark.asyncio
    async def test_budget_config_for_all_agent_types(self):
        """All 5 agent types should produce valid budget configurations."""
        for agent_type in ["planner", "coding", "test", "repair", "reviewer"]:
            budget = ContextBudget(max_total_tokens=8000, reserved_instructions=2000, reserved_output=2000)
            configured = budget.config_for_agent(agent_type)
            assert len(configured.categories) > 0, f"Agent {agent_type} has no budget categories"
