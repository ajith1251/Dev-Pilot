"""
Phase 19B CLI commands — Multi-Provider Failover & Reliability.

Usage:
    python -m app.cli providers
    python -m app.cli provider-health
    python -m app.cli provider-metrics
    python -m app.cli provider-test [--message ...] [--model ...]
"""

from __future__ import annotations

import asyncio
import json
import sys


def _ensure_utf8_stdout() -> None:
    """Force UTF-8 stdout so router payloads cannot crash the CLI on a
    Windows cp1252 console."""
    try:
        sys.stdout.reconfigure(  # type: ignore[attr-defined]
            encoding="utf-8", errors="replace"
        )
    except (AttributeError, ValueError):
        pass


def add_cli_commands(parent_parser) -> None:
    """Add Phase 19B provider CLI commands to the argument parser."""
    subparsers = parent_parser  # Passed as subparsers from main cli

    p_parser = subparsers.add_parser(
        "providers", help="Provider router overview (Phase 19B)"
    )
    p_parser.add_argument("--json", action="store_true", help="Output raw JSON")

    h_parser = subparsers.add_parser(
        "provider-health", help="Provider health snapshot (Phase 19B)"
    )
    h_parser.add_argument("--json", action="store_true", help="Output raw JSON")

    m_parser = subparsers.add_parser(
        "provider-metrics", help="Provider router metrics (Phase 19B)"
    )
    m_parser.add_argument("--json", action="store_true", help="Output raw JSON")

    t_parser = subparsers.add_parser(
        "provider-test", help="Route one benign test call through the router"
    )
    t_parser.add_argument("--message", type=str, default=None,
                          help="Test prompt (default: 'Reply with exactly: provider-ok')")
    t_parser.add_argument("--model", type=str, default=None, help="Override model")
    t_parser.add_argument("--json", action="store_true", help="Output raw JSON")


def _get_router():
    from app.llm.router import get_router

    return get_router()


def run_providers(json_output: bool = False) -> None:
    """Show configured providers, priority and active provider."""
    _ensure_utf8_stdout()
    r = _get_router()
    payload = {
        "routing_enabled": bool(r._settings.PROVIDER_ROUTING_ENABLED),
        "active_provider": r.active_provider,
        "priority": r._priority(),
        "providers": r.provider_snapshots(),
    }
    if json_output:
        print(json.dumps(payload, indent=2, default=str))
        return
    print("\n" + "=" * 60)
    print("  DevPilot Provider Router (Phase 19B)")
    print("=" * 60)
    print(f"  Routing: {'ENABLED' if payload['routing_enabled'] else 'DISABLED'}")
    print(f"  Active:  {payload['active_provider']}")
    print(f"  Priority: {', '.join(payload['priority'])}")
    print()
    print(f"  {'provider':<14} {'configured':<11} {'model'}")
    for p in payload["providers"]:
        print(
            f"  {p['name']:<14} {str(p['configured']):<11} {p.get('model') or '-'}"
        )
    print("=" * 60)


def run_provider_health(json_output: bool = False) -> None:
    """Show per-provider health snapshot."""
    _ensure_utf8_stdout()
    r = _get_router()
    payload = r.health_snapshot()
    if json_output:
        print(json.dumps(payload, indent=2, default=str))
        return
    print("\n" + "=" * 60)
    print("  DevPilot Provider Health (Phase 19B)")
    print("=" * 60)
    for entry in payload.get("providers", []):
        print(f"  {entry['name']:<14} {entry.get('status', '?'):<10} "
              f"circuit={entry.get('circuit_state', '?'):<8} "
              f"ok={entry.get('success_rate', 0):.0%} "
              f"latency={entry.get('avg_latency_ms', 0):.1f}ms")
    print("=" * 60)


def run_provider_metrics(json_output: bool = False) -> None:
    """Show router metrics and failover events."""
    _ensure_utf8_stdout()
    r = _get_router()
    payload = r.metrics_snapshot()
    if json_output:
        print(json.dumps(payload, indent=2, default=str))
        return
    print("\n" + "=" * 60)
    print("  DevPilot Provider Metrics (Phase 19B)")
    print("=" * 60)
    total = payload.get("totals", {})
    print(f"  Calls:        {total.get('total_calls', 0)}")
    print(f"  Successes:    {total.get('total_successes', 0)}")
    print(f"  Failures:     {total.get('total_failures', 0)}")
    print(f"  Retries:      {total.get('total_retries', 0)}")
    print(f"  Failovers:    {total.get('total_failovers', 0)}")
    print()
    for name, m in payload.get("per_provider", {}).items():
        print(f"  {name:<14} calls={m.get('calls', 0):<5} "
              f"ok={m.get('successes', 0):<5} fail={m.get('failures', 0)}")
    events = payload.get("failover_events", [])
    if events:
        print()
        print("  Recent failover events:")
        for ev in events[-5:]:
            print(f"    {ev.get('at', '?')[:19]} "
                  f"{ev.get('failed_provider', '?')} -> {ev.get('fallback_provider', '?')} "
                  f"[{ev.get('reason', '?')}]")
    print("=" * 60)


async def run_provider_test(message: str | None = None,
                            model: str | None = None,
                            json_output: bool = False) -> None:
    """Route one benign call through the router (exercises failover)."""
    _ensure_utf8_stdout()
    from app.llm.base import LLMConfig, LLMMessage

    r = _get_router()
    config = LLMConfig(temperature=0.0, max_tokens=32)
    if model:
        config.model = model
    print("\n" + "=" * 60)
    print("  DevPilot Provider Test (Phase 19B)")
    print("=" * 60)
    print(f"  Routing through: {r._priority()}")
    try:
        response = await r.chat(
            [LLMMessage(role="user", content=message or "Reply with exactly: provider-ok")],
            config=config,
        )
    except Exception as exc:
        print(f"  [FAIL] All providers failed: {exc}")
        print("=" * 60)
        sys.exit(1)
    payload = {
        "success": True,
        "provider": r.active_provider,
        "content": response.content[:200],
        "finish_reason": response.finish_reason,
    }
    if json_output:
        print(json.dumps(payload, indent=2, default=str))
        return
    print(f"  Provider:   {payload['provider']}")
    print(f"  Response:   {payload['content']}")
    print("=" * 60)
