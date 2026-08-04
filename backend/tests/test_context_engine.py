"""
Phase 13-B — Unit tests for ContextEngine: ranking, deduplication, token budgeting,
context assembly, and Planner agent integration.

All tests are deterministic — no LLM or database required.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from app.models.context import (
    AgentContext,
    BudgetCategory,
    ContextBudget,
    ContextCategory,
    ContextItem,
    ContextMetrics,
    ContextSourceType,
    Provenance,
)
from app.services.context_engine import ContextEngine


# ── Helpers ────────────────────────────────────────────────────


def _make_item(
    content: str,
    category: ContextCategory = ContextCategory.PRIMARY_CODE,
    score: float = 0.5,
    source: ContextSourceType = ContextSourceType.GRAPH,
    tokens: int = 0,
) -> ContextItem:
    """Quickly create a ContextItem for testing."""
    return ContextItem(
        content=content,
        category=category,
        provenance=Provenance(source=source, score=score),
        estimated_tokens=tokens or len(content) // 4,
    )


def _make_engine() -> ContextEngine:
    """Create a bare ContextEngine (no services attached)."""
    return ContextEngine(
        budget=ContextBudget(
            max_total_tokens=4000,
            reserved_instructions=1000,
            reserved_output=500,
            categories=[],
        )
    )


# ═══════════════════════════════════════════════════════════════
# 1. Ranking Tests
# ═══════════════════════════════════════════════════════════════


class TestRanking:
    """Tests for ContextEngine._rank_candidates()."""

    def test_task_ranked_highest(self):
        """Task context should always rank highest."""
        engine = _make_engine()
        items = [
            _make_item("some code", ContextCategory.PRIMARY_CODE, score=0.5),
            _make_item("the task", ContextCategory.TASK, score=0.3),
        ]
        ranked = engine._rank_candidates(items, "test task", "planner")
        assert ranked[0].category == ContextCategory.TASK
        assert ranked[0].provenance.score >= 1.0

    def test_plan_ranked_above_code(self):
        """Implementation plan should rank above primary code."""
        engine = _make_engine()
        items = [
            _make_item("def foo(): pass", ContextCategory.PRIMARY_CODE, score=0.5),
            _make_item("Step 1: implement", ContextCategory.IMPLEMENTATION_PLAN, score=0.4),
        ]
        ranked = engine._rank_candidates(items, "test", "planner")
        assert ranked[0].category == ContextCategory.IMPLEMENTATION_PLAN
        assert ranked[1].category == ContextCategory.PRIMARY_CODE

    def test_warnings_ranked_lowest(self):
        """Warnings should rank below all substantive categories."""
        engine = _make_engine()
        items = [
            _make_item("some test", ContextCategory.RELATED_TESTS, score=0.5),
            _make_item("warning msg", ContextCategory.WARNINGS, score=0.3),
        ]
        ranked = engine._rank_candidates(items, "test", "planner")
        assert ranked[-1].category == ContextCategory.WARNINGS

    def test_ranking_maintains_order_within_same_score(self):
        """Items with same score should preserve relative order."""
        engine = _make_engine()
        items = [
            _make_item("alpha", ContextCategory.PRIMARY_CODE, score=0.5),
            _make_item("beta", ContextCategory.PRIMARY_CODE, score=0.5),
            _make_item("gamma", ContextCategory.DEPENDENCIES, score=0.5),
        ]
        ranked = engine._rank_candidates(items, "test", "planner")
        # All primary code gets boosted to 0.9, dependencies to 0.8
        assert ranked[0].category == ContextCategory.PRIMARY_CODE
        assert ranked[1].category == ContextCategory.PRIMARY_CODE
        assert ranked[2].category == ContextCategory.DEPENDENCIES

    def test_ranking_empty_list(self):
        """Empty candidate list should return empty."""
        engine = _make_engine()
        ranked = engine._rank_candidates([], "test", "planner")
        assert ranked == []


# ═══════════════════════════════════════════════════════════════
# 2. Deduplication Tests
# ═══════════════════════════════════════════════════════════════


class TestDeduplication:
    """Tests for ContextEngine._deduplicate()."""

    def test_no_duplicates(self):
        """All unique items should pass through."""
        engine = _make_engine()
        items = [
            _make_item("content A"),
            _make_item("content B"),
            _make_item("content C"),
        ]
        deduped, count = engine._deduplicate(items)
        assert len(deduped) == 3
        assert count == 0

    def test_exact_duplicate_removed(self):
        """Exact duplicate content should be deduplicated."""
        engine = _make_engine()
        items = [
            _make_item("same content", score=0.9),
            _make_item("same content", score=0.5),
            _make_item("different", score=0.7),
        ]
        deduped, count = engine._deduplicate(items)
        assert len(deduped) == 2
        assert count == 1

    def test_higher_score_wins_duplicate(self):
        """When duplicates exist, higher-scored item is kept."""
        engine = _make_engine()
        items = [
            _make_item("shared content", score=0.3),
            _make_item("shared content", score=0.9),
        ]
        deduped, count = engine._deduplicate(items)
        assert len(deduped) == 1
        assert deduped[0].provenance.score == 0.9

    def test_multiple_duplicates(self):
        """Multiple duplicate pairs should all be handled."""
        engine = _make_engine()
        items = [
            _make_item("A", score=1.0),
            _make_item("A", score=0.8),  # dup of A
            _make_item("B", score=0.9),
            _make_item("B", score=0.7),  # dup of B
            _make_item("B", score=0.6),  # dup of B
            _make_item("C", score=0.5),
        ]
        deduped, count = engine._deduplicate(items)
        assert len(deduped) == 3
        assert count == 3

    def test_dedup_empty_list(self):
        """Empty list should return empty with 0 duplicates."""
        engine = _make_engine()
        deduped, count = engine._deduplicate([])
        assert deduped == []
        assert count == 0

    def test_dedup_different_category_same_content(self):
        """Items with same content but different categories are still deduplicated."""
        engine = _make_engine()
        items = [
            _make_item("same text", ContextCategory.PRIMARY_CODE, score=0.8),
            _make_item("same text", ContextCategory.DEPENDENCIES, score=0.9),
        ]
        deduped, count = engine._deduplicate(items)
        assert len(deduped) == 1
        assert count == 1

    def test_duplicate_provenance_merged_into_survivor(self):
        """Phase 15: losing duplicate's provenance is merged onto the survivor."""
        engine = _make_engine()
        items = [
            _make_item("shared", score=0.9, source=ContextSourceType.GRAPH),
            _make_item("shared", score=0.6, source=ContextSourceType.VECTOR),
        ]
        deduped, count = engine._deduplicate(items)
        assert count == 1
        assert len(deduped) == 1
        survivor = deduped[0]
        assert survivor.provenance.source == ContextSourceType.GRAPH
        # The vector provenance should be preserved on the survivor
        assert len(survivor.merged_provenances) == 1
        assert survivor.merged_provenances[0].source == ContextSourceType.VECTOR
        assert survivor.merged_provenances[0].score == 0.6

    def test_duplicate_promotion_keeps_merged_provenance(self):
        """Phase 15: when a later higher-scored item wins, earlier provenance is kept."""
        engine = _make_engine()
        items = [
            _make_item("shared", score=0.5, source=ContextSourceType.GRAPH),
            _make_item("shared", score=0.9, source=ContextSourceType.IMPACT),
        ]
        deduped, count = engine._deduplicate(items)
        assert count == 1
        survivor = deduped[0]
        # Higher-scored item wins
        assert survivor.provenance.source == ContextSourceType.IMPACT
        # Lower-scored graph provenance preserved
        assert any(p.source == ContextSourceType.GRAPH for p in survivor.merged_provenances)

    def test_duplicate_chains_merge_all_provenances(self):
        """Phase 15: three-way duplicates merge all losing provenances."""
        engine = _make_engine()
        items = [
            _make_item("chain", score=0.9, source=ContextSourceType.GRAPH),
            _make_item("chain", score=0.7, source=ContextSourceType.VECTOR),
            _make_item("chain", score=0.8, source=ContextSourceType.IMPACT),
        ]
        deduped, count = engine._deduplicate(items)
        assert count == 2
        survivor = deduped[0]
        sources = [p.source for p in survivor.merged_provenances]
        assert ContextSourceType.VECTOR in sources
        assert ContextSourceType.IMPACT in sources
        assert survivor.provenance.source == ContextSourceType.GRAPH


