"""
Impact Analysis Service — determines what code is affected by a change.

Given a set of symbols (or files/symbols from a patch), the impact
analyzer traverses the semantic graph to find:
- Directly affected symbols (callers, dependents)
- Associated test files
- Dependent modules/files
- Inheritance relationships
- Risk indicators (high-fanout, circular dependencies, deep chains)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from app.code_intelligence.semantic_graph import (
    ConfidenceLevel,
    GraphNode,
    RelationshipType,
    SemanticRepositoryGraph,
    TraversalResult,
)


class RiskLevel(str, Enum):
    """Risk level for an impacted item."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NONE = "none"


@dataclass
class ImpactedSymbol:
    """A symbol affected by a change."""

    node: GraphNode
    relationship: str
    distance: int
    confidence: ConfidenceLevel
    risk: RiskLevel
    evidence: List[str] = field(default_factory=list)


@dataclass
class ImpactAnalysisResult:
    """Complete result of an impact analysis."""

    root_symbols: List[GraphNode] = field(default_factory=list)
    """The symbols that were changed (input)."""

    direct_impact: List[ImpactedSymbol] = field(default_factory=list)
    """Symbols directly affected (distance=1)."""

    indirect_impact: List[ImpactedSymbol] = field(default_factory=list)
    """Symbols transitively affected (distance>1)."""

    related_tests: List[GraphNode] = field(default_factory=list)
    """Test files/symbols that cover affected code."""

    risk_summary: Dict[str, int] = field(default_factory=lambda: {
        "critical": 0, "high": 0, "medium": 0, "low": 0, "none": 0,
    })
    """Count of symbols at each risk level."""

    affected_files: List[str] = field(default_factory=list)
    """Unique file paths affected."""

    truncated: bool = False
    """True if traversal was cut short by limits."""

    warning: Optional[str] = None


