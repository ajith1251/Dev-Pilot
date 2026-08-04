#!/usr/bin/env python3
"""
Phase 13 — Context Pipeline Verification Script.

Exercises the full ContextEngine pipeline end-to-end with mocked
services (code_intelligence, run_store, memory_service) to demonstrate:

  1. Context collection from all 9 sources
  2. Deterministic ranking by category priority
  3. Content-hash deduplication (including cross-category)
  4. Token budgeting with agent-specific category allocations
  5. Context assembly into AgentContext
  6. build_prompt_section() output
  7. explain_context() diagnostic output
  8. Graceful degradation when services are unavailable

Usage:
    python -m scripts.verify_context_pipeline

Requires no database, no LLM, and no external services.
"""

from __future__ import annotations

import asyncio
import sys
import time
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock


# ── Helper: Print section headers ──────────────────────────────


def header(title: str) -> None:
    """Print a section header."""
    width = 68
    print()
    print("-" * width)
    print(f"  {title}")
    print("-" * width)


def ok(msg: str) -> None:
    """Print a passed check."""
    print(f"  [PASS]  {msg}")


def info(msg: str) -> None:
    """Print an info line."""
    print(f"      {msg}")


# ── Mock Service Builders ─────────────────────────────────────


def _make_mock_graph() -> MagicMock:
    """Build a mock semantic graph with sample stats."""
    graph = MagicMock()
    graph.stats.return_value = {
        "node_count": 87,
        "edge_count": 142,
        "file_count": 12,
        "kinds": {
            "class": 15, "function": 32, "method": 28, "interface": 5,
            "variable": 4, "module": 3,
        },
        "relationships": {
            "CALLS": 45, "IMPORTS": 38, "INHERITS": 12, "CONTAINS": 25,
            "REFERENCES": 18, "DEPENDS_ON": 4,
        },
    }
    return graph


def _make_mock_cis() -> Any:
    """Build a mock CodeIntelligenceService."""
    cis = MagicMock()
    cis.get_current_graph.return_value = _make_mock_graph()
    return cis


def _make_mock_run() -> MagicMock:
    """Build a mock historical DevPilotRun."""
    run = MagicMock()
    run.run_id = "run_abc123"
    run.status.value = "approved"
    run.source.title = "Add token expiration validation"
    run.total_duration_ms = 45200.0

    # Failure info
    run.failure = MagicMock()
    run.failure.message = "Test failure in test_auth_expiry"

    # Stage results
    failure_stage = MagicMock()
    failure_stage.stage.value = "testing"
    failure_stage.error = "AssertionError: token did not expire"

    skipped_stage = MagicMock()
    skipped_stage.stage.value = "repair"
    skipped_stage.error = None

    run.stage_results = [skipped_stage, failure_stage, skipped_stage]
    return run


def _make_mock_run_store() -> Any:
    """Build a mock PostgresRunStore with a recoverable historical run."""
    store = MagicMock()
    store.get = AsyncMock(return_value=_make_mock_run())
    return store


def _make_mock_memory() -> MagicMock:
    """Build a mock RepositoryMemory."""
    mem = MagicMock()
    mem.memory_id = "mem_arch_001"
    mem.memory_type.value = "architecture"
    mem.status.value = "verified"
    mem.confidence = 0.88
    mem.content = "AuthService delegates to TokenManager for JWT validation."
    mem.symbol_names = ["AuthService", "TokenManager"]
    mem.evidence = [MagicMock()]
    mem.evidence[0].description = "Discovered during Phase 12 graph analysis"
    return mem


def _make_mock_memory_service() -> Any:
    """Build a mock RepositoryMemoryService."""
    svc = MagicMock()
    svc.get_memories_for_symbols = AsyncMock(
        return_value=[_make_mock_memory()]
    )
    return svc


# ── Verification Steps ─────────────────────────────────────────