# ═══════════════════════════════════════════════════════════════
# 3. Token Budgeting Tests
# ═══════════════════════════════════════════════════════════════


class TestTokenBudgeting:
    """Tests for ContextEngine._apply_budget()."""

    def test_all_items_fit_in_budget(self):
        """When all items fit, nothing should be dropped."""
        engine = _make_engine()
        budget = ContextBudget(
            max_total_tokens=10000,
            reserved_instructions=1000,
            reserved_output=500,
            categories=[],
        )
        items = [_make_item("short", tokens=50), _make_item("brief", tokens=30)]
        selected = engine._apply_budget(items, budget)
        assert len(selected) == 2

    def test_budget_limits_items(self):
        """When items exceed budget, lower-ranked items are dropped."""
        engine = _make_engine()
        budget = ContextBudget(
            max_total_tokens=2000,
            reserved_instructions=500,
            reserved_output=500,
            categories=[],  # No category limits — pure token cap
        )
        items = [
            _make_item("big chunk " * 100, tokens=500, score=0.9),
            _make_item("big chunk " * 100, tokens=500, score=0.8),
            _make_item("big chunk " * 100, tokens=500, score=0.7),
        ]
        # Available = 2000 - 500 - 500 = 1000, enough for 2 items
        selected = engine._apply_budget(items, budget)
        assert len(selected) <= 2

    def test_empty_budget(self):
        """No available tokens should yield empty selection."""
        engine = _make_engine()
        budget = ContextBudget(
            max_total_tokens=1500,
            reserved_instructions=1000,
            reserved_output=500,  # min is 500
            categories=[],
        )
        items = [_make_item("anything", tokens=100)]
        selected = engine._apply_budget(items, budget)
        assert selected == []

    def test_budget_respects_priority_order(self):
        """Higher-scored items should be selected before lower-scored.

        Uses a budget with per-category allocation large enough for all items.
        """
        engine = _make_engine()
        budget = ContextBudget(
            max_total_tokens=5000,
            reserved_instructions=1000,
            reserved_output=500,
            categories=[
                BudgetCategory(
                    category=ContextCategory.PRIMARY_CODE,
                    percentage=70,
                    max_tokens=4000,
                ),
            ],
        )
        items = [
            _make_item("low", tokens=50, score=0.3),
            _make_item("high", tokens=50, score=0.9),
            _make_item("medium", tokens=50, score=0.6),
        ]
        selected = engine._apply_budget(items, budget)
        # Available = 3500, all 3 items fit, in score order
        assert len(selected) == 3
        assert selected[0].content == "high"
        assert selected[1].content == "medium"
        assert selected[2].content == "low"

    def test_budget_empty_items(self):
        """Empty items list should return empty."""
        engine = _make_engine()
        budget = ContextBudget(max_total_tokens=8000, reserved_instructions=2000, reserved_output=2000)
        selected = engine._apply_budget([], budget)
        assert selected == []


