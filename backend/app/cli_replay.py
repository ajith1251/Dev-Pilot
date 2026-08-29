"""
Phase 21 CLI commands for Run Replay & Deterministic Reproduction.

Usage:
    python -m app.cli replay-manifest <run_id>
    python -m app.cli replay <run_id> --mode exact|deterministic [--workspace PATH]
    python -m app.cli replay-compare <run_id> <other_run_id>
    python -m app.cli replay-audit <run_id>
    python -m app.cli replays <run_id>
"""

from __future__ import annotations

import sys


def _ensure_utf8_stdout() -> None:
    """Force UTF-8 stdout so manifest/decision content cannot crash the CLI
    on a Windows cp1252 console."""
    try:
        sys.stdout.reconfigure(  # type: ignore[attr-defined]
            encoding="utf-8", errors="replace"
        )
    except (AttributeError, ValueError):
        pass


def add_cli_commands(parent_parser) -> None:
    """Add Phase 21 replay CLI commands to the argument parser."""
    subparsers = parent_parser

    manifest_parser = subparsers.add_parser(
        "replay-manifest", help="Build/print the replay manifest for a run (Phase 21)"
    )
    manifest_parser.add_argument("run_id", type=str, help="Run ID, e.g. RUN-ABC123")
    manifest_parser.add_argument("--json", action="store_true", help="Output raw JSON")

    replay_parser = subparsers.add_parser(
        "replay", help="Replay a run deterministically (Phase 21)"
    )
    replay_parser.add_argument("run_id", type=str, help="Run ID")
    replay_parser.add_argument(
        "--mode", type=str, default="exact",
        choices=["exact", "deterministic"],
        help="exact = offline deterministic checks; deterministic = + workspace verification",
    )
    replay_parser.add_argument(
        "--workspace", type=str, default=None,
        help="Workspace path for deterministic mode",
    )
    replay_parser.add_argument("--json", action="store_true", help="Output raw JSON")

    compare_parser = subparsers.add_parser(
        "replay-compare", help="Compare two runs stage by stage (Phase 21)"
    )
    compare_parser.add_argument("run_id", type=str, help="Run ID A")
    compare_parser.add_argument("other_run_id", type=str, help="Run ID B")
    compare_parser.add_argument("--json", action="store_true", help="Output raw JSON")

    audit_parser = subparsers.add_parser(
        "replay-audit", help="Full no-LLM audit of a run (Phase 21)"
    )
    audit_parser.add_argument("run_id", type=str, help="Run ID")
    audit_parser.add_argument("--json", action="store_true", help="Output raw JSON")

    replays_parser = subparsers.add_parser(
        "replays", help="Show replay history for a run (Phase 21)"
    )
    replays_parser.add_argument("run_id", type=str, help="Run ID")
    replays_parser.add_argument("--json", action="store_true", help="Output raw JSON")


async def run_replay_manifest(run_id: str, json_output: bool = False) -> None:
    _ensure_utf8_stdout()
    from app.services.replay_service import ReplayService

    svc = ReplayService()
    run = await svc.get_run(run_id)
    if run is None:
        print(f"\n  Run {run_id} not found.\n")
        return
    manifest = await svc.build_manifest(run)

    if json_output:
        import json
        print(json.dumps(manifest.model_dump(mode="json"), indent=2, default=str))
        return

    s = manifest.summary()
    print(f"\n{'='*60}")
    print(f"  Run: {run_id} - Replay Manifest ({s['manifest_id']})")
    print(f"{'='*60}\n")
    print(f"  Status:      {s['source_run_status']}")
    print(f"  Repository:  {s['repository_state']['path'][:80]}")
    print(f"  Fingerprint: {s['repository_state']['fingerprint']}")
    print(f"  Stages:      {s['stage_count']}")
    print(f"  Decisions:   {s['decision_count']}")
    print(f"  Handoffs:    {s['handoffs']}")
    print(f"  Consensus:   {s['consensus']}  Contradictions: {s['contradictions']}")
    print(f"  Content:     {s['content_hash']}\n")

    print("  Stage records:")
    for st in manifest.stages:
        icon = {"deterministic": "D", "llm_proposed": "L", "observational": "O"}.get(
            st.kind.value, "?"
        )
        print(
            f"    [{icon}] {st.stage:<22} {st.status:<10} "
            f"hash={st.output_hash[:16]} captured={st.captured}"
        )

    print("\n  Deterministic decisions:")
    for d in manifest.deterministic_decisions:
        print(
            f"    - {d.decision_type:<18} {d.value[:60]} "
            f"(replayable={d.replayable})"
        )
    print(f"{'='*60}\n")


def _verdict_exit_code(verdict: str) -> int:
    """CI-friendly exit codes: 0 = MATCH, 1 = DRIFT/INCOMPLETE, 2 = INVALID."""
    if verdict == "match":
        return 0
    if verdict in ("drift", "incomplete"):
        return 1
    return 2


