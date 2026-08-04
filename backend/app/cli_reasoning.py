"""
Phase 17 CLI commands for Collaborative Reasoning & Evidence Consensus.

Usage:
    python -m app.cli consensus <run_id>
    python -m app.cli conflicts <run_id>
    python -m app.cli notebook <run_id>
"""

from __future__ import annotations

import sys


def _ensure_utf8_stdout() -> None:
    """Force UTF-8 stdout so live-LLM-derived content (topics, summaries)
    cannot crash the CLI on a Windows cp1252 console."""
    try:
        sys.stdout.reconfigure(  # type: ignore[attr-defined]
            encoding="utf-8", errors="replace"
        )
    except (AttributeError, ValueError):
        pass


def add_cli_commands(parent_parser) -> None:
    """Add Phase 17 reasoning CLI commands to the argument parser."""
    subparsers = parent_parser  # Passed as subparsers from main cli

    consensus_parser = subparsers.add_parser(
        "consensus", help="Show evidence consensus records for a run (Phase 17)"
    )
    consensus_parser.add_argument("run_id", type=str, help="Run ID, e.g. RUN-ABC123")
    consensus_parser.add_argument(
        "--json", action="store_true", help="Output raw JSON",
    )

    conflicts_parser = subparsers.add_parser(
        "conflicts", help="Show detected contradictions for a run (Phase 17)"
    )
    conflicts_parser.add_argument("run_id", type=str, help="Run ID")
    conflicts_parser.add_argument(
        "--json", action="store_true", help="Output raw JSON",
    )

    notebook_parser = subparsers.add_parser(
        "notebook", help="Show the shared engineering notebook for a run (Phase 17)"
    )
    notebook_parser.add_argument("run_id", type=str, help="Run ID")
    notebook_parser.add_argument(
        "--json", action="store_true", help="Output raw JSON",
    )


async def run_consensus(run_id: str, json_output: bool = False) -> None:
    """Display evidence consensus records for a run."""
    _ensure_utf8_stdout()
    from app.services.reasoning_service import CollaborativeReasoningEngine

    svc = CollaborativeReasoningEngine()
    await svc.recover(run_id)
    consensus = await svc.list_consensus(run_id)

    if json_output:
        import json
        print(json.dumps([c.model_dump() for c in consensus], indent=2, default=str))
        return

    print(f"\n{'='*60}")
    # ASCII-only separators (a Windows cp1252 console cannot encode the
    # Unicode arrow/em-dash used elsewhere).
    print(f"  Run: {run_id} - Evidence Consensus ({len(consensus)})")
    print(f"{'='*60}\n")

    if not consensus:
        print("  No consensus records found for this run.")
        print(f"{'='*60}\n")
        return

    for i, c in enumerate(consensus, 1):
        print(f"  [{i}] {c.topic} -> {c.status.value.upper()}")
        print(f"      Summary: {c.summary[:160]}")
        print(f"      Confidence: {c.confidence.tier.value} "
              f"({round(c.confidence.value, 2)}) "
              f"[{c.confidence.evidence_count} evidence, "
              f"{c.confidence.deterministic_count} deterministic]")
        if c.supporting_evidence:
            print(f"      Supporting: {len(c.supporting_evidence)} ref(s)")
        if c.conflicting_evidence:
            print(f"      Conflicting: {len(c.conflicting_evidence)} ref(s)")
        print(f"      Decision: {c.final_decision[:140]}")
        if c.contributing_agents:
            print(f"      Agents: {', '.join(c.contributing_agents)}")
        print()

    print(f"{'='*60}\n")


async def run_conflicts(run_id: str, json_output: bool = False) -> None:
    _ensure_utf8_stdout()
    """Display detected contradictions for a run."""
    from app.services.reasoning_service import CollaborativeReasoningEngine

    svc = CollaborativeReasoningEngine()
    await svc.recover(run_id)
    contradictions = await svc.list_contradictions(run_id)

    if json_output:
        import json
        print(json.dumps([c.model_dump() for c in contradictions],
                         indent=2, default=str))
        return

    print(f"\n{'='*60}")
    print(f"  Run: {run_id} - Contradictions ({len(contradictions)})")
    print(f"{'='*60}\n")

    if not contradictions:
        print("  No contradictions detected for this run.")
        print(f"{'='*60}\n")
        return

    for i, c in enumerate(contradictions, 1):
        print(f"  [{i}] {c.kind.value} -> {c.resolution}")
        print(f"      {c.description[:180]}")
        print(f"      Claim: {c.claim_evidence.type.value} "
              f"({c.claim_evidence.reference[:80]})")
        if c.deterministic_evidence:
            print(f"      Deterministic: {c.deterministic_evidence.type.value} "
                  f"({c.deterministic_evidence.reference[:80]})")
        print()

    print(f"{'='*60}\n")


async def run_notebook(run_id: str, json_output: bool = False) -> None:
    _ensure_utf8_stdout()
    """Display the shared engineering notebook for a run."""
    from app.services.reasoning_service import CollaborativeReasoningEngine

    svc = CollaborativeReasoningEngine()
    await svc.recover(run_id)
    notebook = await svc.get_notebook(run_id)

    if json_output:
        import json
        print(json.dumps(notebook.model_dump() if notebook else None,
                         indent=2, default=str))
        return

    if notebook is None:
        print(f"\n  No engineering notebook found for run {run_id}.\n")
        return

    print(f"\n{'='*60}")
    print(f"  Run: {run_id} - Engineering Notebook ({notebook.notebook_id})")
    print(f"{'='*60}\n")
    print(f"  Task: {notebook.task[:180]}")
    print(f"  Accepted decisions: {len(notebook.accepted_decisions)}")
    print(f"  Rejected decisions: {len(notebook.rejected_decisions)}")
    print(f"  Conflicts: {len(notebook.conflicts)} "
          f"(resolved: {len(notebook.resolved_conflicts)})")
    print(f"  Consensus: {len(notebook.consensus)}")
    print(f"  Timeline entries: {len(notebook.timeline)}\n")

    print("  Timeline:")
    for t in notebook.timeline[:20]:
        print(f"    [{t.entry_type.value}] {t.label[:70]} - {t.detail[:120]}")

    print(f"{'='*60}\n")
