"""
Phase 15 CLI commands for Multi-Agent Collaboration diagnostics.

Usage:
    python -m app.cli handoffs <run_id>
    python -m app.cli decisions <run_id>
    python -m app.cli collaboration <run_id>
"""

from __future__ import annotations


def add_cli_commands(parent_parser) -> None:
    """Add Phase 15 collaboration CLI commands to the argument parser."""
    subparsers = parent_parser  # Passed as subparsers from main cli

    handoffs_parser = subparsers.add_parser(
        "handoffs", help="Show structured agent handoffs for a run (Phase 15)"
    )
    handoffs_parser.add_argument("run_id", type=str, help="Run ID, e.g. RUN-ABC123")
    handoffs_parser.add_argument(
        "--to-agent", type=str, default=None,
        help="Filter handoffs by recipient agent (planner, coding, testing, repair, reviewer)",
    )
    handoffs_parser.add_argument(
        "--json", action="store_true", help="Output raw JSON",
    )

    decisions_parser = subparsers.add_parser(
        "decisions", help="Show engineering decision records for a run (Phase 15)"
    )
    decisions_parser.add_argument("run_id", type=str, help="Run ID")
    decisions_parser.add_argument(
        "--json", action="store_true", help="Output raw JSON",
    )

    collab_parser = subparsers.add_parser(
        "collaboration", help="Show shared run collaboration summary (Phase 15)"
    )
    collab_parser.add_argument("run_id", type=str, help="Run ID")
    collab_parser.add_argument(
        "--json", action="store_true", help="Output raw JSON",
    )


async def run_handoffs(run_id: str, to_agent: str | None = None, json_output: bool = False) -> None:
    """Display structured handoffs for a run."""
    from app.services.collaboration_service import CollaborationService

    svc = CollaborationService()
    await svc.recover(run_id)
    handoffs = await svc.list_handoffs(run_id, to_agent=to_agent)

    if json_output:
        import json
        print(json.dumps([
            {
                "handoff_id": h.handoff_id,
                "from_agent": h.from_agent,
                "to_agent": h.to_agent,
                "stage": h.stage,
                "summary": h.summary,
                "decisions": h.decisions,
                "affected_symbols": h.affected_symbols,
                "status": h.status.value,
                "validation": h.validation,
                "created_at": h.created_at,
            }
            for h in handoffs
        ], indent=2))
        return

    print(f"\n{'='*60}")
    print(f"  Run: {run_id} — Handoffs ({len(handoffs)})")
    print(f"{'='*60}\n")

    if not handoffs:
        print("  No handoffs found for this run.")
        print(f"{'='*60}\n")
        return

    for i, h in enumerate(handoffs, 1):
        print(f"  [{i}] {h.from_agent} → {h.to_agent}  ({h.stage})")
        print(f"      Summary: {h.summary[:150]}")
        if h.affected_symbols:
            print(f"      Symbols: {', '.join(h.affected_symbols[:8])}")
        if h.decisions:
            print(f"      Decisions: {'; '.join(d[:100] for d in h.decisions[:3])}")
        if h.evidence_refs:
            print(f"      Evidence: {len(h.evidence_refs)} ref(s)")
        print(f"      Status: {h.status.value}")
        print()

    print(f"{'='*60}\n")


async def run_decisions(run_id: str, json_output: bool = False) -> None:
    """Display engineering decision records for a run."""
    from app.services.collaboration_service import CollaborationService

    svc = CollaborationService()
    await svc.recover(run_id)
    decisions = await svc.list_decisions(run_id)

    if json_output:
        import json
        print(json.dumps([
            {
                "decision_id": d.decision_id,
                "decision_type": d.decision_type.value,
                "statement": d.statement,
                "made_by": d.made_by,
                "created_at": d.created_at,
            }
            for d in decisions
        ], indent=2))
        return

    print(f"\n{'='*60}")
    print(f"  Run: {run_id} — Decisions ({len(decisions)})")
    print(f"{'='*60}\n")

    if not decisions:
        print("  No decision records found for this run.")
        print(f"{'='*60}\n")
        return

    for i, d in enumerate(decisions, 1):
        print(f"  [{i}] [{d.decision_type.value}] {d.statement[:180]}")
        print(f"      Made by: {d.made_by}")

    print(f"{'='*60}\n")


async def run_collaboration(run_id: str, json_output: bool = False) -> None:
    """Display the shared run collaboration summary."""
    from app.services.collaboration_service import CollaborationService

    svc = CollaborationService()
    await svc.recover(run_id)
    metrics = await svc.get_collaboration_metrics(run_id)
    handoffs = await svc.list_handoffs(run_id)
    decisions = await svc.list_decisions(run_id)
    conflicts = await svc.list_conflicts(run_id)

    if json_output:
        import json
        print(json.dumps({
            **metrics,
            "handoffs": [
                {
                    "from": h.from_agent,
                    "to": h.to_agent,
                    "summary": h.summary,
                    "status": h.status.value,
                }
                for h in handoffs
            ],
            "decisions": [
                {
                    "type": d.decision_type.value,
                    "statement": d.statement,
                    "made_by": d.made_by,
                }
                for d in decisions
            ],
            "conflicts": [
                {
                    "description": c.description,
                    "resolution": c.resolution.value,
                }
                for c in conflicts
            ],
        }, indent=2))
        return

    print(f"\n{'='*60}")
    print(f"  Run: {run_id} — Collaboration Summary")
    print(f"{'='*60}\n")

    print(f"  Handoffs:            {metrics['handoffs_total']}")
    print(f"  Handoffs validated:  {metrics['handoffs_validated']}")
    print(f"  Decisions:           {metrics['decisions']}")
    print(f"  Conflicts detected:  {metrics['conflicts_detected']}")
    print(f"  Conflicts resolved:  {metrics['conflicts_resolved']}")
    print(f"  Evidence items:      {metrics['evidence_items']}")

    # Timeline
    print(f"\n  Timeline:")
    print(f"    Planner")
    for h in handoffs:
        if h.from_agent == "planner":
            print(f"      ↓ {h.to_agent}")
    print(f"    Coding")
    for h in handoffs:
        if h.from_agent == "coding":
            print(f"      ↓ {h.to_agent}")
    print(f"    Testing")
    for h in handoffs:
        if h.from_agent == "testing":
            print(f"      ↓ {h.to_agent}")
    print(f"    Repair")
    for h in handoffs:
        if h.from_agent == "repair":
            print(f"      ↓ {h.to_agent}")
    print(f"    Review")

    if conflicts:
        print(f"\n  Conflicts:")
        for c in conflicts:
            print(f"    - {c.description[:140]} [{c.resolution.value}]")

    print(f"{'='*60}\n")