async def run_replay(
    run_id: str, mode: str = "exact", workspace: str | None = None,
    json_output: bool = False,
) -> int:
    """Run a replay. Returns a process exit code (0 match, 1 drift/incomplete,
    2 invalid) so CI gates can fail on divergence."""
    _ensure_utf8_stdout()
    from app.models.replay import ReplayMode
    from app.services.replay_service import ReplayService

    result = await ReplayService().replay(
        run_id=run_id,
        mode=ReplayMode(mode),
        workspace=workspace,
    )
    exit_code = _verdict_exit_code(result.verdict.value)

    if json_output:
        import json
        print(json.dumps(result.model_dump(mode="json"), indent=2, default=str))
        return exit_code

    s = result.summary_dict()
    print(f"\n{'='*60}")
    print(f"  Replay: {s['replay_id']} - {mode.upper()} mode")
    print(f"  Run:    {run_id}")
    print(f"  Verdict: {s['verdict'].upper()}")
    print(f"{'='*60}\n")
    print(f"  Checks: {s['checks_passed']}/{s['checks_total']} passed, "
          f"{s['checks_failed']} failed, {s['checks_skipped']} skipped, "
          f"{s['checks_not_replayable']} not replayable")

    for c in result.checks:
        icon = {"passed": "PASS", "failed": "FAIL", "skipped": "skip",
                "not_replayable": "n/a"}.get(c.status.value, "?")
        print(f"    [{icon}] {c.check:<28} {c.stage}")
        if c.status.value in ("failed", "not_replayable") or c.note:
            print(f"           expected: {c.expected[:100]}")
            print(f"           actual:   {c.actual[:100]}")
            if c.note:
                print(f"           note:     {c.note[:120]}")

    print(f"\n  Summary: {s['summary']}")
    if result.divergences:
        print("\n  Divergences:")
        for d in result.divergences[:10]:
            print(f"    - {d[:180]}")
    print(f"{'='*60}\n")
    return exit_code


async def run_replay_compare(
    run_id: str, other_run_id: str, json_output: bool = False,
) -> int:
    _ensure_utf8_stdout()
    from app.models.replay import ReplayMode
    from app.services.replay_service import ReplayService

    result = await ReplayService().replay(
        run_id=run_id,
        mode=ReplayMode.COMPARE,
        other_run_id=other_run_id,
    )
    exit_code = _verdict_exit_code(result.verdict.value)

    if json_output:
        import json
        print(json.dumps(result.model_dump(mode="json"), indent=2, default=str))
        return exit_code

    print(f"\n{'='*60}")
    print(f"  COMPARE {run_id} vs {other_run_id}")
    print(f"  Verdict: {result.verdict.value.upper()}")
    print(f"{'='*60}\n")

    for c in result.stage_comparisons:
        icon = {True: "=", False: "!=", None: "?"}.get(c.matched, "?")
        print(
            f"    [{icon}] {c.stage:<24} A={c.recorded_hash[:16]} "
            f"B={c.replay_hash[:16]}  {c.detail[:60]}"
        )
    if result.divergences:
        print("\n  Divergences / diverging decisions:")
        for d in result.divergences[:10]:
            print(f"    - {d[:180]}")
    print(f"{'='*60}\n")
    return exit_code


async def run_replay_audit(run_id: str, json_output: bool = False) -> None:
    _ensure_utf8_stdout()
    from app.services.replay_service import ReplayService

    audit = await ReplayService().audit(run_id)
    if not audit.get("available"):
        print(f"\n  Run {run_id} not found — cannot audit.\n")
        return

    if json_output:
        import json
        print(json.dumps(audit, indent=2, default=str))
        return

    print(f"\n{'='*60}")
    print(f"  Audit: {run_id} - verdict {audit['verdict'].upper()}")
    print(f"{'='*60}\n")
    m = audit["manifest"]
    print(f"  Manifest: {m['manifest_id']} ({m['stage_count']} stages, "
          f"{m['decision_count']} decisions)")
    print(f"  Repository: {m['repository_state']['path'][:80]}")
    print(f"  Fingerprint: {m['repository_state']['fingerprint']}")

    print("\n  Deterministic decisions (replay outcomes):")
    for d in audit["deterministic_decisions"]:
        matched = d.get("matched")
        icon = {True: "REPLAYED OK", False: "DIVERGED", None: "recorded"}.get(
            matched, "recorded"
        )
        print(f"    [{icon:<12}] {d['decision_type']:<18} {d['value'][:60]}")

    print(f"\n  Checks: {audit['replay']['checks_passed']}/"
          f"{audit['replay']['checks_total']} passed")
    for c in audit["checks"]:
        icon = {"passed": "PASS", "failed": "FAIL", "skipped": "skip",
                "not_replayable": "n/a"}.get(c["status"], "?")
        print(f"    [{icon}] {c['check']:<28} {c['stage']}")
    if audit["divergences"]:
        print("\n  Divergences:")
        for d in audit["divergences"][:10]:
            print(f"    - {d[:180]}")
    print(f"{'='*60}\n")


async def run_replays(run_id: str, json_output: bool = False) -> None:
    _ensure_utf8_stdout()
    from app.services.replay_service import ReplayService

    replays = await ReplayService().list_replays(run_id=run_id)

    if json_output:
        import json
        print(json.dumps([r.summary_dict() for r in replays], indent=2, default=str))
        return

    print(f"\n{'='*60}")
    print(f"  Replay history for {run_id} ({len(replays)})")
    print(f"{'='*60}\n")
    if not replays:
        print("  No replays recorded for this run.")
    for r in replays:
        s = r.summary_dict()
        print(
            f"    {s['replay_id']}  mode={s['mode']:<12} verdict={s['verdict']:<10} "
            f"checks={s['checks_passed']}/{s['checks_total']}  {s['created_at']}"
        )
    print(f"{'='*60}\n")