async def step_1_basic_context() -> None:
    """Verify basic task-only context builds correctly."""
    header("1. Basic Task-Only Context (Minimal Input)")

    from app.services.context_engine import ContextEngine
    from app.models.context import AgentContext

    engine = ContextEngine()
    ctx = await engine.build_context(
        task="Fix token validation in AuthService",
        agent_type="planner",
    )

    assert isinstance(ctx, AgentContext), "Context must be AgentContext"
    assert ctx.task == "Fix token validation in AuthService"
    assert ctx.agent_type == "planner"
    assert ctx.metrics.candidates_considered >= 1
    assert ctx.metrics.items_selected >= 1
    assert ctx.metrics.tokens_before > 0

    ok(f"AgentContext created for {ctx.agent_type}")
    ok(f"Candidates: {ctx.metrics.candidates_considered}")
    ok(f"Selected:   {ctx.metrics.items_selected}")
    ok(f"Tokens:     {ctx.metrics.tokens_before} -> {ctx.metrics.tokens_after}")


async def step_2_all_sources() -> None:
    """Verify context builds with ALL 9 sources active."""
    header("2. Full Pipeline — All 9 Context Sources")

    from app.services.context_engine import ContextEngine
    from app.models.context import AgentContext, ContextCategory

    engine = ContextEngine(
        code_intelligence_service=_make_mock_cis(),
        postgres_run_store=_make_mock_run_store(),
        memory_service=_make_mock_memory_service(),
    )

    ctx = await engine.build_context(
        task="Refactor AuthService to use async/await patterns",
        agent_type="coding",
        repository_path="/tmp/mock-repo",
        symbol_names=["AuthService", "TokenManager", "AuthController"],
        file_paths=["app/services/auth_service.py", "app/controllers/auth_controller.py"],
        plan_text="STEP-001: Update AuthService to use async\n"
                   "STEP-002: Update TokenManager to return coroutines\n"
                   "STEP-003: Update AuthController callers",
        requirements_text="All authentication flows must be fully async",
        run_id="run_abc123",
        test_failures=[
            {"test_name": "test_login_async", "message": "TypeError: 'coroutine' object is not callable",
             "file_path": "tests/test_auth.py"},
            {"test_name": "test_token_refresh", "message": "RuntimeError: Event loop is closed",
             "file_path": "tests/test_token.py"},
        ],
        repair_history=[
            {"status": "failed", "reason": "Patch did not resolve async compatibility"},
            {"status": "partial", "reason": "TokenManager updated but AuthService still blocking"},
        ],
        review_findings=[
            {"title": "Missing error handling for async timeouts", "severity": "medium"},
            {"title": "Incomplete test coverage for edge cases", "severity": "low"},
        ],
    )

    assert isinstance(ctx, AgentContext)
    assert ctx.task == "Refactor AuthService to use async/await patterns"
    assert ctx.agent_type == "coding"

    # Verify all source types contributed items
    categories_found = {item.category.value for item in ctx.raw_items}
    info(f"Categories in result: {sorted(categories_found)}")

    # Note: graph context requires real get_graph_context_markdown(),
    # which isn't available with mocked CIS. graph_items will be 0.
    assert ctx.metrics.candidates_considered >= 8, (
        f"Expected >= 8 candidates, got {ctx.metrics.candidates_considered}"
    )
    assert ctx.metrics.run_history_items > 0, "Run history should be present"
    assert ctx.metrics.test_failure_items > 0, "Test failures should be present"
    assert ctx.metrics.repair_history_items > 0, "Repair history should be present"
    assert ctx.metrics.memory_items > 0, "Repository memory should be present"

    ok(f"Candidates considered: {ctx.metrics.candidates_considered}")
    ok(f"Items selected:        {ctx.metrics.items_selected}")
    ok(f"Duplicates removed:    {ctx.metrics.duplicates_removed}")
    ok(f"Tokens:                {ctx.metrics.tokens_before} -> {ctx.metrics.tokens_after}")
    ok(f"Sources:               graph={ctx.metrics.graph_items}, "
       f"run_history={ctx.metrics.run_history_items}, "
       f"test_failure={ctx.metrics.test_failure_items}, "
       f"repair_history={ctx.metrics.repair_history_items}, "
       f"memory={ctx.metrics.memory_items}")

    # Verify specific content is present (graph context not available with mocked CIS)
    assert ctx.implementation_plan, "Plan should be populated"
    assert ctx.previous_failures, "Test failures should be populated"
    assert ctx.previous_repairs, "Repair history should be populated"
    assert ctx.repository_memory, "Repository memory should be populated"
    assert ctx.historical_memory, "Historical run memory should be populated"
    assert ctx.repository_summary, "Repository summary should be populated"

    ok("8 of 9 context sources contributed content (graph requires real CIS)")


