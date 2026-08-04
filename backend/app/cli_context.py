"""
Phase 13 CLI commands for Context Engineering diagnostics.

Usage:
    python -m app.cli context <repo> "<task>" --agent planner
    python -m app.cli context-explain <repo> "<task>"
"""

from __future__ import annotations

import sys
from pathlib import Path


def add_cli_commands(parent_parser) -> None:
    """Add Phase 13 CLI commands to the argument parser."""
    subparsers = parent_parser  # Passed as subparsers from main cli

    # context — build context for a task
    ctx_parser = subparsers.add_parser(
        "context", help="Build agent-specific context (Phase 13)"
    )
    ctx_parser.add_argument("repo", type=str, help="Path to the repository")
    ctx_parser.add_argument("task", type=str, help="Task description")
    ctx_parser.add_argument(
        "--agent", type=str, default="planner",
        choices=["planner", "coding", "test", "repair", "reviewer"],
        help="Agent type to build context for",
    )
    ctx_parser.add_argument(
        "--symbols", type=str, default=None,
        help="Comma-separated symbol names to focus on",
    )
    ctx_parser.add_argument(
        "--plan", type=str, default=None,
        help="Implementation plan text or path to plan file",
    )
    ctx_parser.add_argument(
        "--json", action="store_true",
        help="Output raw JSON instead of formatted display",
    )

    # context-explain — diagnostic explanation
    explain_parser = subparsers.add_parser(
        "context-explain", help="Show why context was selected (Phase 13 diagnostics)"
    )
    explain_parser.add_argument("repo", type=str, help="Path to the repository")
    explain_parser.add_argument("task", type=str, help="Task description")
    explain_parser.add_argument(
        "--agent", type=str, default="planner",
        choices=["planner", "coding", "test", "repair", "reviewer"],
        help="Agent type",
    )
    explain_parser.add_argument(
        "--symbols", type=str, default=None,
        help="Comma-separated symbol names",
    )


async def run_context(
    repo: str,
    task: str,
    agent: str = "planner",
    symbols: str | None = None,
    plan: str | None = None,
    json_output: bool = False,
) -> None:
    """Build context for a task and display results.

    Attempts to wire real services (CodeIntelligenceService,
    RepositoryMemoryService) into the ContextEngine so that
    graph context and repository memory are available in CLI mode.
    Falls back gracefully to a bare engine if services are unavailable.
    """
    from app.services.context_engine import ContextEngine

    path = Path(repo)
    if not path.is_dir():
        print(f"Error: Path is not a directory: {repo}")
        return

    repo_path = str(path.resolve())
    symbol_list = [s.strip() for s in symbols.split(",") if s.strip()] if symbols else None

    # Read plan from file if path provided
    plan_text = plan
    if plan and Path(plan).is_file():
        plan_text = Path(plan).read_text()

    # Attempt to wire real services for richer context
    cis = _try_init_code_intelligence()
    memory = _try_init_memory_service()

    engine = ContextEngine(
        code_intelligence_service=cis,
        memory_service=memory,
    )

    ctx = await engine.build_context(
        task=task,
        agent_type=agent,
        repository_path=repo_path,
        symbol_names=symbol_list,
        plan_text=plan_text,
    )

    if json_output:
        import json
        print(json.dumps({
            "agent_type": ctx.agent_type,
            "metrics": ctx.metrics.dict_summary(),
            "prompt_section": ctx.build_prompt_section(),
            "explanation": engine.explain_context(ctx),
        }, indent=2))
        return

    print(f"\n{'='*60}")
    print(f"  Phase 13 — Context: {agent}")
    print(f"  Repository: {path.name}")
    print(f"  Task: {task[:80]}")
    print(f"{'='*60}\n")

    # Show metrics
    m = ctx.metrics
    print(f"  Candidates considered:  {m.candidates_considered}")
    print(f"  Items selected:         {m.items_selected}")
    print(f"  Duplicates removed:     {m.duplicates_removed}")
    print(f"  Estimated tokens:       {m.tokens_before} -> {m.tokens_after}")

    if m.graph_items > 0:
        print(f"  Graph evidence:         {m.graph_items} items")
    if m.memory_items > 0:
        print(f"  Memory evidence:        {m.memory_items} items")
    if m.run_history_items > 0:
        print(f"  Run history:            {m.run_history_items} items")
    if m.test_failure_items > 0:
        print(f"  Test failures:          {m.test_failure_items} items")

    # Show prompt preview
    prompt = ctx.build_prompt_section()
    print(f"\n  {'='*56}\n  Prompt Section (first 1500 chars):\n  {'='*56}\n")
    print(f"  {prompt[:1500].replace(chr(10), chr(10)+'  ')}")

    if len(prompt) > 1500:
        print(f"\n  ... ({len(prompt) - 1500} more chars)")

    print(f"\n{'='*60}\n")


async def run_context_explain(
    repo: str,
    task: str,
    agent: str = "planner",
    symbols: str | None = None,
) -> None:
    """Show diagnostic explanation of context selection."""
    from app.services.context_engine import ContextEngine

    path = Path(repo)
    if not path.is_dir():
        print(f"Error: Path is not a directory: {repo}")
        return

    repo_path = str(path.resolve())
    symbol_list = [s.strip() for s in symbols.split(",") if s.strip()] if symbols else None

    # Attempt to wire real services
    cis = _try_init_code_intelligence()
    memory = _try_init_memory_service()

    engine = ContextEngine(
        code_intelligence_service=cis,
        memory_service=memory,
    )
    ctx = await engine.build_context(
        task=task,
        agent_type=agent,
        repository_path=repo_path,
        symbol_names=symbol_list,
    )

    explanation = engine.explain_context(ctx)

    print(f"\n{'='*60}")
    print(f"  Phase 13 — Context Explanation for: {agent}")
    print(f"  Repository: {path.name}")
    print(f"  Task: {task[:80]}")
    print(f"{'='*60}\n")

    print(explanation)

    print(f"{'='*60}\n")


# ── Service Injection Helpers ──────────────────────────────────


def _try_init_code_intelligence():
    """Try to initialize CodeIntelligenceService.

    Returns None if the service is unavailable (no API key,
    missing modules, etc.). This allows the CLI to gracefully
    degrade to task-only context when graph services aren't available.
    """
    try:
        from app.services.code_intelligence_service import CodeIntelligenceService
        svc = CodeIntelligenceService()
        # Quick health check via stats (won't crash if no graph)
        graph = svc.get_current_graph()
        if graph is not None:
            _ = graph.stats()
        return svc
    except Exception:
        return None


def _try_init_memory_service():
    """Try to initialize RepositoryMemoryService.

    Returns None if database is not configured. The memory service
    requires PostgreSQL — it gracefully degrades when unavailable.
    """
    try:
        from app.services.repository_memory_service import RepositoryMemoryService
        svc = RepositoryMemoryService()
        # Light check: verify session factory can be created
        _ = svc._get_session_factory()
        return svc
    except Exception:
        return None
