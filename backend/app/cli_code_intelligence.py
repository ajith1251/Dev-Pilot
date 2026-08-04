"""
Phase 12 CLI commands for Advanced Code Intelligence.

Usage:
    python -m app.cli code-index <path>         — Index a repository
    python -m app.cli code-symbols <path>        — List symbols
    python -m app.cli code-symbol <id>           — Get symbol details
    python -m app.cli code-impact <path> <sym>   — Impact analysis
    python -m app.cli code-retrieve <path> <sym> — Graph-aware retrieval
"""

from __future__ import annotations

import sys
from pathlib import Path

from app.code_intelligence import (
    CodeIntelligenceService,
    ImpactAnalysisService,
)
from app.code_intelligence.graph_retriever import GraphAwareRetriever


def add_cli_commands(parent_parser) -> None:
    """Add Phase 12 CLI commands to the argument parser."""
    subparsers = parent_parser  # This is passed as subparsers from main cli

    # code-index
    index_parser = subparsers.add_parser(
        "code-index", help="Build a semantic repository index (Phase 12)"
    )
    index_parser.add_argument("path", type=str, help="Path to the repository")
    index_parser.add_argument("--verbose", action="store_true", help="Show detailed stats")

    # code-symbols
    sym_parser = subparsers.add_parser(
        "code-symbols", help="List symbols in the indexed repository"
    )
    sym_parser.add_argument("path", type=str, help="Path to the repository")
    sym_parser.add_argument("--kind", type=str, default=None, help="Filter by kind")
    sym_parser.add_argument("--name", type=str, default=None, help="Filter by name")
    sym_parser.add_argument("--limit", type=int, default=30, help="Max results")

    # code-symbol
    sym_detail = subparsers.add_parser(
        "code-symbol", help="Get symbol details with graph context"
    )
    sym_detail.add_argument("path", type=str, help="Path to the repository")
    sym_detail.add_argument("symbol_id", type=str, help="Symbol ID (file::name)")
    sym_detail.add_argument("--depth", type=int, default=2, help="Traversal depth")

    # code-impact
    impact_parser = subparsers.add_parser(
        "code-impact", help="Analyze impact of changing a symbol"
    )
    impact_parser.add_argument("path", type=str, help="Path to the repository")
    impact_parser.add_argument("symbol", type=str, help="Symbol name to analyze")
    impact_parser.add_argument("--depth", type=int, default=3, help="Max traversal depth")

    # code-retrieve
    retrieve_parser = subparsers.add_parser(
        "code-retrieve", help="Graph-aware context retrieval for agents"
    )
    retrieve_parser.add_argument("path", type=str, help="Path to the repository")
    retrieve_parser.add_argument("symbol", type=str, help="Symbol name to retrieve context for")
    retrieve_parser.add_argument("--depth", type=int, default=2, help="Expand depth")

    # code-status
    status_parser = subparsers.add_parser(
        "code-status", help="Show code intelligence status"
    )
    status_parser.add_argument("path", type=str, help="Path to the repository")


def run_code_index(path: str, verbose: bool = False) -> None:
    """Build a semantic repository index."""
    service = CodeIntelligenceService()
    print(f"\n{'='*60}")
    print(f"  Phase 12 — Semantic Repository Index")
    print(f"{'='*60}")
    print(f"  Repository: {path}")
    print(f"{'='*60}\n")

    result = service.index_repository(path)

    print(f"  Files scanned:  {result.stats.files_scanned}")
    print(f"  Files parsed:   {result.stats.files_parsed}")
    print(f"  Files failed:   {result.stats.files_failed}")
    print(f"  Symbols:        {result.stats.symbols_extracted}")
    print(f"  Relationships:  {result.stats.edges_created}")
    print(f"  Duration:       {result.stats.duration_seconds}s")
    print(f"  Index ID:       {result.index_id}")

    if verbose and result.stats.languages:
        print(f"\n  Languages:")
        for lang, count in sorted(result.stats.languages.items(), key=lambda x: -x[1]):
            print(f"    {lang}: {count} files")

    if result.stats.warnings:
        print(f"\n  Warnings ({len(result.stats.warnings)}):")
        for w in result.stats.warnings[:5]:
            print(f"    ⚠ {w}")

    if result.stats.errors:
        print(f"\n  Errors ({len(result.stats.errors)}):")
        for e in result.stats.errors[:5]:
            print(f"    ❌ {e}")

    print(f"\n{'='*60}\n")