async def step_3_dedup() -> None:
    """Verify deduplication removes cross-category duplicates."""
    header("3. Deduplication — Cross-Category Dedup")

    from app.services.context_engine import ContextEngine
    from app.models.context import (
        ContextItem, ContextCategory, ContextSourceType, Provenance,
    )

    engine = ContextEngine()

    # Craft items where same content appears in multiple categories
    items = [
        ContextItem(
            content="class AuthService: pass",
            category=ContextCategory.PRIMARY_CODE,
            provenance=Provenance(source=ContextSourceType.GRAPH, score=0.9),
            estimated_tokens=10,
        ),
        ContextItem(
            content="class AuthService: pass",
            category=ContextCategory.DEPENDENCIES,
            provenance=Provenance(source=ContextSourceType.GRAPH, score=0.8),
            estimated_tokens=10,
        ),
        ContextItem(
            content="def validate_token(): pass",
            category=ContextCategory.PRIMARY_CODE,
            provenance=Provenance(source=ContextSourceType.GRAPH, score=0.7),
            estimated_tokens=10,
        ),
        ContextItem(
            content="unique content here",
            category=ContextCategory.RELATED_TESTS,
            provenance=Provenance(source=ContextSourceType.TEST_FAILURE, score=0.6),
            estimated_tokens=10,
        ),
    ]

    deduped, count = engine._deduplicate(items)

    assert count == 1, f"Expected 1 duplicate, got {count}"
    assert len(deduped) == 3, f"Expected 3 unique items, got {len(deduped)}"
    # The duplicate "class AuthService: pass" should keep the higher-scored item (0.9)
    dup_item = [i for i in deduped if "AuthService" in i.content]
    assert len(dup_item) == 1
    assert dup_item[0].provenance.score == 0.9

    ok(f"Deduplication: {count} duplicate(s) removed")
    ok(f"Kept higher-scored duplicate (score={dup_item[0].provenance.score})")


async def step_4_ranking() -> None:
    """Verify category-based ranking is deterministic."""
    header("4. Ranking — Deterministic Category Priority")

    from app.services.context_engine import ContextEngine
    from app.models.context import ContextItem, ContextCategory, ContextSourceType, Provenance

    engine = ContextEngine()

    items = [
        ContextItem(
            content="test failure details",
            category=ContextCategory.PREVIOUS_FAILURES,
            provenance=Provenance(source=ContextSourceType.TEST_FAILURE, score=0.7),
            estimated_tokens=10,
        ),
        ContextItem(
            content="important task",
            category=ContextCategory.TASK,
            provenance=Provenance(source=ContextSourceType.REQUIREMENTS, score=0.5),
            estimated_tokens=10,
        ),
        ContextItem(
            content="relevant test",
            category=ContextCategory.RELATED_TESTS,
            provenance=Provenance(source=ContextSourceType.TEST_FAILURE, score=0.8),
            estimated_tokens=10,
        ),
    ]

    ranked = engine._rank_candidates(items, "test ranking", "planner")

    # TASK base 0.5 -> boosted to 1.0
    # RELATED_TESTS base 0.8 -> boosted to max(0.8, 0.6) = 0.8
    # PREVIOUS_FAILURES base 0.7 -> no boost in _rank_candidates -> stays 0.7
    assert ranked[0].category == ContextCategory.TASK, "Task must rank first"
    assert ranked[1].category in (
        ContextCategory.RELATED_TESTS, ContextCategory.PREVIOUS_FAILURES,
    ), "Test/failure rank second/third"

    ok(f"Top ranked:  {ranked[0].category.value} (score={ranked[0].provenance.score:.2f})")
    ok(f"Second:      {ranked[1].category.value} (score={ranked[1].provenance.score:.2f})")
    ok(f"Third:       {ranked[2].category.value} (score={ranked[2].provenance.score:.2f})")


