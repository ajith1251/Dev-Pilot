"""
Agent Graph Helper — provides a simple interface for agents to get
semantic graph context without importing GraphAwareRetriever directly.

Each agent calls `get_graph_context()` with relevant symbol names
and file paths from its input. The function returns a formatted
string that can be injected into the agent's prompt.

If no graph is loaded, returns an empty string (graceful degradation).
"""

from __future__ import annotations

import os
import re
from typing import List, Optional


def get_graph_context(
    symbol_names: List[str],
    file_paths: Optional[List[str]] = None,
    max_context: int = 20,
    repo_path: Optional[str] = None,
) -> str:
    """Get formatted semantic graph context for agent prompts.

    Args:
        symbol_names: Symbol names to look up (e.g., ['AuthService', 'login']).
        file_paths: Optional file paths to include.
        max_context: Maximum related symbols to include.
        repo_path: Optional repository path to index on-demand.

    Returns:
        Formatted string for injection into agent prompts, or
        empty string if no graph is available.
    """
    from app.code_intelligence.code_intelligence_service import CodeIntelligenceService
    from app.code_intelligence.graph_retriever import GraphAwareRetriever

    service = _get_service(repo_path=repo_path)
    graph = service.get_current_graph()
    if not graph:
        return ""

    retriever = GraphAwareRetriever(graph=graph)
    return retriever.get_agent_context(
        symbol_names=symbol_names,
        file_paths=file_paths,
        max_context=max_context,
    )


def get_graph_context_markdown(
    symbol_names: List[str],
    file_paths: Optional[List[str]] = None,
    max_context: int = 20,
    repo_path: Optional[str] = None,
) -> str:
    """Get graph context formatted as a markdown section for prompts.

    Returns a ready-to-inject markdown block, or empty string.
    """
    context = get_graph_context(
        symbol_names=symbol_names,
        file_paths=file_paths,
        max_context=max_context,
        repo_path=repo_path,
    )
    if not context:
        return ""
    return f"\n=== SEMANTIC GRAPH CONTEXT (REPOSITORY STRUCTURE) ===\n{context}\n\n"


def _get_service(repo_path: Optional[str] = None) -> CodeIntelligenceService:
    """Get or create the CodeIntelligenceService.

    If repo_path is provided and no graph is loaded, indexes the repo.
    Creates a fresh instance to avoid circular imports with API modules.
    """
    from app.code_intelligence.code_intelligence_service import CodeIntelligenceService

    service = CodeIntelligenceService(max_files=500)

    if repo_path:
        try:
            if os.path.isdir(repo_path):
                service.index_repository(repo_path)
        except Exception:
            pass  # Graceful degradation

    return service


def extract_symbols_from_plan(plan_text: str) -> List[str]:
    """Extract likely symbol names from plan text.

    Looks for capitalized words (class names, function names)
    mentioned in the plan.
    """
    # Look for CamelCase identifiers (class names) and function names
    symbols = set()
    # Class names: CamelCase
    for m in re.finditer(r'\b[A-Z][a-z]+(?:[A-Z][a-z]*)+\b', plan_text):
        symbols.add(m.group())
    # Function names: snake_case after 'def' or in code blocks
    for m in re.finditer(r'def\s+(\w+)', plan_text):
        symbols.add(m.group(1))
    return list(symbols)[:15]


def extract_symbols_from_changed_files(file_paths: List[str]) -> List[str]:
    """Extract likely symbol names from file paths.

    Uses file names without extensions as symbol name candidates.
    """
    symbols = []
    for fp in file_paths:
        name = os.path.splitext(os.path.basename(fp))[0]
        # Convert snake_case to CamelCase convention
        parts = name.split("_")
        symbols.append("".join(p.capitalize() for p in parts))
        symbols.append(name)
    return symbols[:15]
