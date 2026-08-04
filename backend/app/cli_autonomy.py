"""
Phase 16 CLI commands for Autonomous Execution.

Usage:
    python -m app.cli autonomous-run <repo> "<task>"
    python -m app.cli autonomous-status <goal_id>
    python -m app.cli autonomous-dry-run <repo> "<task>"
    python -m app.cli autonomous-pause <goal_id>
    python -m app.cli autonomous-resume <goal_id>
    python -m app.cli autonomous-cancel <goal_id>
"""

from __future__ import annotations

import asyncio
import json


def add_cli_commands(parent_parser) -> None:
    """Add Phase 16 autonomy CLI commands to the argument parser."""
    subparsers = parent_parser

    run_parser = subparsers.add_parser(
        "autonomous-run", help="Run an autonomous goal (Phase 16)"
    )
    run_parser.add_argument("repo", type=str, help="Repository path")
    run_parser.add_argument("task", type=str, help="Task / goal text")
    run_parser.add_argument("--criteria", type=str, default=None,
                            help="Comma-separated acceptance criteria")
    run_parser.add_argument("--max-iterations", type=int, default=None)
    run_parser.add_argument("--max-replans", type=int, default=None)
    run_parser.add_argument("--json", action="store_true", help="Output raw JSON")

    status_parser = subparsers.add_parser(
        "autonomous-status", help="Show autonomous goal status (Phase 16)"
    )
    status_parser.add_argument("goal_id", type=str, help="Goal ID, e.g. GOAL-ABC123")
    status_parser.add_argument("--json", action="store_true", help="Output raw JSON")

    dry_parser = subparsers.add_parser(
        "autonomous-dry-run", help="Estimate an autonomous run without mutations (Phase 16)"
    )
    dry_parser.add_argument("repo", type=str, help="Repository path")
    dry_parser.add_argument("task", type=str, help="Task / goal text")
    dry_parser.add_argument("--criteria", type=str, default=None)
    dry_parser.add_argument("--json", action="store_true", help="Output raw JSON")

    for name, help_text in (
        ("autonomous-pause", "Pause an autonomous goal (Phase 16)"),
        ("autonomous-resume", "Resume an autonomous goal (Phase 16)"),
        ("autonomous-cancel", "Cancel an autonomous goal (Phase 16)"),
    ):
        p = subparsers.add_parser(name, help=help_text)
        p.add_argument("goal_id", type=str, help="Goal ID")


async def run_autonomous_run(
    repo: str,
    task: str,
    criteria: str | None = None,
    max_iterations: int | None = None,
    max_replans: int | None = None,
    json_output: bool = False,
) -> None:
    """Run a bounded autonomous goal."""
    from app.services.autonomy_service import AutonomousExecutionController
    from app.models.autonomy import ExecutionBudget

    budget = None
    if max_iterations or max_replans:
        budget = ExecutionBudget()
        if max_iterations:
            budget.max_iterations = max_iterations
        if max_replans:
            budget.max_replans = max_replans

    criteria_list = [c.strip() for c in criteria.split(",") if c.strip()] if criteria else None

    controller = AutonomousExecutionController()
    state = await controller.create_goal(
        task=task,
        repository=repo,
        criteria_texts=criteria_list,
        budget=budget,
    )
    await controller.start(state.goal_id)

    if json_output:
        print(json.dumps(state.status_summary(), indent=2, default=str))
        return

    _print_status(state)


async def run_autonomous_status(goal_id: str, json_output: bool = False) -> None:
    from app.services.autonomy_service import AutonomousExecutionController

    controller = AutonomousExecutionController()
    try:
        state = await controller.recover(goal_id)
    except KeyError:
        print(f"Goal {goal_id} not found.")
        return
    if json_output:
        print(json.dumps(state.status_summary(), indent=2, default=str))
        return
    _print_status(state)


async def run_autonomous_dry_run(repo: str, task: str, criteria: str | None = None,
                                 json_output: bool = False) -> None:
    from app.services.autonomy_service import AutonomousExecutionController

    criteria_list = [c.strip() for c in criteria.split(",") if c.strip()] if criteria else None
    controller = AutonomousExecutionController()
    report = await controller.dry_run(task=task, repository=repo, criteria_texts=criteria_list)
    if json_output:
        print(json.dumps(report.summary(), indent=2, default=str))
        return
    print(f"DRY RUN — {task}")
    print(f"  Repository:  {report.repository or '(not specified)'}")
    print(f"  Criteria:    {len(report.extracted_criteria)}")
    for c in report.extracted_criteria:
        print(f"    - [{c['type']}] {c['description']}")
    print(f"  Budget:      {json.dumps(report.estimated_budget)}")
    print(f"  Workflow:    {' -> '.join(report.likely_workflow)}")
    print(f"  Feasibility: {report.feasibility}")
    for w in report.warnings:
        print(f"  [warn] {w}")


async def run_autonomous_control(action: str, goal_id: str) -> None:
    from app.services.autonomy_service import AutonomousExecutionController

    controller = AutonomousExecutionController()
    try:
        if action == "pause":
            state = await controller.pause(goal_id)
        elif action == "resume":
            state = await controller.resume(goal_id)
        else:
            state = await controller.cancel(goal_id)
    except KeyError:
        print(f"Goal {goal_id} not found.")
        return
    print(f"Goal {goal_id} → {state.state.value}")
    if state.escalations:
        latest = state.escalations[-1]
        print(f"  Escalation ({latest.reason.value}): {latest.needed_input[:200]}")


def _print_status(state) -> None:
    summary = state.status_summary()
    goal = summary["goal"]
    budget = summary["budget"]
    print(f"GOAL: {goal['task']}")
    print(f"State: {summary['state']}")
    print(f"Goal status: {goal['status']}")
    print(f"Iteration: {budget['usage']['iterations']} / {budget['limits']['max_iterations']}")
    print(f"Replans: {budget['usage']['replans']} / {budget['limits']['max_replans']}")
    print(f"Repairs: {budget['usage']['repairs']} / {budget['limits']['max_repairs']}")
    progress = goal["progress"]
    print(f"Criteria: {progress['criteria_satisfied']} / {progress['criteria_total']} satisfied")
    print(f"  satisfied:   {progress['criteria_satisfied']}")
    print(f"  unsatisfied: {progress['criteria_unsatisfied']}")
    print(f"  unknown:     {progress['criteria_unknown']}")
    print(f"Trend: {progress['trend']}")
    print(f"Plan versions: {len(summary['plan_versions'])}")
    for pv in summary["plan_versions"]:
        print(f"  v{pv['version']} [{pv['status']}] {pv['plan_summary'][:70]}")
    latest = summary["latest_decision"]
    if latest:
        print(f"Next action: {latest['action']} ({latest['reason_code']})")
        print(f"  Rationale: {latest['rationale']}")
    if summary["escalations"]:
        for e in summary["escalations"]:
            print(f"Escalation ({e['reason']}): {e['needed_input'][:120]}")