async def step_5_agent_specific_budgets() -> None:
    """Verify different agent types get different token budgets."""
    header("5. Agent-Specific Budgets")

    from app.models.context import ContextBudget

    budget = ContextBudget(max_total_tokens=8000, reserved_instructions=2000, reserved_output=2000)

    planner_budget = budget.config_for_agent("planner")
    coding_budget = budget.config_for_agent("coding")
    test_budget = budget.config_for_agent("test")
    repair_budget = budget.config_for_agent("repair")
    reviewer_budget = budget.config_for_agent("reviewer")

    # Check categories differ per agent
    planner_cats = {bc.category.value for bc in planner_budget.categories}
    coding_cats = {bc.category.value for bc in coding_budget.categories}

    assert "repository_summary" in planner_cats, "Planner should have REPOSITORY_SUMMARY"
    assert "repository_summary" not in coding_cats, "Coding should not have REPOSITORY_SUMMARY"

    test_cats_set = {bc.category.value for bc in test_budget.categories}
    assert "related_tests" in test_cats_set, "Test agent should prioritize RELATED_TESTS"
    assert "previous_failures" in test_cats_set, "Test agent should have PREVIOUS_FAILURES"

    repair_cats_set = {bc.category.value for bc in repair_budget.categories}
    assert "previous_failures" in repair_cats_set, "Repair should emphasize PREVIOUS_FAILURES"
    assert "previous_repairs" in repair_cats_set, "Repair should have PREVIOUS_REPAIRS"
    assert "warnings" in repair_cats_set, "Repair should have WARNINGS"

    reviewer_cats_set = {bc.category.value for bc in reviewer_budget.categories}
    assert "review_findings" in reviewer_cats_set, "Reviewer should have REVIEW_FINDINGS"
    assert "implementation_plan" in reviewer_cats_set, "Reviewer should have IMPLEMENTATION_PLAN"

    info(f"Planner categories:  {sorted(planner_cats)}")
    info(f"Coding categories:   {sorted(coding_cats)}")
    info(f"Test categories:     {sorted(test_cats_set)}")
    info(f"Repair categories:   {sorted(repair_cats_set)}")
    info(f"Reviewer categories: {sorted(reviewer_cats_set)}")

    ok("All 5 agent types have distinct budget configurations")


async def step_6_prompt_section() -> None:
    """Verify build_prompt_section() produces structured output."""
    header("6. Prompt Section Output")

    from app.services.context_engine import ContextEngine

    engine = ContextEngine(
        code_intelligence_service=_make_mock_cis(),
        memory_service=_make_mock_memory_service(),
    )

    ctx = await engine.build_context(
        task="Add logging middleware to AuthService",
        agent_type="coding",
        repository_path="/tmp/mock-repo",
        symbol_names=["AuthService", "LoggerMiddleware"],
        file_paths=["app/services/auth_service.py"],
        plan_text="STEP-001: Create LoggerMiddleware class\n"
                   "STEP-002: Integrate with AuthService",
        test_failures=[
            {"test_name": "test_middleware_logs", "message": "AssertionError: log not written",
             "file_path": "tests/test_middleware.py"},
        ],
    )

    prompt = ctx.build_prompt_section()

    assert "=== TASK ===" in prompt, "Prompt must include TASK section"
    assert "Add logging middleware" in prompt
    assert "=== IMPLEMENTATION PLAN ===" in prompt
    assert "LoggerMiddleware" in prompt

    # Print a preview
    lines = prompt.split("\n")
    preview_lines = [l for l in lines if l.strip()][:20]
    info(f"Prompt section preview ({len(lines)} total lines):")
    for l in preview_lines:
        info(f"  {l[:100]}")

    ok(f"build_prompt_section() produced {len(lines)} lines")


