"""
DevPilot CLI — safe demo tool for repository analysis.

Usage:
    python -m app.cli analyze /path/to/repository
    python -m app.cli analyze . --depth 5
    python -m app.cli github analyze https://github.com/owner/repo
    python -m app.cli github issue https://github.com/owner/repo/issues/42
    python -m app.cli run . --task "Fix auth"
    python -m app.cli verify-persistence
    python -m app.cli verify

This script is for demonstration only. It never executes target code.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

# Ensure package can be found when run as script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="DevPilot — analyze local or remote repositories"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # analyze command (local)
    analyze_parser = subparsers.add_parser(
        "analyze", help="Analyze a local repository"
    )
    analyze_parser.add_argument(
        "path", type=str, help="Path to the repository"
    )
    analyze_parser.add_argument(
        "--depth", type=int, default=10, help="Max directory depth"
    )

    # github subcommand group
    github_parser = subparsers.add_parser(
        "github", help="GitHub operations (analyze, issue, info)"
    )
    github_subparsers = github_parser.add_subparsers(
        dest="github_command", help="GitHub sub-command"
    )
    gh_analyze = github_subparsers.add_parser("analyze", help="Analyze a remote GitHub repository")
    gh_analyze.add_argument("url", type=str, help="GitHub repository URL")
    gh_analyze.add_argument("--ref", type=str, default=None, help="Branch/tag to analyze")
    gh_analyze.add_argument("--no-shallow", action="store_true", help="Full clone instead of shallow")
    gh_issue = github_subparsers.add_parser("issue", help="Fetch a GitHub issue")
    gh_issue.add_argument("url", type=str, help="GitHub issue URL")
    gh_info = github_subparsers.add_parser("info", help="Show repository metadata")
    gh_info.add_argument("url", type=str, help="GitHub repository URL")

    # ── Plan subcommand ─────────────────────────────────────────
    plan_parser = subparsers.add_parser("plan", help="Create an implementation plan from a task")
    plan_parser.add_argument("--task", type=str, required=True, help="Task title")
    plan_parser.add_argument("--description", type=str, default="", help="Task description")
    plan_parser.add_argument("--repo-path", type=str, default=None, help="Local repository path for context")
    gh_plan = github_subparsers.add_parser("plan", help="Create an implementation plan from a GitHub issue")
    gh_plan.add_argument("url", type=str, help="GitHub issue URL")

    # ── Phase 5: Index and Search ───────────────────────────────
    index_parser = subparsers.add_parser("index", help="Build a repository code index (Phase 5)")
    index_parser.add_argument("path", type=str, help="Path to the repository")
    index_parser.add_argument("--embeddings", action="store_true", help="Generate embeddings")
    search_parser = subparsers.add_parser("search", help="Search indexed repository code (Phase 5)")
    search_parser.add_argument("path", type=str, help="Path to the repository")
    search_parser.add_argument("query", type=str, help="Search query")
    search_parser.add_argument("--top-k", type=int, default=10, help="Number of results")
    search_parser.add_argument("--embeddings", action="store_true", help="Enable semantic search")
    plan_ctx_parser = subparsers.add_parser("plan-context", help="Retrieve context for plan (Phase 5)")
    plan_ctx_parser.add_argument("path", type=str, help="Path to the repository")
    plan_ctx_parser.add_argument("--plan-file", type=str, help="Path to ImplementationPlan JSON")
    plan_ctx_parser.add_argument("--top-k", type=int, default=5, help="Results per step")

    # ── Phase 6: Code generation ────────────────────────────────
    code_parser = subparsers.add_parser("code", help="Generate code changes (Phase 6)")
    code_parser.add_argument("--plan-file", type=str, required=True, help="Path to ImplementationPlan JSON")
    code_parser.add_argument("--context-file", type=str, required=True, help="Path to RetrievedContext JSON")
    code_parser.add_argument("--repo-path", type=str, required=True, help="Path to the source repository")
    code_parser.add_argument("--output", type=str, default=None, help="Output file for PatchSet JSON")
    code_parser.add_argument("--dry-run", action="store_true", help="Dry-run the patch")
    code_parser.add_argument("--apply", action="store_true", help="Apply the patch to workspace")

    # ── Phase 7: Test subcommands ───────────────────────────────
    test_plan_parser = subparsers.add_parser("test-plan", help="Create a test execution plan (Phase 7)")
    test_plan_parser.add_argument("--workspace", type=str, required=True, help="Workspace root path")
    test_plan_parser.add_argument("--workspace-id", type=str, default="cli-workspace", help="Workspace identifier")
    test_plan_parser.add_argument("--changed-files", type=str, default=None, help="Comma-separated changed files")
    test_run_parser = subparsers.add_parser("test", help="Run tests in a workspace (Phase 7)")
    test_run_parser.add_argument("--workspace", type=str, required=True, help="Workspace root path")
    test_run_parser.add_argument("--workspace-id", type=str, default="cli-workspace", help="Workspace identifier")
    test_run_parser.add_argument("--timeout", type=int, default=60, help="Per-command timeout in seconds")
    test_run_parser.add_argument("--changed-files", type=str, default=None, help="Comma-separated changed files")

    # ── Phase 8: Repair subcommands ─────────────────────────────
    repair_diag_parser = subparsers.add_parser("repair-diagnose", help="Diagnose test failures (Phase 8)")
    repair_diag_parser.add_argument("--run-file", type=str, required=True, help="Path to TestRunResult JSON")
    repair_diag_parser.add_argument("--patch-file", type=str, default=None, help="Path to PatchSet JSON")
    repair_diag_parser.add_argument("--plan-file", type=str, default=None, help="Path to ImplementationPlan JSON")
    repair_run_parser = subparsers.add_parser("repair", help="Execute bounded repair loop (Phase 8)")
    repair_run_parser.add_argument("--workspace", type=str, required=True, help="Workspace root path")
    repair_run_parser.add_argument("--workspace-id", type=str, default="cli-repair", help="Workspace identifier")
    repair_run_parser.add_argument("--run-file", type=str, required=True, help="Path to TestRunResult JSON")
    repair_run_parser.add_argument("--patch-file", type=str, default=None, help="Path to PatchSet JSON")
    repair_run_parser.add_argument("--plan-file", type=str, default=None, help="Path to ImplementationPlan JSON")
    repair_run_parser.add_argument("--max-attempts", type=int, default=None, help="Override max repair attempts")

    # ── Phase 9: Review subcommand ──────────────────────────────
    review_parser = subparsers.add_parser("review", help="Review implementation quality (Phase 9)")
    review_parser.add_argument("--workspace-id", type=str, default="review-ws", help="Workspace identifier")
    review_parser.add_argument("--plan-file", type=str, default=None, help="Path to ImplementationPlan JSON")
    review_parser.add_argument("--requirements-file", type=str, default=None, help="Path to StructuredRequirements JSON")
    review_parser.add_argument("--patch-file", type=str, default=None, help="Path to PatchSet JSON")
    review_parser.add_argument("--run-file", type=str, default=None, help="Path to TestRunResult JSON")
    review_parser.add_argument("--repair-file", type=str, default=None, help="Path to RepairResult JSON")
    review_parser.add_argument("--use-llm", action="store_true", help="Enable LLM-assisted review")
    review_parser.add_argument("--verbose", action="store_true", help="Show detailed findings")

    # ── Phase 6: Patch subcommand ───────────────────────────────
    patch_parser = subparsers.add_parser("patch", help="Apply or dry-run a pre-generated patch (Phase 6)")
    patch_parser.add_argument("--patch-file", type=str, required=True, help="Path to PatchSet JSON")
    patch_parser.add_argument("--workspace", type=str, required=True, help="Workspace root path")
    patch_parser.add_argument("--dry-run", action="store_true", help="Dry-run without modifying files")
    patch_parser.add_argument("--apply", action="store_true", help="Apply the patch")

    # ── Database ─────────────────────────────────────────────────
    db_parser = subparsers.add_parser(
        "db-check", help="Check PostgreSQL database connectivity"
    )

    # ── Phase 10: Run subcommand ────────────────────────────────
    run_parser = subparsers.add_parser("run", help="Run end-to-end DevPilot pipeline (Phase 10)")
    run_parser.add_argument("repo", type=str, help="Path to repository or GitHub URL")
    run_parser.add_argument("--task", type=str, required=True, help="Task title")
    run_parser.add_argument("--description", type=str, default="", help="Task description")
    run_parser.add_argument("--json", action="store_true", help="Output structured JSON result")

    # ── Phase 11H: Verification commands ──────────────────────────
    vp_parser = subparsers.add_parser(
        "verify-persistence",
        help="Verify PostgreSQL persistence: connectivity, schema, migrations, and basic operations"
    )
    verify_parser = subparsers.add_parser(
        "verify",
        help="Run all safe local verification checks (config, DB, Alembic, tests, contracts)"
    )

    # ── Phase 12: Code Intelligence Commands ───────────────────────
    from app.cli_code_intelligence import add_cli_commands
    add_cli_commands(subparsers)

    # ── Phase 13: Context Engineering Commands ─────────────────────
    from app.cli_context import add_cli_commands as add_context_commands
    add_context_commands(subparsers)

    # ── Phase 15: Multi-Agent Collaboration Commands ────────────────
    from app.cli_collaboration import add_cli_commands as add_collaboration_commands
    add_collaboration_commands(subparsers)

    # ── Phase 16: Autonomous Execution Commands ────────────────────
    from app.cli_autonomy import add_cli_commands as add_autonomy_commands
    add_autonomy_commands(subparsers)

    # ── Phase 17: Collaborative Reasoning Commands ─────────────────
    from app.cli_reasoning import add_cli_commands as add_reasoning_commands
    add_reasoning_commands(subparsers)

    # ── Phase 18: Engineering Knowledge Graph Commands ─────────────
    from app.cli_engineering_graph import add_cli_commands as add_graph_commands
    add_graph_commands(subparsers)

    # ── Phase 19B: Multi-Provider Failover & Reliability Commands ───
    from app.cli_providers import add_cli_commands as add_providers_commands
    add_providers_commands(subparsers)

    args = parser.parse_args()

    if args.command == "analyze":
        asyncio.run(run_analysis(args.path, args.depth))
    elif args.command == "plan":
        asyncio.run(run_plan(args.task, args.description, args.repo_path))
    elif args.command == "repair-diagnose":
        asyncio.run(run_repair_diagnose(args.run_file, args.patch_file, args.plan_file))
    elif args.command == "repair":
        asyncio.run(run_repair_workflow(args.workspace, args.workspace_id, args.run_file, args.patch_file, args.plan_file, args.max_attempts))
    elif args.command == "test-plan":
        changed = args.changed_files.split(",") if args.changed_files else []
        asyncio.run(run_test_plan(args.workspace, args.workspace_id, changed))
    elif args.command == "test":
        changed = args.changed_files.split(",") if args.changed_files else []
        asyncio.run(run_test_execution(args.workspace, args.workspace_id, args.timeout, changed))
    elif args.command == "index":
        asyncio.run(run_index(args.path, args.embeddings))
    elif args.command == "search":
        asyncio.run(run_search(args.path, args.query, args.top_k, args.embeddings))
    elif args.command == "plan-context":
        asyncio.run(run_plan_context(args.path, args.plan_file, args.top_k))
    elif args.command == "code":
        asyncio.run(run_code_generation(args.plan_file, args.context_file, args.repo_path, args.output, args.dry_run, args.apply))
    elif args.command == "review":
        asyncio.run(run_review(workspace_id=args.workspace_id, plan_file=args.plan_file, requirements_file=args.requirements_file, patch_file=args.patch_file, run_file=args.run_file, repair_file=args.repair_file, use_llm=args.use_llm, verbose=args.verbose))
    elif args.command == "patch":
        asyncio.run(run_patch_operations(args.patch_file, args.workspace, args.dry_run, args.apply))
    elif args.command == "db-check":
        asyncio.run(run_db_check())
    elif args.command == "verify-persistence":
        asyncio.run(run_verify_persistence())
    elif args.command == "verify":
        asyncio.run(run_verify())
    elif args.command == "run":
        asyncio.run(run_orchestration(repo=args.repo, task=args.task, description=args.description, json_output=args.json))
    elif args.command == "code-index":
        from app.cli_code_intelligence import run_code_index
        run_code_index(args.path, args.verbose)
    elif args.command == "code-symbols":
        from app.cli_code_intelligence import run_code_symbols
        run_code_symbols(args.path, args.kind, args.name, args.limit)
    elif args.command == "code-symbol":
        from app.cli_code_intelligence import run_code_symbol_detail
        run_code_symbol_detail(args.path, args.symbol_id, args.depth)
    elif args.command == "code-impact":
        from app.cli_code_intelligence import run_code_impact
        run_code_impact(args.path, args.symbol, args.depth)
    elif args.command == "code-retrieve":
        from app.cli_code_intelligence import run_code_retrieve
        run_code_retrieve(args.path, args.symbol, args.depth)
    elif args.command == "code-status":
        from app.cli_code_intelligence import run_code_status
        run_code_status(args.path)
    elif args.command == "context":
        from app.cli_context import run_context
        asyncio.run(run_context(args.repo, args.task, args.agent, args.symbols, args.plan, args.json))
    elif args.command == "context-explain":
        from app.cli_context import run_context_explain
        asyncio.run(run_context_explain(args.repo, args.task, args.agent, args.symbols))
    elif args.command == "handoffs":
        from app.cli_collaboration import run_handoffs
        asyncio.run(run_handoffs(args.run_id, args.to_agent, args.json))
    elif args.command == "decisions":
        from app.cli_collaboration import run_decisions
        asyncio.run(run_decisions(args.run_id, args.json))
    elif args.command == "collaboration":
        from app.cli_collaboration import run_collaboration
        asyncio.run(run_collaboration(args.run_id, args.json))
    elif args.command == "consensus":
        from app.cli_reasoning import run_consensus
        asyncio.run(run_consensus(args.run_id, args.json))
    elif args.command == "conflicts":
        from app.cli_reasoning import run_conflicts
        asyncio.run(run_conflicts(args.run_id, args.json))
    elif args.command == "notebook":
        from app.cli_reasoning import run_notebook
        asyncio.run(run_notebook(args.run_id, args.json))
    elif args.command == "autonomous-run":
        from app.cli_autonomy import run_autonomous_run
        asyncio.run(run_autonomous_run(args.repo, args.task, args.criteria,
                                       args.max_iterations, args.max_replans, args.json))
    elif args.command == "autonomous-status":
        from app.cli_autonomy import run_autonomous_status
        asyncio.run(run_autonomous_status(args.goal_id, args.json))
    elif args.command == "autonomous-dry-run":
        from app.cli_autonomy import run_autonomous_dry_run
        asyncio.run(run_autonomous_dry_run(args.repo, args.task, args.criteria, args.json))
    elif args.command == "autonomous-pause":
        from app.cli_autonomy import run_autonomous_control
        asyncio.run(run_autonomous_control("pause", args.goal_id))
    elif args.command == "autonomous-resume":
        from app.cli_autonomy import run_autonomous_control
        asyncio.run(run_autonomous_control("resume", args.goal_id))
    elif args.command == "autonomous-cancel":
        from app.cli_autonomy import run_autonomous_control
        asyncio.run(run_autonomous_control("cancel", args.goal_id))
    elif args.command == "graph":
        from app.cli_engineering_graph import (
            run_graph_query, run_graph_explain, run_graph_history,
            run_graph_neighborhood, run_graph_version,
            run_graph_org_stats, run_graph_org_repositories,
            run_graph_org_cross_edges, run_graph_org_query,
            run_graph_org_traversal,
        )
        if args.graph_command == "query":
            asyncio.run(run_graph_query(args.query, args.limit, args.json))
        elif args.graph_command == "explain":
            asyncio.run(run_graph_explain(args.node_id, args.json))
        elif args.graph_command == "history":
            asyncio.run(run_graph_history(args.node_id, args.json))
        elif args.graph_command == "neighborhood":
            asyncio.run(run_graph_neighborhood(
                args.node_id, args.depth, args.max_nodes, args.json))
        elif args.graph_command == "version":
            asyncio.run(run_graph_version(args.json))
        elif args.graph_command == "org-stats":
            asyncio.run(run_graph_org_stats(args.json))
        elif args.graph_command == "org-repositories":
            asyncio.run(run_graph_org_repositories(args.json))
        elif args.graph_command == "org-cross-edges":
            asyncio.run(run_graph_org_cross_edges(args.json))
        elif args.graph_command == "org-query":
            asyncio.run(run_graph_org_query(
                args.query, args.scope, args.repository_id, args.limit, args.json))
        elif args.graph_command == "org-traversal":
            asyncio.run(run_graph_org_traversal(
                args.node_id, args.depth, args.max_nodes, args.json))
        else:
            from app.cli_engineering_graph import add_cli_commands
            tmp = argparse.ArgumentParser()
            add_cli_commands(tmp.add_subparsers())
            parser.print_help()
    elif args.command == "github":
        if args.github_command == "analyze":
            asyncio.run(run_github_analysis(args.url, args.ref, not args.no_shallow))
        elif args.command == "github" and args.github_command == "issue":
            asyncio.run(run_github_issue(args.url))
        elif args.command == "github" and args.github_command == "info":
            asyncio.run(run_github_info(args.url))
        elif args.command == "github" and args.github_command == "plan":
            asyncio.run(run_github_plan(args.url))
        else:
            github_parser.print_help()
    elif args.command == "providers":
        from app.cli_providers import run_providers
        run_providers(args.json)
    elif args.command == "provider-health":
        from app.cli_providers import run_provider_health
        run_provider_health(args.json)
    elif args.command == "provider-metrics":
        from app.cli_providers import run_provider_metrics
        run_provider_metrics(args.json)
    elif args.command == "provider-test":
        from app.cli_providers import run_provider_test
        asyncio.run(run_provider_test(args.message, args.model, args.json))
    else:
        parser.print_help()


async def run_analysis(repo_path: str, max_depth: int = 10) -> None:
    """Run repository analysis and display results."""
    from app.workflows.repository_analysis import RepositoryAnalysisWorkflow
    path = Path(repo_path).resolve()
    print(f"\n{'='*60}\n  DevPilot Repository Intelligence\n{'='*60}")
    print(f"  Repository: {path.name}\n  Path:       {path}\n{'='*60}\n")
    if not path.is_dir():
        print(f"  ❌ Error: Path is not a directory: {repo_path}")
        sys.exit(1)
    workflow = RepositoryAnalysisWorkflow()
    state = await workflow.run(str(path))
    if not state.profile:
        print("  ❌ No profile generated")
        sys.exit(1)
    profile = state.profile
    print(f"  Scan: {profile.scan.total_files_scanned} files, {profile.scan.total_dirs_scanned} dirs, {profile.scan.total_files_ignored} ignored")
    print(f"  Duration: {profile.scan.duration_seconds}s\n")
    print(f"{'='*60}\n  Status: {state.status}\n{'='*60}\n")


async def run_github_analysis(url: str, ref: str | None = None, shallow: bool = True) -> None:
    """Run remote GitHub repository analysis."""
    from app.workflows.remote_analysis import RemoteAnalysisWorkflow
    print(f"\n{'='*60}\n  DevPilot Remote Repository Analysis\n{'='*60}\n  Repository: {url}\n{'='*60}\n")
    workflow = RemoteAnalysisWorkflow()
    state = await workflow.run(url=url, ref=ref, shallow=shallow)
    if not state.result:
        print("  ❌ No result generated")
        return
    r = state.result
    print(f"  Repository: {r.github.full_name}\n  Description: {r.github.description or '(none)'}")
    print(f"  Language: {r.github.language or '?'}")
    print(f"  ⭐ {r.github.stargazers_count}  🍴 {r.github.forks_count}  📋 {r.github.open_issues_count}")
    print(f"\n  Duration: {r.acquisition.duration_seconds}s\n{'='*60}\n  Status: {state.status}\n{'='*60}\n")


async def run_github_issue(url: str) -> None:
    """Fetch and display a GitHub issue."""
    from app.services.github import GitHubService
    print(f"\n{'='*60}\n  DevPilot GitHub Issue Fetcher\n{'='*60}\n  URL: {url}\n{'='*60}\n")
    github = GitHubService()
    try:
        parsed = github.parse_issue_url(url)
        issue = await github.get_issue(parsed[0], parsed[1], parsed[2])
    except Exception as exc:
        print(f"  ❌ Error: {exc}")
        return
    print(f"  #{issue.number}: {issue.title}\n  State: {issue.state}")
    body_preview = (issue.body or "")[:500]
    if body_preview:
        print(f"  Body:\n  {body_preview}")
    print(f"\n{'='*60}\n")


async def run_plan(title: str, description: str = "", repo_path: str | None = None) -> None:
    """Create an implementation plan from a user task."""
    from app.workflows.planning import PlanningWorkflow
    print(f"\n{'='*60}\n  DevPilot Planning\n{'='*60}\n  Task: {title[:80]}\n{'='*60}\n")
    workflow = PlanningWorkflow()
    state = await workflow.run_from_task(title=title, description=description, repo_path=repo_path)
    if state.plan and not state.plan.error:
        print(f"  Plan steps ({len(state.plan.steps)}):")
        for step in state.plan.steps[:8]:
            print(f"    {step.id}: {step.title}")
    print(f"\n{'='*60}\n  Status: {state.status}\n{'='*60}\n")


async def run_github_plan(url: str) -> None:
    """Create plan from GitHub issue."""
    from app.workflows.planning import PlanningWorkflow
    print(f"\n{'='*60}\n  DevPilot GitHub Issue Planning\n{'='*60}\n  URL: {url}\n{'='*60}\n")
    workflow = PlanningWorkflow()
    state = await workflow.run_from_github(url=url)
    if state.plan and not state.plan.error:
        print(f"  Plan steps ({len(state.plan.steps)}):")
        for step in state.plan.steps[:8]:
            print(f"    {step.id}: {step.title}")
    print(f"\n{'='*60}\n  Status: {state.status}\n{'='*60}\n")


async def run_index(repo_path: str, embeddings: bool = False) -> None:
    """Build a repository code index (Phase 5)."""
    from app.services.index_builder import RepositoryIndexBuilder
    path = Path(repo_path).resolve()
    print(f"\n{'='*60}\n  DevPilot Code Index Builder (Phase 5)\n{'='*60}\n  Repository: {path.name}\n{'='*60}\n")
    if not path.is_dir():
        print(f"  ❌ Error: Path is not a directory: {repo_path}")
        return
    builder = RepositoryIndexBuilder(enable_embeddings=embeddings)
    index = builder.build(str(path))
    print(f"  Files indexed: {index.statistics.files_indexed}")
    print(f"  Symbols: {index.statistics.symbols_extracted}")
    print(f"  Duration: {index.statistics.duration_seconds}s\n{'='*60}\n")


async def run_search(repo_path: str, query: str, top_k: int = 10, embeddings: bool = False) -> None:
    """Search indexed repository code."""
    from app.services.index_builder import RepositoryIndexBuilder
    from app.rag.retrieval.hybrid_retriever import HybridRetriever
    from app.models.rag import RetrievalQuery
    path = Path(repo_path).resolve()
    print(f"\n{'='*60}\n  DevPilot Code Search (Phase 5)\n{'='*60}\n  Query: {query}\n{'='*60}\n")
    if not path.is_dir():
        return
    builder = RepositoryIndexBuilder(enable_embeddings=embeddings)
    code_index, lex_idx, sym_idx, vec_idx = builder.build_with_indexes(str(path))
    retriever = HybridRetriever(lexical_index=lex_idx, symbol_index=sym_idx, vector_index=vec_idx)
    retriever.set_indexes(lex_idx, sym_idx, vec_idx, code_index.chunks)
    rq = RetrievalQuery(text=query, top_k=top_k)
    result = retriever.retrieve(rq)
    print(f"  Found {len(result.items)} results\n{'='*60}\n")


async def run_plan_context(repo_path: str, plan_file: str | None = None, top_k: int = 5) -> None:
    """Retrieve context for an implementation plan."""
    from app.rag.retrieval.plan_context_retriever import PlanContextRetriever
    from app.models.issues import ImplementationPlan, ImplementationStep
    path = Path(repo_path).resolve()
    print(f"\n{'='*60}\n  DevPilot Plan Context Retrieval (Phase 5)\n{'='*60}\n")
    if not path.is_dir():
        return
    plan = ImplementationPlan(
        summary="Demo plan", objective="Demo objective",
        steps=[ImplementationStep(id="STEP-001", title="Demo step", description="Implement the feature", affected_areas=["src"])],
        test_strategy="Unit tests",
    )
    retriever = PlanContextRetriever()
    result = await retriever.retrieve_for_plan(plan=plan, repository_path=str(path), top_k_per_step=top_k)
    print(f"  Retrieved {result.total_chunks} chunks\n{'='*60}\n")


async def run_code_generation(plan_file: str, context_file: str, repo_path: str, output: str | None = None, dry_run: bool = False, apply: bool = False) -> None:
    """Generate code changes from plan + context (Phase 6)."""
    print(f"\n{'='*60}\n  DevPilot Code Generation (Phase 6)\n{'='*60}\n")
    print("  Code generation requires an LLM provider. Use the test suite.\n{'='*60}\n")


async def run_patch_operations(patch_file: str, workspace: str, dry_run: bool = False, apply: bool = False) -> None:
    """Apply or dry-run a pre-generated patch (Phase 6)."""
    import json
    print(f"\n{'='*60}\n  DevPilot Patch Operations (Phase 6)\n{'='*60}\n")
    try:
        from app.models.coding import PatchSet
        from app.services.safe_patch_engine import SafePatchEngine
        with open(patch_file, "r") as f:
            patch_data = json.load(f)
        patch = PatchSet(**patch_data)
    except Exception as exc:
        print(f"  [!] Error loading patch file: {exc}")
        return
    engine = SafePatchEngine(workspace_root=workspace)
    if apply:
        result = engine.apply(patch)
        print(f"  Result: {result.status.value}")
    elif dry_run:
        result = engine.dry_run(patch)
        print(f"  Result: {result.status.value}  (dry-run)")
    else:
        print(f"  PatchSet: {patch.patch_id} — {len(patch.changes)} changes")
        print(f"  Use --dry-run to simulate or --apply to execute")
    print(f"\n{'='*60}\n")


async def run_test_plan(workspace_root: str, workspace_id: str, changed_files: list[str] | None = None) -> None:
    """Create a test execution plan (Phase 7)."""
    from app.agents.test_agent import TestAgent, TestAgentInput
    print(f"\n{'='*60}\n  DevPilot Test Plan (Phase 7)\n{'='*60}\n")
    ws = Path(workspace_root)
    if not ws.is_dir():
        print(f"  [!] Workspace not found: {workspace_root}")
        return
    agent = TestAgent()
    inp = TestAgentInput(workspace_id=workspace_id, workspace_root=workspace_root, changed_files=changed_files or [])
    output = await agent.execute(inp)
    if output.plan and output.plan.steps:
        for step in output.plan.steps:
            print(f"  {step.step_id}: {' '.join(step.arguments)}")
    print(f"\n{'='*60}\n")


async def run_test_execution(workspace_root: str, workspace_id: str, timeout: int = 60, changed_files: list[str] | None = None) -> None:
    """Execute tests in a workspace (Phase 7)."""
    from app.agents.test_agent import TestAgent, TestAgentInput
    from app.services.testing_service import TestingService
    print(f"\n{'='*60}\n  DevPilot Test Execution (Phase 7)\n{'='*60}\n")
    ws = Path(workspace_root)
    if not ws.is_dir():
        print(f"  [!] Workspace not found: {workspace_root}")
        return
    agent = TestAgent()
    inp = TestAgentInput(workspace_id=workspace_id, workspace_root=workspace_root, changed_files=changed_files or [])
    plan_output = await agent.execute(inp)
    if not plan_output.plan.steps:
        print("  [!] No test steps to execute")
        return
    for step in plan_output.plan.steps:
        step.timeout_seconds = timeout
    service = TestingService()
    result = await service.run_tests(plan=plan_output.plan)
    print(f"  Status: {result.status.value}")
    print(f"  Commands: {result.commands_passed}/{result.commands_total} passed")
    print(f"\n{'='*60}\n")


async def run_repair_diagnose(run_file: str, patch_file: str | None = None, plan_file: str | None = None) -> None:
    """Diagnose test failures (Phase 8)."""
    import json
    from app.models.testing import TestRunResult
    from app.workflows.repair import RepairWorkflow
    print(f"\n{'='*60}\n  DevPilot Repair Diagnosis (Phase 8)\n{'='*60}\n")
    try:
        with open(run_file, "r") as f:
            test_result = TestRunResult(**json.load(f))
        workflow = RepairWorkflow()
        diagnoses = await workflow.diagnose(test_result=test_result)
        print(f"  Diagnoses: {len(diagnoses)}")
    except Exception as exc:
        print(f"  [!] Error: {exc}")


async def run_repair_workflow(workspace_root: str, workspace_id: str, run_file: str, patch_file: str | None = None, plan_file: str | None = None, max_attempts: int | None = None) -> None:
    """Execute bounded repair loop (Phase 8)."""
    import json
    from app.models.testing import TestRunResult
    from app.workflows.repair import RepairWorkflow
    print(f"\n{'='*60}\n  DevPilot Repair Workflow (Phase 8)\n{'='*60}\n")
    try:
        with open(run_file, "r") as f:
            test_result = TestRunResult(**json.load(f))
        workflow = RepairWorkflow()
        result = await workflow.run(workspace_root=workspace_root, workspace_id=workspace_id, test_result=test_result, max_attempts=max_attempts)
        print(f"  Status: {result.status.value}")
        print(f"  Attempts: {result.attempts}")
    except Exception as exc:
        print(f"  [!] Error: {exc}")


async def run_review(workspace_id: str = "review-ws", plan_file: str | None = None, requirements_file: str | None = None, patch_file: str | None = None, run_file: str | None = None, repair_file: str | None = None, use_llm: bool = False, verbose: bool = False) -> None:
    """Execute Phase 9 review workflow."""
    import json
    from app.workflows.review import ReviewWorkflow
    print(f"\n{'='*60}\n  DevPilot Review (Phase 9)\n{'='*60}\n")
    try:
        workflow = ReviewWorkflow()
        report, gate = await workflow.run(workspace_id=workspace_id, use_llm=use_llm)
        print(f"  Decision: {gate.decision.value.upper()}")
        if gate.score is not None:
            print(f"  Score: {gate.score:.1f}")
        print(f"  Findings: {len(report.findings)}")
    except Exception as exc:
        print(f"  [!] Error: {exc}")


async def run_github_info(url: str) -> None:
    """Fetch and display GitHub repository metadata."""
    from app.services.github import GitHubService
    print(f"\n{'='*60}\n  DevPilot GitHub Repository Info\n{'='*60}\n  URL: {url}\n{'='*60}\n")
    try:
        parsed = GitHubService().parse_repo_url(url)
        metadata = await GitHubService().get_repo_metadata(parsed[0], parsed[1])
        print(f"  {metadata.full_name}\n  Description: {metadata.description or '(none)'}")
        print(f"  ⭐ {metadata.stargazers_count}  🍴 {metadata.forks_count}")
        print(f"\n{'='*60}\n")
    except Exception as exc:
        print(f"  ❌ Error: {exc}")


async def run_db_check() -> None:
    """Check PostgreSQL database connectivity."""
    from app.db.database import check_database_connection, verify_database_config
    from app.config import settings

    print(f"\n{'='*60}")
    print(f"  DevPilot Database Check")
    print(f"{'='*60}\n")

    db_url = settings.DATABASE_URL
    test_url = settings.TEST_DATABASE_URL

    if not db_url:
        print("  DATABASE_URL:  NOT CONFIGURED")
        print("  Set DATABASE_URL in .env to enable PostgreSQL.")
        print(f"\n{'='*60}\n")
        return

    # Show configuration (redacted)
    from app.db.database import redact_url
    print(f"  DATABASE_URL:    {redact_url(db_url)}")
    if test_url:
        safe_test = redact_url(test_url)
        print(f"  TEST_DATABASE_URL: {safe_test}")
    else:
        print(f"  TEST_DATABASE_URL: NOT CONFIGURED")
    print()

    # Check connection
    check = await check_database_connection(database_url=db_url)
    print(f"  Configuration: {'OK' if check.configured else 'MISSING'}")
    print(f"  Server:        {'Reachable' if check.connected else 'UNREACHABLE'}")
    if check.connected:
        print(f"  Database:      {check.database_name}")
        print(f"  Server Version: {check.server_version}")
        print(f"  Connection:    OK")
        print(f"  SELECT 1:      OK")
    elif check.error:
        print(f"  Error:         {check.error[:200]}")
    print(f"\n{'='*60}\n")


async def run_verify_persistence() -> None:
    """Run comprehensive persistence verification (Phase 11H)."""
    from app.config import settings
    from app.db.database import check_database_connection, redact_url
    from app.core.logging import logger
    import subprocess

    print(f"\n{'='*60}")
    print(f"  DevPilot Persistence Verification")
    print(f"{'='*60}\n")

    results: list[dict] = []

    def report(name: str, passed: bool, detail: str = "") -> None:
        status = "✓ PASS" if passed else "✗ FAIL"
        results.append({"name": name, "passed": passed, "detail": detail})
        print(f"  [{status}] {name}")
        if detail:
            print(f"         {detail}")

    # 1. Configuration check
    db_url = settings.DATABASE_URL
    test_url = settings.TEST_DATABASE_URL
    report("DATABASE_URL configured", bool(db_url),
           redact_url(db_url) if db_url else "Not set")
    report("TEST_DATABASE_URL configured", bool(test_url),
           redact_url(test_url) if test_url else "Not set (optional)")

    # 2. PostgreSQL connectivity
    if db_url:
        check = await check_database_connection(database_url=db_url)
        report("PostgreSQL connectivity", check.connected,
               f"{check.database_name} v{check.server_version}" if check.connected else check.error[:100])
    else:
        report("PostgreSQL connectivity", False, "Skipped — DATABASE_URL not configured")

    # 3. Alembic migration check
    try:
        import alembic.config
        alembic_cfg = alembic.config.Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
        alembic_cfg.set_main_option("script_location", str(Path(__file__).resolve().parent.parent / "alembic"))
        from alembic.command import current as alembic_current
        # Capture output
        from io import StringIO
        old_stdout = sys.stdout
        sys.stdout = StringIO()
        try:
            alembic_current(alembic_cfg)
            alembic_output = sys.stdout.getvalue()
            is_head = "head" in alembic_output.lower()
            report("Alembic current == head", is_head, alembic_output.strip()[:100])
        finally:
            sys.stdout = old_stdout
    except Exception as exc:
        report("Alembic current == head", False, str(exc)[:100])

    # 4. Expected tables
    if db_url and check.connected:
        try:
            from sqlalchemy import text
            from sqlalchemy.ext.asyncio import create_async_engine
            engine = create_async_engine(db_url)
            async with engine.connect() as conn:
                result = await conn.execute(text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' ORDER BY table_name"
                ))
                tables = [row[0] for row in result]
                expected = {"runs", "tasks", "repositories", "stage_results", "run_events", "artifacts"}
                found = set(tables)
                missing = expected - found
                report("Expected tables present", not missing,
                       f"Found: {sorted(found & expected)}, Missing: {sorted(missing)}" if missing else f"All {len(expected)} tables present")
                extra = found - expected
                if extra:
                    report("Unexpected tables absent", True, f"Ignored: {sorted(extra)}")
            await engine.dispose()
        except Exception as exc:
            report("Expected tables present", False, str(exc)[:100])
    else:
        report("Expected tables present", False, "Skipped — no DB connection")

    # 5. RunStore availability
    try:
        from app.services.run_store import InMemoryRunStore
        store = InMemoryRunStore()
        report("InMemoryRunStore available", True, "Instantiated successfully")
    except Exception as exc:
        report("InMemoryRunStore available", False, str(exc)[:100])

    try:
        from app.services.postgres_run_store import PostgresRunStore
        pstore = PostgresRunStore()
        report("PostgresRunStore available", True, "Instantiated successfully")
    except Exception as exc:
        report("PostgresRunStore available", False, str(exc)[:100])

    # 6. Transaction support
    if db_url and check.connected:
        try:
            from sqlalchemy import text
            engine = create_async_engine(db_url)
            async with engine.connect() as conn:
                result = await conn.execute(text("SHOW transaction_isolation"))
                isolation = result.scalar_one()
                report("Transaction support", True, f"Isolation level: {isolation}")
            await engine.dispose()
        except Exception as exc:
            report("Transaction support", False, str(exc)[:100])
    else:
        report("Transaction support", False, "Skipped — no DB connection")

    # Summary
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    failed = total - passed
    print(f"\n{'='*60}")
    print(f"  Results: {passed}/{total} passed, {failed} failed")
    print(f"  Overall: {'PASS' if failed == 0 else 'SOME CHECKS FAILED'}")
    print(f"{'='*60}\n")

    if failed > 0:
        sys.exit(1)


async def run_verify() -> None:
    """Run all safe local verification checks."""
    from app.config import settings
    from app.db.database import check_database_connection, redact_url
    import subprocess

    print(f"\n{'='*60}")
    print(f"  DevPilot Local Verification")
    print(f"{'='*60}\n")

    results: list[dict] = []

    def report(name: str, passed: bool, detail: str = "") -> None:
        status = "✓ PASS" if passed else "✗ FAIL"
        results.append({"name": name, "passed": passed, "detail": detail})
        print(f"  [{status}] {name}")
        if detail:
            print(f"         {detail}")

    # 1. Configuration checks
    report("DEBUG mode", settings.is_debug, str(settings.is_debug))
    report("LLM configured", bool(settings.OPENAI_API_KEY or settings.ANTHROPIC_API_KEY),
           "OpenAI" if settings.OPENAI_API_KEY else "Anthropic" if settings.ANTHROPIC_API_KEY else "None (agents unavailable)")

    # 2. Database check
    db_url = settings.DATABASE_URL
    if db_url:
        check = await check_database_connection(database_url=db_url)
        report("PostgreSQL connectivity", check.connected,
               f"{check.database_name} v{check.server_version}" if check.connected else check.error[:100])
    else:
        report("PostgreSQL connectivity", False, "DATABASE_URL not configured")

    # 3. Alembic migration
    import subprocess
    try:
        import sys
        from io import StringIO
        import alembic.config
        alembic_cfg = alembic.config.Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
        alembic_cfg.set_main_option("script_location", str(Path(__file__).resolve().parent.parent / "alembic"))
        from alembic.command import current as alembic_current
        old_stdout = sys.stdout
        sys.stdout = StringIO()
        alembic_current(alembic_cfg)
        out = sys.stdout.getvalue()
        sys.stdout = old_stdout
        is_head = "head" in out.lower()
        report("Alembic migration at head", is_head, out.strip()[:100])
    except Exception as exc:
        report("Alembic migration at head", False, str(exc)[:100])

    # Summary
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    failed = total - passed
    print(f"\n{'='*60}")
    print(f"  Results: {passed}/{total} passed, {failed} failed")
    print(f"  Overall: {'PASS' if failed == 0 else 'SOME CHECKS FAILED'}")
    print(f"{'='*60}\n")

    if failed > 0:
        sys.exit(1)


async def run_orchestration(repo: str, task: str, description: str = "", json_output: bool = False) -> None:
    """Execute end-to-end DevPilot pipeline (Phase 10)."""
    from app.services.orchestration_service import OrchestrationService
    from app.models.orchestration import RunSource, RunSourceType

    print(f"\n{'='*60}\n  DevPilot Run (Phase 10)\n{'='*60}")
    print(f"  Repository: {repo}")
    print(f"  Task:       {task[:80]}\n{'='*60}\n")

    is_github = repo.startswith("https://github.com/") or repo.startswith("github.com/")
    source = RunSource(
        source_type=RunSourceType.GITHUB_ISSUE if is_github else RunSourceType.USER_TASK,
        title=task,
        description=description,
        repository_path=repo,
    )

    orch = OrchestrationService()
    run = orch.create_run(source)
    print(f"  Run ID: {run.run_id}\n")

    result = await orch.execute_run(run_id=run.run_id, workspace_root=repo)

    if json_output:
        import json
        print(json.dumps(result.model_dump(mode="json", exclude_none=True), indent=2))
        return

    # Display stages
    print(f"  {'='*56}\n  Pipeline Progress\n  {'='*56}")
    stage_order = [
        "acquiring_repository", "analyzing_repository", "analyzing_task",
        "planning", "retrieving_context", "coding", "validating_patch",
        "applying_patch", "testing", "repairing", "reviewing", "quality_gate",
    ]
    for s_name in stage_order:
        found = [s for s in result.stages if s["stage"] == s_name]
        if found:
            s = found[0]
            icon = {"succeeded": "✓", "failed": "✗", "skipped": "○", "cancelled": "⊘"}.get(s.get("status", ""), "?")
            print(f"  {icon} {s_name:<22} {s.get('status', '?')}")
        else:
            print(f"  · {s_name:<22} pending")
    print()

    # Display final decision
    print(f"  {'='*56}\n  Decision: {result.status.value.upper()}\n  {'='*56}")

    if result.quality_gate:
        g = result.quality_gate
        if g.score is not None:
            print(f"  Score: {g.score:.1f}/100")
        print(f"  Requirements: {g.requirements_satisfied} satisfied, {g.requirements_unsatisfied} unsatisfied")
        print(f"  Verification: {g.verification_status}")
    if result.failure:
        print(f"\n  Failure: [{result.failure.code.value}] {result.failure.message[:200]}")
    if result.duration_seconds:
        print(f"\n  Duration: {result.duration_seconds:.2f}s")
    print(f"\n{'='*60}\n")


if __name__ == "__main__":
    main()