def run_code_symbols(path: str, kind: str | None = None, name: str | None = None, limit: int = 30) -> None:
    """List symbols in the indexed repository."""
    service = CodeIntelligenceService()
    result = service.index_repository(path)
    graph = result.graph

    symbols = graph.all_nodes()
    if kind:
        symbols = [s for s in symbols if s.kind == kind]
    if name:
        symbols = [s for s in symbols if name.lower() in s.name.lower()]
    symbols = symbols[:limit]

    print(f"\n{'='*60}")
    print(f"  Symbols in {result.repository_id}")
    print(f"{'='*60}")
    print(f"  Total: {len(symbols)} (showing up to {limit})")
    print()

    for s in symbols:
        parent = f" <- {s.parent_id.rsplit('::', 1)[-1]}" if s.parent_id else ""
        sig = f"  {s.signature}" if s.signature else ""
        print(f"  [{s.kind:>14}] {s.name}{parent}")
        print(f"    {s.file_path}")
        if sig:
            print(f"    {sig}")
        print()

    print(f"{'='*60}\n")


def run_code_symbol_detail(path: str, symbol_id: str, depth: int = 2) -> None:
    """Show symbol details with graph context."""
    service = CodeIntelligenceService()
    result = service.index_repository(path)
    graph = result.graph

    node = graph.get_node(symbol_id)
    if not node:
        print(f"Symbol not found: {symbol_id}")
        return

    print(f"\n{'='*60}")
    print(f"  Symbol: {node.name}")
    print(f"{'='*60}")
    print(f"  ID:            {node.id}")
    print(f"  Qualified:     {node.qualified_name}")
    print(f"  Kind:          {node.kind}")
    print(f"  File:          {node.file_path}")
    print(f"  Lines:         {node.start_line}-{node.end_line}")
    if node.signature:
        print(f"  Signature:     {node.signature}")
    if node.docstring:
        print(f"  Doc:           {node.docstring}")
    if node.parent_id:
        parent = graph.get_node(node.parent_id)
        print(f"  Parent:        {parent.name if parent else node.parent_id}")

    # Dependencies
    print(f"\n  Dependencies (outgoing):")
    for edge in graph.get_edges(symbol_id)[:10]:
        target = graph.get_node(edge.target_id)
        tname = target.name if target else edge.target_id
        print(f"    -> {tname} ({edge.metadata.relationship.value}, {edge.metadata.confidence.value})")

    # Dependents
    print(f"\n  Dependents (incoming):")
    for edge in graph.get_reverse_edges(symbol_id)[:10]:
        source = graph.get_node(edge.source_id)
        sname = source.name if source else edge.source_id
        print(f"    <- {sname} ({edge.metadata.relationship.value}, {edge.metadata.confidence.value})")

    print(f"\n  Stats:")
    print(f"    Edge count: {len(graph.get_edges(symbol_id))} outgoing, "
          f"{len(graph.get_reverse_edges(symbol_id))} incoming")

    print(f"\n{'='*60}\n")


def run_code_impact(path: str, symbol_name: str, depth: int = 3) -> None:
    """Run impact analysis."""
    service = CodeIntelligenceService()
    result = service.index_repository(path)

    matches = result.graph.find_symbols_by_name(symbol_name)
    if not matches:
        print(f"Symbol '{symbol_name}' not found")
        return

    symbol_ids = [m.id for m in matches]
    impact_result = service.analyze_impact(
        symbol_ids=symbol_ids[:5],
        max_depth=depth,
    )

    from app.code_intelligence.impact_analyzer import ImpactAnalysisService
    print(ImpactAnalysisService.summarize(impact_result))


def run_code_retrieve(path: str, symbol_name: str, depth: int = 2) -> None:
    """Graph-aware retrieval for agents."""
    service = CodeIntelligenceService()
    result = service.index_repository(path)
    graph = result.graph

    retriever = GraphAwareRetriever(graph=graph)
    context = retriever.get_agent_context(
        symbol_names=[symbol_name],
        max_context=30,
    )
    print(f"\n{'='*60}")
    print(f"  Graph-Aware Context for '{symbol_name}'")
    print(f"{'='*60}\n")
    print(context)
    print(f"\n{'='*60}\n")


def run_code_status(path: str) -> None:
    """Show code intelligence status."""
    service = CodeIntelligenceService()
    result = service.index_repository(path)
    graph = result.graph
    stats = graph.stats()

    print(f"\n{'='*60}")
    print(f"  Code Intelligence Status — {result.repository_id}")
    print(f"{'='*60}")
    print(f"  Index ID:       {result.index_id}")
    print(f"  Graph nodes:    {stats['node_count']}")
    print(f"  Graph edges:    {stats['edge_count']}")
    print(f"  Unique files:   {stats['file_count']}")
    print(f"  Fingerprint:    {result.content_fingerprint}")
    print(f"  Duration:       {result.stats.duration_seconds}s")
    if stats.get("kinds"):
        print(f"\n  Symbol kinds:")
        for kind, count in sorted(stats["kinds"].items(), key=lambda x: -x[1])[:10]:
            print(f"    {kind}: {count}")
    if stats.get("relationships"):
        print(f"\n  Relationship types:")
        for rel, count in sorted(stats["relationships"].items(), key=lambda x: -x[1])[:10]:
            print(f"    {rel}: {count}")
    print(f"\n{'='*60}\n")