# ═══════════════════════════════════════════════════════════════
# 4. Context Assembly Tests
# ═══════════════════════════════════════════════════════════════


class TestContextAssembly:
    """Tests for ContextEngine._assemble_context()."""

    def test_assemble_empty_items(self):
        """Empty items should produce a valid AgentContext with defaults."""
        engine = _make_engine()
        ctx = engine._assemble_context([], "empty task", "planner")
        assert isinstance(ctx, AgentContext)
        assert ctx.task == "empty task"
        assert ctx.agent_type == "planner"
        assert ctx.raw_items == []

    def test_assemble_task_item_fills_correct_field(self):
        """Task items should set ctx.task."""
        engine = _make_engine()
        items = [_make_item("the task content", ContextCategory.TASK, score=1.0)]
        ctx = engine._assemble_context(items, "my task", "planner")
        assert ctx.task == "my task"  # Task is passed separately

    def test_assemble_primary_code_fills_correct_field(self):
        """Primary code items should populate ctx.primary_symbols."""
        engine = _make_engine()
        items = [_make_item("class AuthService:", ContextCategory.PRIMARY_CODE)]
        ctx = engine._assemble_context(items, "task", "planner")
        assert "AuthService" in ctx.primary_symbols

    def test_assemble_multiple_categories(self):
        """Multiple categories should each fill their respective fields."""
        engine = _make_engine()
        items = [
            _make_item("repo stats here", ContextCategory.REPOSITORY_SUMMARY),
            _make_item("step1: do thing", ContextCategory.IMPLEMENTATION_PLAN),
            _make_item("class Auth:", ContextCategory.PRIMARY_CODE),
            _make_item("test_auth.py", ContextCategory.RELATED_TESTS),
        ]
        ctx = engine._assemble_context(items, "task", "planner")
        assert ctx.repository_summary
        assert ctx.implementation_plan
        assert ctx.primary_symbols
        assert ctx.related_tests

    def test_assemble_preserves_order(self):
        """Items should be assembled in the order provided (already ranked)."""
        engine = _make_engine()
        items = [
            _make_item("FIRST", ContextCategory.PRIMARY_CODE, score=0.9),
            _make_item("SECOND", ContextCategory.PRIMARY_CODE, score=0.8),
        ]
        ctx = engine._assemble_context(items, "task", "planner")
        # Both should appear in primary_symbols in order
        assert ctx.primary_symbols.index("FIRST") < ctx.primary_symbols.index("SECOND")


