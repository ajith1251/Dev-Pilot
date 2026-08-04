"""
Graph-Aware Retriever — extends Phase 5 HybridRetriever with graph context.

Given a query and a semantic graph, the retriever:
1. Finds relevant symbols (by name, kind, file)
2. Traverses the neighborhood (callers, dependencies, tests)
3. Ranks results combining text relevance + graph distance
4. Returns bounded context for agent consumption
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from app.code_intelligence.semantic_graph import (
    GraphNode,
    RelationshipType,
    SemanticRepositoryGraph,
    TraversalResult,
)


@dataclass
class GraphContextItem:
    """A single item in graph-aware retrieval result."""

    node: GraphNode
    graph_distance: int
    relationship_types: List[str]
    relevance_score: float = 0.0
    context_preview: Optional[str] = None
    evidence: List[str] = field(default_factory=list)


@dataclass
class GraphRetrievalResult:
    """Result of graph-aware retrieval."""

    direct_matches: List[GraphContextItem] = field(default_factory=list)
    """Symbols that directly matched the query."""

    graph_context: List[GraphContextItem] = field(default_factory=list)
    """Related symbols from graph traversal (callers, deps, tests)."""

    total_symbols: int = 0
    truncated: bool = False
    warnings: List[str] = field(default_factory=list)

    def all_items(self) -> List[GraphContextItem]:
        return self.direct_matches + self.graph_context


class GraphAwareRetriever:
    """Enhances retrieval with semantic graph context.

    Uses the graph to expand a symbol query into its neighborhood:
    - Callers (what calls this)
    - Callees (what this calls)
    - Dependents (what depends on this)
    - Related tests
    """

    # Graph expansion limits
    DEFAULT_EXPANSION_DEPTH = 2
    DEFAULT_MAX_EXPANDED = 30

    # Priority for graph-expanded items
    GRAPH_PRIORITY = {
        RelationshipType.TESTS: 10,
        RelationshipType.CALLS: 8,
        RelationshipType.INHERITS: 7,
        RelationshipType.IMPLEMENTS: 7,
        RelationshipType.DEPENDS_ON: 5,
        RelationshipType.REFERENCES: 3,
        RelationshipType.CONTAINS: 2,
        RelationshipType.IMPORTS: 1,
    }

    def __init__(
        self,
        graph: Optional[SemanticRepositoryGraph] = None,
    ) -> None:
        self._graph = graph

    def set_graph(self, graph: SemanticRepositoryGraph) -> None:
        self._graph = graph

    def retrieve_for_symbols(
        self,
        symbol_ids: List[str],
        expand_depth: int = DEFAULT_EXPANSION_DEPTH,
        max_expanded: int = DEFAULT_MAX_EXPANDED,
    ) -> GraphRetrievalResult:
        """Retrieve graph context for specific symbols.

        Args:
            symbol_ids: Symbol IDs to expand from.
            expand_depth: How deep to traverse the graph.
            max_expanded: Max related symbols to include.

        Returns:
            GraphRetrievalResult with matches and expanded context.
        """
        result = GraphRetrievalResult()
        if not self._graph:
            result.warnings.append("No graph loaded")
            return result

        # Direct matches
        direct_nodes: List[GraphNode] = []
        for sid in symbol_ids:
            node = self._graph.get_node(sid)
            if node:
                direct_nodes.append(node)
                result.direct_matches.append(GraphContextItem(
                    node=node,
                    graph_distance=0,
                    relationship_types=[],
                    relevance_score=1.0,
                ))

        if not direct_nodes:
            result.warnings.append("No matching symbols found")
            return result

        result.total_symbols = len(direct_nodes)

        # Graph expansion
        expanded: Dict[str, Tuple[GraphNode, int, List[str]]] = {}
        visited: Set[str] = {n.id for n in direct_nodes}

        for node in direct_nodes:
            self._expand_from_node(
                node=node,
                current_depth=0,
                max_depth=expand_depth,
                max_nodes=max_expanded,
                visited=visited,
                expanded=expanded,
            )

        # Convert to result items
        for node_id, (gnode, distance, rels) in expanded.items():
            priority = max(
                self.GRAPH_PRIORITY.get(RelationshipType(r), 0) for r in rels
            )
            score = 1.0 / (1.0 + distance + (10.0 / (priority + 1)))

            result.graph_context.append(GraphContextItem(
                node=gnode,
                graph_distance=distance,
                relationship_types=rels,
                relevance_score=score,
            ))

        # Sort by relevance
        result.graph_context.sort(key=lambda x: -x.relevance_score)
        result.graph_context = result.graph_context[:max_expanded]

        if len(expanded) > max_expanded:
            result.truncated = True

        return result

    def retrieve_for_file(
        self,
        file_path: str,
        expand_depth: int = DEFAULT_EXPANSION_DEPTH,
        max_expanded: int = DEFAULT_MAX_EXPANDED,
    ) -> GraphRetrievalResult:
        """Retrieve graph context for symbols in a file."""
        if not self._graph:
            result = GraphRetrievalResult()
            result.warnings.append("No graph loaded")
            return result

        symbols = self._graph.symbols_in_file(file_path)
        return self.retrieve_for_symbols(
            symbol_ids=[s.id for s in symbols],
            expand_depth=expand_depth,
            max_expanded=max_expanded,
        )

    def _expand_from_node(
        self,
        node: GraphNode,
        current_depth: int,
        max_depth: int,
        max_nodes: int,
        visited: Set[str],
        expanded: Dict[str, Tuple[GraphNode, int, List[str]]],
    ) -> None:
        """Recursively expand from a node."""
        if current_depth >= max_depth:
            return
        if len(expanded) >= max_nodes:
            return

        if not self._graph:
            return

        # Outgoing edges (callees, dependencies)
        for edge in self._graph.get_edges(node.id):
            tid = edge.target_id
            if tid not in visited:
                visited.add(tid)
                t_node = self._graph.get_node(tid)
                if t_node:
                    rel = edge.metadata.relationship.value
                    if tid in expanded:
                        existing = expanded[tid]
                        if rel not in existing[2]:
                            existing[2].append(rel)
                    else:
                        expanded[tid] = (t_node, current_depth + 1, [rel])
                    self._expand_from_node(
                        t_node, current_depth + 1, max_depth, max_nodes,
                        visited, expanded,
                    )

        # Incoming edges (callers, dependents)
        for edge in self._graph.get_reverse_edges(node.id):
            sid = edge.source_id
            if sid not in visited:
                visited.add(sid)
                s_node = self._graph.get_node(sid)
                if s_node:
                    rel = edge.metadata.relationship.value
                    if sid in expanded:
                        existing = expanded[sid]
                        if rel not in existing[2]:
                            existing[2].append(rel)
                    else:
                        expanded[sid] = (s_node, current_depth + 1, [rel])
                    self._expand_from_node(
                        s_node, current_depth + 1, max_depth, max_nodes,
                        visited, expanded,
                    )

    def get_agent_context(
        self,
        symbol_names: List[str],
        file_paths: Optional[List[str]] = None,
        max_context: int = 20,
    ) -> str:
        """Get a formatted context string suitable for LLM agent prompts.

        Args:
            symbol_names: Symbol names to look up.
            file_paths: Optional file paths to include.
            max_context: Maximum items to include.

        Returns:
            Formatted context string.
        """
        if not self._graph:
            return "(No graph loaded)"

        # Collect relevant symbol IDs
        symbol_ids: List[str] = []

        for name in symbol_names:
            matches = self._graph.find_symbols_by_name(name)
            for m in matches:
                symbol_ids.append(m.id)

        for fp in (file_paths or []):
            symbols = self._graph.symbols_in_file(fp)
            for s in symbols:
                symbol_ids.append(s.id)

        if not symbol_ids:
            return "(No relevant symbols found in graph)"

        result = self.retrieve_for_symbols(
            symbol_ids=symbol_ids[:10],
            max_expanded=max_context,
        )

        lines = ["## Graph Context"]
        lines.append(f"Found {result.total_symbols} relevant symbols")

        for item in result.direct_matches[:5]:
            lines.append(f"\n### {item.node.name} ({item.node.kind})")
            lines.append(f"  File: {item.node.file_path}")
            if item.node.signature:
                lines.append(f"  Signature: {item.node.signature}")
            if item.node.docstring:
                lines.append(f"  Doc: {item.node.docstring}")

        if result.graph_context:
            lines.append(f"\n### Related ({len(result.graph_context)} symbols):")
            for item in result.graph_context[:10]:
                lines.append(
                    f"  [{item.node.kind}] {item.node.name} "
                    f"({item.node.file_path}, dist={item.graph_distance})"
                )

        if result.truncated:
            lines.append("\n(Results truncated — more related symbols exist)")

        return "\n".join(lines)
