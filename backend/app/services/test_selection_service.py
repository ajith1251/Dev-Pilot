"""
Phase 15 (Phase 12d) — Impact-Driven Test Selection.

Selects the tests that cover changed code using the Phase 12 semantic
graph's impact analysis instead of filename heuristics. Given a set of
changed files, it:

1. Finds the symbols defined in those files
2. Runs ImpactAnalysisService to discover what transitively depends
   on them (callers, implementers, tests)
3. Maps TESTS-related nodes back to test file paths
4. Ranks them by relevance (distance + relationship priority)

This gives Phase 7 smarter targeting: only tests covering impacted
code are executed, instead of the whole suite.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from app.code_intelligence.impact_analyzer import ImpactAnalysisService
from app.code_intelligence.semantic_graph import (
    RelationshipType,
    SemanticRepositoryGraph,
)


@dataclass
class TestSelection:
    """A test file selected because it covers impacted code."""

    file_path: str
    reason: str
    distance: int
    confidence: str = "medium"


@dataclass
class TestSelectionResult:
    """Result of an impact-driven test selection pass."""

    selected_tests: List[TestSelection] = field(default_factory=list)
    impacted_files: List[str] = field(default_factory=list)
    root_symbol_count: int = 0
    truncated: bool = False
    warning: Optional[str] = None

    @property
    def file_paths(self) -> List[str]:
        """Unique test file paths in selection order."""
        seen: Set[str] = set()
        paths: List[str] = []
        for t in self.selected_tests:
            if t.file_path not in seen:
                seen.add(t.file_path)
                paths.append(t.file_path)
        return paths


class TestSelectionService:
    """Select tests that cover changed code via the semantic graph.

    Falls back to an empty result (never raising) when no graph is
    loaded, mirroring the graceful-degradation pattern used across
    the codebase.
    """

    # Test node kinds produced by the parsers
    TEST_KINDS = {"test_file", "test_class", "test_function", "test_method"}

    def __init__(
        self,
        graph: Optional[SemanticRepositoryGraph] = None,
        max_depth: int = 3,
        max_nodes: int = 150,
    ) -> None:
        self._graph = graph
        self._analyzer = ImpactAnalysisService(
            graph=graph,
            max_depth=max_depth,
            max_nodes=max_nodes,
        )

    def set_graph(self, graph: SemanticRepositoryGraph) -> None:
        """Set or update the graph to use."""
        self._graph = graph
        self._analyzer.set_graph(graph)

    def select_for_changed_files(
        self,
        changed_files: List[str],
    ) -> TestSelectionResult:
        """Select tests covering the given changed files.

        Args:
            changed_files: Repository-relative paths of modified files.

        Returns:
            TestSelectionResult with ranked test file selections.
        """
        result = TestSelectionResult()
        if not changed_files:
            return result
        if self._graph is None:
            result.warning = "No graph loaded — test selection unavailable"
            return result

        # 1. Impact analysis on the changed files
        impact = self._analyzer.analyze_files(
            file_paths=changed_files,
            max_depth=self._analyzer.max_depth,
            max_nodes=self._analyzer.max_nodes,
        )

        result.root_symbol_count = len(impact.root_symbols)
        result.impacted_files = impact.affected_files
        result.truncated = impact.truncated

        # 2. Collect test nodes + files
        test_files: Dict[str, TestSelection] = {}
        for node in impact.related_tests:
            if node.kind not in self.TEST_KINDS and "test" not in node.kind:
                continue
            if not node.file_path:
                continue

            distance = 1  # related_tests are reached via TESTS edges (distance 1)
            existing = test_files.get(node.file_path)
            if existing is None or distance < existing.distance:
                test_files[node.file_path] = TestSelection(
                    file_path=node.file_path,
                    reason=f"covers impacted symbol '{node.name}' ({node.kind})",
                    distance=distance,
                    confidence="high",
                )

        # 3. Also consider direct TESTS edges from impacted files themselves
        for fp in changed_files:
            for node in self._graph.symbols_in_file(fp):
                for edge in self._graph.tests_for_symbol(node.id):
                    target = self._graph.get_node(edge.source_id)
                    if target and target.file_path:
                        test_files.setdefault(
                            target.file_path,
                            TestSelection(
                                file_path=target.file_path,
                                reason=f"direct TESTS edge for '{node.name}'",
                                distance=0,
                                confidence="exact",
                            ),
                        )

        # 4. Order: exact first, then distance, then path
        result.selected_tests = sorted(
            test_files.values(),
            key=lambda t: (
                0 if t.confidence == "exact" else 1,
                t.distance,
                t.file_path,
            ),
        )
        return result

    def summarize(self, result: TestSelectionResult) -> str:
        """Human-readable summary of a selection result."""
        lines = [
            f"Test selection: {len(result.selected_tests)} test(s)",
            f"  Impacted files:  {len(result.impacted_files)}",
            f"  Root symbols:    {result.root_symbol_count}",
        ]
        if result.truncated:
            lines.append("  ⚠ Impact traversal truncated (limits reached)")
        for t in result.selected_tests[:10]:
            lines.append(f"    [{t.confidence}] {t.file_path} — {t.reason}")
        return "\n".join(lines)
