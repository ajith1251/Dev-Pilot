"""
Durability live report — the `--json`-equivalent for the pytest live class.

Runs the SAME two live HTTP paths as
``tests/test_api_durability.py::TestLiveApiDurability`` — one real
``execute_run`` (``POST /api/v1/runs``) AND one real autonomous goal loop
(``POST /api/v1/autonomy/run``) against a production LLM provider + a
test-named PostgreSQL — and emits the structured summary in the exact shape
``scripts/verify_api_durability.py --json`` produced:

    {
      "mode": "live",
      "run_api":  {run_id, run_status, handoffs, decisions, consensus_via_api,
                   consensus_recovered, runs_in_table},
      "goal_api": {goal_id, goal_state, goal_runs, goal_run_statuses,
                   goal_latest_run_status, goal_handoffs, goal_decisions,
                   goal_consensus, goal_recovered}
    }

Reuses the exact helpers from ``scripts/verify_api_durability.py``
(``build_wired_stack`` / ``run_live_http_execute`` / ``run_live_http_goal``
/ ``TERMINAL_STATUSES`` / ``TERMINAL_GOAL_STATES`` / ``pick_database_url``
/ ``ensure_schema`` / ``check_live_mode``), so the pytest coverage and this
report are the same code — never a parallel reimplementation.

Usage:
    python scripts/durability_report.py                 # JSON to stdout
    python scripts/durability_report.py --out report.json

Skip policy (CI-safe): with no live provider or no test-named PostgreSQL the
script prints ``{"mode": "skipped", ...}`` and exits 0 — CI stays green with
no API keys. On a full run it applies the same terminal gates as the
``live-llm-e2e`` job and exits 1 unless the run reached a terminal verdict
(approved/rejected/needs_human_review) AND the goal a terminal state with
its newest run terminal.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = _BACKEND_DIR / "scripts"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# Reuse the exact helpers (imports app.main at module scope — same as the
# pytest class does via `import verify_api_durability as vd`).
import verify_api_durability as vd  # noqa: E402

FIXTURE_AUTH_APP = _BACKEND_DIR / "tests" / "fixtures" / "fixture_auth_app"


def _drive_live(db_url: str) -> Dict[str, Any]:
    """Run both live HTTP paths once; return the run_api/goal_api summary.

    Mirrors ``TestLiveApiDurability.artifacts`` exactly: the wired stack is
    built once, both HTTP paths run against the same PostgresRunStore /
    collaboration / reasoning / autonomy instances the gates inspect, and
    the API singletons are restored afterwards. Engines are disposed so no
    pool leaks across the process.
    """
    import app.api.v1.autonomy as autonomy_api
    import app.api.v1.orchestration as orch_api
    import app.api.v1.reasoning as reasoning_api

    saved = (
        getattr(orch_api, "workflow", None),
        getattr(reasoning_api, "_service", None),
        getattr(autonomy_api, "_service", None),
    )

    async def _drive() -> Dict[str, Any]:
        ctrl, orch, collab, session_factory, run_store, reasoning = (
            vd.build_wired_stack(db_url, live=True)
        )
        try:
            run_result = await vd.run_live_http_execute(
                orch, collab, run_store, reasoning, session_factory,
                str(FIXTURE_AUTH_APP),
            )
            goal_result = await vd.run_live_http_goal(
                ctrl, orch, collab, run_store, reasoning, session_factory,
                str(FIXTURE_AUTH_APP),
            )
            return {"run_api": run_result, "goal_api": goal_result}
        finally:
            await vd._dispose_stack_engines(session_factory, collab)

    try:
        return asyncio.run(_drive())
    finally:
        orch_api.workflow, reasoning_api._service, autonomy_api._service = saved


def _apply_gates(payload: Dict[str, Any]) -> None:
    """Apply the live-llm-e2e terminal gates, recording failures in payload."""
    run = payload["run_api"]
    goal = payload["goal_api"]
    failed: list = []

    if run["run_status"] not in vd.TERMINAL_STATUSES:
        failed.append(
            f"run API: status='{run['run_status']}' is not a terminal verdict "
            "(approved/rejected/needs_human_review)")
    if goal["goal_state"] not in vd.TERMINAL_GOAL_STATES:
        failed.append(
            f"goal API: state='{goal['goal_state']}' is not a terminal goal "
            "state (completed/stopped/waiting_for_human)")
    if not goal["goal_runs"]:
        failed.append("goal API: no runs persisted for the live goal")
    if (goal.get("goal_latest_run_status")
            not in vd.TERMINAL_STATUSES):
        failed.append(
            f"goal API: the goal's newest run did not reach a terminal "
            f"verdict (latest={goal.get('goal_latest_run_status')})")

    payload["gates"] = failed
    payload["passed"] = not failed


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the live durability HTTP paths and emit the "
                    "run_api/goal_api JSON summary (verify_api_durability "
                    "--json equivalent for the pytest live class).",
    )
    parser.add_argument(
        "--out", metavar="FILE", default=None,
        help="write the JSON report to FILE (also printed to stdout)",
    )
    args = parser.parse_args(argv)

    # All helper chatter (check_live_mode / pick_database_url / ensure_schema
    # / run_live_http_*) is diagnostic — send it to stderr so stdout carries
    # ONLY the JSON document (machine-readable contract).
    report: Dict[str, Any]
    exit_code: int
    with contextlib.redirect_stdout(sys.stderr):
        if not vd.check_live_mode():
            report = {"mode": "skipped",
                      "reason": "no live LLM provider configured "
                                "(DEVPILOT_LLM_PROVIDER + matching API key)"}
            exit_code = 0
        else:
            db_url = vd.pick_database_url()
            if not db_url:
                report = {"mode": "skipped",
                          "reason": "no test-named PostgreSQL "
                                    "(set TEST_DATABASE_URL)"}
                exit_code = 0
            elif not vd.ensure_schema(db_url):
                report = {"mode": "skipped",
                          "reason": "alembic upgrade head failed — cannot "
                                    "run live durability paths"}
                exit_code = 0
            else:
                try:
                    payload: Dict[str, Any] = {"mode": "live"}
                    payload.update(_drive_live(db_url))
                    _apply_gates(payload)
                    report = payload
                    exit_code = 0 if payload["passed"] else 1
                except Exception as exc:  # noqa: BLE001 — report contract
                    # A live crash must still emit a JSON document so a
                    # machine consumer never receives empty stdout.
                    report = {"mode": "error",
                              "error": f"{type(exc).__name__}: {exc}"}
                    exit_code = 1

    print(json.dumps(report, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2),
                                  encoding="utf-8")
        print(f"  [ok] report written to {args.out}", file=sys.stderr)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