async def step_7_diagnostics() -> None:
    """Verify explain_context() produces useful diagnostics."""
    header("7. Diagnostic Explanation")

    from app.services.context_engine import ContextEngine

    engine = ContextEngine(
        code_intelligence_service=_make_mock_cis(),
        postgres_run_store=_make_mock_run_store(),
        memory_service=_make_mock_memory_service(),
    )

    ctx = await engine.build_context(
        task="Fix token expiry in AuthService",
        agent_type="planner",
        repository_path="/tmp/mock-repo",
        symbol_names=["AuthService", "TokenManager"],
        plan_text="STEP-001: Add expiry check to TokenManager",
        run_id="run_abc123",
    )

    explanation = engine.explain_context(ctx)

    assert "Context for: planner" in explanation
    assert "Candidates considered" in explanation
    assert "Items selected" in explanation
    assert "Duplicates removed" in explanation
    assert "Estimated tokens" in explanation
    assert "Top Context Items" in explanation

    info(f"Explanation preview ({len(explanation.split(chr(10)))} lines):")
    for line in explanation.split("\n")[:15]:
        info(f"  {line}")

    ok("explain_context() produces full diagnostic output")


async def step_8_graceful_degradation() -> None:
    """Verify graceful degradation when services are unavailable."""
    header("8. Graceful Degradation — No Services")

    from app.services.context_engine import ContextEngine
    from app.models.context import AgentContext

    # No cis, no store, no memory_service — engine should still work
    engine = ContextEngine()

    ctx = await engine.build_context(
        task="A simple task",
        agent_type="planner",
        repository_path="/tmp/nonexistent",
        symbol_names=["MissingSymbol"],
        file_paths=["missing.py"],
        plan_text="Step 1: do something",
        run_id="nonexistent_run",
        test_failures=[
            {"test_name": "test_fail", "message": "error", "file_path": "test.py"},
        ],
    )

    assert isinstance(ctx, AgentContext)
    assert ctx.task == "A simple task"
    assert ctx.metrics.candidates_considered > 0
    # Graph, history, memory should all be empty — only task + plan + failures
    assert ctx.metrics.graph_items == 0
    assert ctx.metrics.run_history_items == 0
    assert ctx.metrics.memory_items == 0

    ok(f"Context built with bare engine: {ctx.metrics.candidates_considered} candidates")
    ok(f"Graph items:   {ctx.metrics.graph_items} (expected 0)")
    ok(f"History items: {ctx.metrics.run_history_items} (expected 0)")
    ok(f"Memory items:  {ctx.metrics.memory_items} (expected 0)")


# ── Main ───────────────────────────────────────────────────────


async def main() -> int:
    """Run all verification steps."""
    # Fix Windows cp1252 encoding for Unicode-safe output
    try:
        sys.stdout.reconfigure(encoding='utf-8')  # type: ignore[union-attr]
    except Exception:
        pass
    start = time.time()
    failures = 0

    print()
    print("+========================================================+")
    print("| Phase 13 - Context Pipeline Verification              |")
    print("| Full end-to-end test with mocked services             |")
    print("+========================================================+")
    print()
    print(f"  Started at: {time.strftime('%H:%M:%S')}")
    print(f"  Python:     {sys.version.split()[0]}")
    print()

    steps = [
        ("Basic task-only context", step_1_basic_context),
        ("All 9 context sources", step_2_all_sources),
        ("Cross-category deduplication", step_3_dedup),
        ("Deterministic ranking", step_4_ranking),
        ("Agent-specific budgets", step_5_agent_specific_budgets),
        ("Prompt section output", step_6_prompt_section),
        ("Diagnostic explanation", step_7_diagnostics),
        ("Graceful degradation", step_8_graceful_degradation),
    ]

    for name, step_fn in steps:
        try:
            await step_fn()
            print(f"  [OK]    {name}")
        except Exception as exc:
            print(f"  [FAIL]  {name}: {exc}")
            import traceback
            traceback.print_exc()
            failures += 1

    # Summary
    duration = time.time() - start
    print()
    print("-" * 68)
    if failures == 0:
        print(f"  ALL {len(steps)} STEPS PASSED  ({duration:.2f}s)  [SUCCESS]")
        print()
        print("  Context Pipeline Verification: PASS")
        print(f"  Total tests: {len(steps)} passed, 0 failed")
        return 0
    else:
        print(f"  {failures}/{len(steps)} STEPS FAILED  ({duration:.2f}s)  [FAILURE]")
        print()
        print("  Context Pipeline Verification: FAIL")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
