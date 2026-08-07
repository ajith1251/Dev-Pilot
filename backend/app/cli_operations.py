"""
Phase 20B CLI commands — Operational Hardening.

Usage:
    python -m app.cli validate-config [--json]
    python -m app.cli ops-status [--json]
    python -m app.cli ops-metrics [--json]
"""

from __future__ import annotations

import asyncio
import json
import sys


def _ensure_utf8_stdout() -> None:
    try:
        sys.stdout.reconfigure(  # type: ignore[attr-defined]
            encoding="utf-8", errors="replace"
        )
    except (AttributeError, ValueError):
        pass


def add_cli_commands(parent_parser) -> None:
    """Add Phase 20B operational CLI commands to the argument parser."""
    subparsers = parent_parser  # Passed as subparsers from main cli

    v_parser = subparsers.add_parser(
        "validate-config", help="Validate configuration at startup (Phase 20B)"
    )
    v_parser.add_argument("--json", action="store_true", help="Output raw JSON")

    s_parser = subparsers.add_parser(
        "ops-status", help="Subsystem status matrix (Phase 20B)"
    )
    s_parser.add_argument("--json", action="store_true", help="Output raw JSON")

    m_parser = subparsers.add_parser(
        "ops-metrics", help="Runtime operational metrics (Phase 20B)"
    )
    m_parser.add_argument("--json", action="store_true", help="Output raw JSON")


def run_validate_config(json_output: bool = False) -> None:
    """Validate configuration and print findings."""
    _ensure_utf8_stdout()
    from app.core.startup_validation import validate_settings

    findings = validate_settings()
    errors = [f for f in findings if f["severity"] == "error"]
    warnings = [f for f in findings if f["severity"] == "warning"]
    if json_output:
        print(json.dumps({
            "error_count": len(errors),
            "warning_count": len(warnings),
            "findings": findings,
        }, indent=2, default=str))
        return
    print("\n" + "=" * 60)
    print("  DevPilot Configuration Validation (Phase 20B)")
    print("=" * 60)
    if not findings:
        print("  [OK] No configuration problems detected.")
    for f in findings:
        mark = "ERROR" if f["severity"] == "error" else "warn "
        print(f"  [{mark}] {f['code']}")
        print(f"         {f['message']}")
    print(f"\n  {len(errors)} error(s), {len(warnings)} warning(s)")
    print("=" * 60)


def run_ops_status(json_output: bool = False) -> None:
    """Show the subsystem status matrix."""
    _ensure_utf8_stdout()
    from app.services.subsystem_status import build_subsystem_status

    payload = asyncio.run(build_subsystem_status())
    if json_output:
        print(json.dumps(payload, indent=2, default=str))
        return
    print("\n" + "=" * 60)
    print("  DevPilot Subsystem Status (Phase 20B)")
    print("=" * 60)
    summary = payload.get("summary", {})
    print(f"  Ready: {summary.get('ready')}   Status: {summary.get('status')}")
    if summary.get("error_subsystems"):
        print(f"  Errors: {summary['error_subsystems']}")
    print()
    for name, sub in payload.get("subsystems", {}).items():
        detail = sub.get("detail", {})
        brief = ""
        if isinstance(detail, dict):
            if "connected" in detail:
                brief = f"connected={detail.get('connected')}"
            elif "active_provider" in detail:
                brief = f"active={detail.get('active_provider')} configured={detail.get('configured_count')}"
            elif "active_runs" in detail:
                brief = f"active_runs={detail.get('active_runs')}"
            elif "active_connections" in detail:
                brief = f"ws={detail.get('active_connections')}"
            elif "memory_mb" in detail:
                brief = f"memory={detail.get('memory_mb')}MB tasks={detail.get('open_tasks')}"
            elif "version" in detail:
                brief = f"version={detail.get('version')}"
        print(f"  {name:<18} {sub.get('status', '?'):<8} {brief}")
    print("=" * 60)


def run_ops_metrics(json_output: bool = False) -> None:
    """Show runtime operational metrics."""
    _ensure_utf8_stdout()
    from app.services.system_metrics import get_system_metrics

    payload = get_system_metrics().snapshot()
    if json_output:
        print(json.dumps(payload, indent=2, default=str))
        return
    print("\n" + "=" * 60)
    print("  DevPilot Runtime Metrics (Phase 20B)")
    print("=" * 60)
    print(f"  Uptime:          {payload['uptime_seconds']:.0f}s")
    runs = payload["runs"]
    print(f"  Runs:            {runs['active']} active, "
          f"{runs['completed_total']} completed, "
          f"{runs['throughput_per_minute']:.2f}/min")
    repo = payload["repositories"]
    print(f"  Repositories:    {repo['processed_total']} processed, "
          f"avg {repo['avg_processing_seconds']}s")
    aut = payload["autonomy"]
    print(f"  Autonomy:        {aut['active_goals']} active, "
          f"{aut['goals_total']} total")
    prov = payload["providers"]
    if prov.get("totals"):
        t = prov["totals"]
        print(f"  Providers:       {t.get('total_requests', 0)} calls, "
              f"{t.get('failovers', 0)} failovers, "
              f"{t.get('retries', 0)} retries, "
              f"{t.get('recoveries', 0)} recoveries")
    res = payload["resources"]
    print(f"  Resources:       memory={res['memory_mb']}MB, "
          f"ws={res['active_ws_connections']}, "
          f"tasks={res['open_tasks']}")
    print("=" * 60)