# ═══════════════════════════════════════════════════════════════
# 5. End-to-End Context Pipeline Tests
# ═══════════════════════════════════════════════════════════════


class TestContextPipeline:
    """End-to-end tests for ContextEngine.build_context().

    These test the full pipeline: collect → rank → dedup → budget → assemble.
    Note: graph and DB sources won't work without a service, so these test
    the pure deterministic pipeline with task context only.
    """

    @pytest.mark.asyncio
    async def test_build_context_minimal(self):
        """Minimal input should produce a valid AgentContext."""
        engine = _make_engine()
        ctx = await engine.build_context(
            task="Fix token validation",
            agent_type="planner",
        )
        assert isinstance(ctx, AgentContext)
        assert ctx.task == "Fix token validation"
        assert ctx.agent_type == "planner"
        assert ctx.metrics.candidates_considered >= 1

    @pytest.mark.asyncio
    async def test_build_context_with_plan(self):
        """Plan text should be included in context."""
        engine = _make_engine()
        ctx = await engine.build_context(
            task="Add user authentication",
            agent_type="planner",
            plan_text="STEP-001: Create AuthService\nSTEP-002: Add login method",
        )
        assert ctx.implementation_plan
        assert "AuthService" in ctx.implementation_plan

    @pytest.mark.asyncio
    async def test_build_context_with_failures(self):
        """Test failures should be included in context."""
        engine = _make_engine()
        failures = [
            {"test_name": "test_login_fails", "message": "AssertionError: invalid token", "file_path": "tests/test_auth.py"},
        ]
        ctx = await engine.build_context(
            task="Fix login test",
            agent_type="repair",
            test_failures=failures,
        )
        assert ctx.previous_failures
        assert "test_login_fails" in ctx.previous_failures

    @pytest.mark.asyncio
    async def test_build_context_different_agent_types(self):
        """Different agent types produce valid context with correct agent_type."""
        engine = _make_engine()
        for agent_type in ["planner", "coding", "test", "repair", "reviewer"]:
            ctx = await engine.build_context(
                task=f"Task for {agent_type}",
                agent_type=agent_type,
            )
            assert ctx.agent_type == agent_type
            assert ctx.task == f"Task for {agent_type}"

    @pytest.mark.asyncio
    async def test_build_context_tracks_metrics(self):
        """Metrics should be tracked through the pipeline."""
        engine = _make_engine()
        ctx = await engine.build_context(
            task="Test metrics tracking",
            agent_type="planner",
            plan_text="Step 1: implement",
            test_failures=[{"test_name": "test_a", "message": "fail"}],
        )
        assert ctx.metrics.candidates_considered >= 3  # task + plan + failures
        assert ctx.metrics.items_selected >= 1
        assert ctx.metrics.tokens_before > 0
        assert ctx.metrics.tokens_after > 0

    @pytest.mark.asyncio
    async def test_build_context_prompt_section(self):
        """build_prompt_section() should produce structured output."""
        engine = _make_engine()
        ctx = await engine.build_context(
            task="Task for prompt test",
            agent_type="planner",
            plan_text="Implementation plan here",
        )
        prompt = ctx.build_prompt_section()
        assert "=== TASK ===" in prompt
        assert "Task for prompt test" in prompt
        assert "=== IMPLEMENTATION PLAN ===" in prompt

    @pytest.mark.asyncio
    async def test_explain_context(self):
        """explain_context() should produce diagnostic output."""
        engine = _make_engine()
        ctx = await engine.build_context(
            task="Test explain",
            agent_type="planner",
        )
        explanation = engine.explain_context(ctx)
        assert "Context for: planner" in explanation
        assert "Candidates considered" in explanation

    @pytest.mark.asyncio
    async def test_build_context_with_cross_agent_notes(self):
        """Phase 15: cross_agent_notes are assembled into ctx.agent_notes."""
        engine = _make_engine()
        ctx = await engine.build_context(
            task="Fix auth",
            agent_type="repair",
            cross_agent_notes=[
                "Planner produced a 3-step implementation plan: implement token validation",
                "Coding agent changed 2 file(s): auth.py, test_auth.py",
            ],
        )
        assert ctx.agent_notes
        assert "Planner produced" in ctx.agent_notes
        assert "Coding agent changed" in ctx.agent_notes
        assert ctx.metrics.cross_agent_items >= 1
        # Prompt section should include the notes
        prompt = ctx.build_prompt_section()
        assert "PRIOR AGENT NOTES" in prompt

    @pytest.mark.asyncio
    async def test_build_context_without_cross_agent_notes(self):
        """Without cross_agent_notes, agent_notes stays empty."""
        engine = _make_engine()
        ctx = await engine.build_context(
            task="Fix auth",
            agent_type="planner",
        )
        assert not ctx.agent_notes
        assert ctx.metrics.cross_agent_items == 0


# ═══════════════════════════════════════════════════════════════
# 6. Token Estimation Tests
# ═══════════════════════════════════════════════════════════════


class TestTokenEstimation:
    """Tests for the _estimate_tokens helper."""

    def test_estimate_tokens_rough_approximation(self):
        from app.services.context_engine import _estimate_tokens
        # 4 chars per token
        assert _estimate_tokens("hello") == 1
        assert _estimate_tokens("hello world") == 2  # 11 chars // 4 = 2
        assert _estimate_tokens("") == 0

    def test_estimate_tokens_longer_text(self):
        from app.services.context_engine import _estimate_tokens
        text = "a" * 100
        assert _estimate_tokens(text) == 25