class ImpactAnalysisService:
    """Analyze the impact of changing code symbols.

    Uses the semantic repository graph to determine what code
    is affected by a change, providing evidence-backed results.
    """

    # Relationship types that propagate impact
    IMPACT_RELATIONSHIPS = {
        RelationshipType.CALLS,
        RelationshipType.INHERITS,
        RelationshipType.IMPLEMENTS,
        RelationshipType.DEPENDS_ON,
        RelationshipType.COMPOSES,
        RelationshipType.REFERENCES,
        RelationshipType.TESTS,
    }

    # High-risk indicators
    HIGH_FANOUT_THRESHOLD = 20
    DEEP_CHAIN_THRESHOLD = 5

    def __init__(
        self,
        graph: Optional[SemanticRepositoryGraph] = None,
        max_depth: int = 3,
        max_nodes: int = 100,
    ) -> None:
        self._graph = graph
        self.max_depth = max_depth
        self.max_nodes = max_nodes

    def set_graph(self, graph: SemanticRepositoryGraph) -> None:
        """Set or update the graph to use."""
        self._graph = graph

    def analyze(
        self,
        symbol_ids: List[str],
        max_depth: Optional[int] = None,
        max_nodes: Optional[int] = None,
    ) -> ImpactAnalysisResult:
        """Analyze the impact of changing the given symbols.

        Args:
            symbol_ids: List of symbol IDs that are being changed.
            max_depth: Max traversal depth for impact propagation.
            max_nodes: Max nodes to include in result.

        Returns:
            ImpactAnalysisResult with affected symbols, tests, and risk.
        """
        result = ImpactAnalysisResult()
        if not self._graph:
            result.warning = "No graph loaded"
            return result

        depth = max_depth or self.max_depth
        node_limit = max_nodes or self.max_nodes
        affected_files: Set[str] = set()

        for sym_id in symbol_ids:
            node = self._graph.get_node(sym_id)
            if not node:
                result.root_symbols.append(GraphNode(
                    id=sym_id, name=sym_id, qualified_name=sym_id,
                    kind="unknown", file_path="", language="",
                ))
                continue

            result.root_symbols.append(node)
            affected_files.add(node.file_path)

            # Traverse dependents (what depends on this symbol)
            traversal = self._graph.traverse_dependents(
                node_id=sym_id,
                max_depth=depth,
                max_nodes=node_limit,
                relationship_types=self.IMPACT_RELATIONSHIPS,
            )

            if traversal.truncated:
                result.truncated = True

            for traveral_node in traversal.nodes:
                if traveral_node.id == sym_id:
                    continue

                distance = traversal.levels.get(traveral_node.id, 1)
                affected_files.add(traveral_node.file_path)

                # Determine relationship
                rel_types = self._get_relationship_types(sym_id, traveral_node.id)
                rel_str = ",".join(r.value for r in rel_types) if rel_types else "depends_on"

                # Assess risk
                risk = self._assess_risk(traveral_node, distance)

                impacted = ImpactedSymbol(
                    node=traveral_node,
                    relationship=rel_str,
                    distance=distance,
                    confidence=self._get_confidence(sym_id, traveral_node.id),
                    risk=risk,
                    evidence=self._gather_evidence(sym_id, traveral_node.id),
                )

                if distance == 1:
                    result.direct_impact.append(impacted)
                else:
                    result.indirect_impact.append(impacted)

                # Update risk summary
                result.risk_summary[risk.value] = result.risk_summary.get(risk.value, 0) + 1

                # Collect test associations
                if traveral_node.kind in ("test_file", "test_function", "test_method", "test_class"):
                    result.related_tests.append(traveral_node)

        result.affected_files = sorted(affected_files)

        return result

    def analyze_files(
        self,
        file_paths: List[str],
        max_depth: Optional[int] = None,
        max_nodes: Optional[int] = None,
    ) -> ImpactAnalysisResult:
        """Analyze impact by changing specific files.

        Finds all symbols in the given files and analyzes their impact.
        """
        if not self._graph:
            result = ImpactAnalysisResult()
            result.warning = "No graph loaded"
            return result

        # Find all symbols in these files
        affected_symbols: List[str] = []
        for fp in file_paths:
            symbols = self._graph.symbols_in_file(fp)
            for sym in symbols:
                affected_symbols.append(sym.id)

        return self.analyze(
            symbol_ids=affected_symbols,
            max_depth=max_depth,
            max_nodes=max_nodes,
        )

    def _get_relationship_types(self, source_id: str, target_id: str) -> Set[RelationshipType]:
        """Get the relationship types between two symbols."""
        types: Set[RelationshipType] = set()
        if not self._graph:
            return types
        edges = self._graph.get_edges(source_id, target_id)
        for edge in edges:
            types.add(edge.metadata.relationship)
        reverse = self._graph.get_reverse_edges(source_id, target_id)
        for edge in reverse:
            types.add(edge.metadata.relationship)
        return types

    def _get_confidence(self, source_id: str, target_id: str) -> ConfidenceLevel:
        """Get the highest confidence level between two symbols."""
        best = ConfidenceLevel.UNRESOLVED
        if not self._graph:
            return best
        for edge in self._graph.get_edges(source_id, target_id):
            conf = edge.metadata.confidence
            if conf == ConfidenceLevel.EXACT:
                return conf
            if conf == ConfidenceLevel.HIGH:
                best = conf
            elif conf == ConfidenceLevel.MEDIUM and best not in (ConfidenceLevel.EXACT, ConfidenceLevel.HIGH):
                best = conf
        return best

    def _gather_evidence(self, source_id: str, target_id: str) -> List[str]:
        """Gather evidence strings about the relationship."""
        evidence = []
        if not self._graph:
            return evidence
        for edge in self._graph.get_edges(source_id, target_id):
            if edge.metadata.resolution_detail:
                evidence.append(edge.metadata.resolution_detail)
        for edge in self._graph.get_reverse_edges(source_id, target_id):
            if edge.metadata.resolution_detail:
                evidence.append(f"Reverse: {edge.metadata.resolution_detail}")
        return evidence

    def _assess_risk(self, node: GraphNode, distance: int) -> RiskLevel:
        """Assess the risk level for an impacted symbol."""
        if not self._graph:
            return RiskLevel.MEDIUM

        # Check fan-out
        outgoing = len(self._graph.get_edges(node.id))
        incoming = len(self._graph.get_reverse_edges(node.id))

        if outgoing > self.HIGH_FANOUT_THRESHOLD or incoming > self.HIGH_FANOUT_THRESHOLD:
            return RiskLevel.HIGH

        # Deep chain
        if distance >= self.DEEP_CHAIN_THRESHOLD:
            return RiskLevel.HIGH

        # Module-level impact
        if node.kind == "module" and incoming > 10:
            return RiskLevel.CRITICAL

        # Distance-based
        if distance == 1:
            if node.kind in ("class", "interface", "module"):
                return RiskLevel.MEDIUM
            return RiskLevel.LOW
        elif distance <= 3:
            return RiskLevel.LOW
        else:
            return RiskLevel.NONE

    @staticmethod
    def summarize(result: ImpactAnalysisResult) -> str:
        """Create a human-readable summary of impact analysis."""
        lines = []
        lines.append(f"Impact Analysis: {len(result.root_symbols)} root symbol(s)")
        lines.append(f"  Direct impact:  {len(result.direct_impact)} symbols")
        lines.append(f"  Indirect impact: {len(result.indirect_impact)} symbols")
        lines.append(f"  Related tests:   {len(result.related_tests)}")
        lines.append(f"  Affected files:  {len(result.affected_files)}")
        lines.append(f"  Risk summary:    {result.risk_summary}")
        if result.truncated:
            lines.append("  ⚠ Traversal was truncated (limits reached)")
        if result.warning:
            lines.append(f"  ⚠ {result.warning}")

        # Top impacted symbols
        for item in result.direct_impact[:5]:
            lines.append(
                f"    [{item.risk.value}] {item.node.name} "
                f"({item.relationship}, distance={item.distance})"
            )
        for item in result.indirect_impact[:3]:
            lines.append(
                f"    [{item.risk.value}] {item.node.name} "
                f"(indirect, distance={item.distance})"
            )

        return "\n".join(lines)
