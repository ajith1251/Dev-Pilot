"""
Semantic Repository Graph — in-memory directed graph of code symbols and relationships.

Provides:
- Add/remove nodes and edges
- Symbol lookup by ID, name, file
- Traversal: dependencies, dependents, callers, callees, tests
- Cycle-safe traversal with bounded depth and fan-out limits
- Confidence levels for resolved relationships
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set


# ── Relationship Types ─────────────────────────────────────────


class RelationshipType(str, Enum):
    """Types of relationships between code symbols."""

    CONTAINS = "contains"  # File contains class, class contains method
    IMPORTS = "imports"  # Imports another module/symbol
    EXPORTS = "exports"  # Exports a symbol
    DEFINES = "defines"  # Defines a type/interface
    CALLS = "calls"  # Calls a function/method
    REFERENCES = "references"  # References a symbol
    INHERITS = "inherits"  # Extends a class
    IMPLEMENTS = "implements"  # Implements an interface
    DEPENDS_ON = "depends_on"  # General dependency
    TESTS = "tests"  # Test file tests an implementation
    COMPOSES = "composes"  # Uses as a component/dependency injection
    ANNOTATED_BY = "annotated_by"  # Has a decorator/annotation
    MEMBER_OF = "member_of"  # Is a member of a module/namespace


# ── Confidence Levels ──────────────────────────────────────────


class ConfidenceLevel(str, Enum):
    """Confidence in a relationship resolution.

    EXACT:    Statically verifiable (e.g., import statement, class inheritance)
    HIGH:     Strong evidence (e.g., method call with matching name/args)
    MEDIUM:   Probable but ambiguous (e.g., dynamic call, name-based match)
    UNRESOLVED: Relationship exists but target not found in graph
    """

    EXACT = "exact"
    HIGH = "high"
    MEDIUM = "medium"
    UNRESOLVED = "unresolved"


# ── Graph Entities ─────────────────────────────────────────────


@dataclass(frozen=True)
class GraphNode:
    """A node in the semantic repository graph.

    Represents any semantic entity: file, module, class, function, etc.
    Nodes are immutable and identified by their ID (derived from file + qualified name).
    """

    id: str
    """Stable deterministic identifier: file_path::qualified_name"""

    name: str
    """Short name of the symbol (e.g., 'AuthService', 'login')"""

    qualified_name: str
    """Fully qualified name (e.g., 'app.services.auth.AuthService.login')"""

    kind: str
    """Kind of symbol: 'file', 'module', 'class', 'function', 'method',
    'interface', 'type', 'enum', 'variable', 'import', 'test_file', etc."""

    file_path: str
    """Repository-relative path to the source file."""

    language: str
    """Programming language."""

    start_line: int = 0
    end_line: int = 0

    parent_id: Optional[str] = None
    """ID of parent node (e.g., class containing a method)."""

    signature: Optional[str] = None
    """Function/class signature if available."""

    docstring: Optional[str] = None
    """First line of documentation."""

    metadata: Dict[str, Any] = field(default_factory=dict)
    """Additional metadata (decorators, visibility, exports, etc.)."""


@dataclass(frozen=True)
class EdgeMetadata:
    """Metadata attached to a graph edge."""

    relationship: RelationshipType
    confidence: ConfidenceLevel
    source_lines: Optional[List[int]] = None
    """Line numbers in source that evidence this relationship."""

    resolution_detail: Optional[str] = None
    """How the relationship was resolved (e.g., 'import statement', 'name match')."""

    weight: float = 1.0
    """Relative importance of this relationship (0-1)."""

    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GraphEdge:
    """A directed edge in the semantic repository graph."""

    source_id: str
    target_id: str
    metadata: EdgeMetadata

    def __post_init__(self) -> None:
        if not self.source_id or not self.target_id:
            raise ValueError("source_id and target_id must be non-empty")


# ── Traversal Result ───────────────────────────────────────────


@dataclass
class TraversalResult:
    """Result of a bounded graph traversal."""

    nodes: List[GraphNode] = field(default_factory=list)
    edges: List[GraphEdge] = field(default_factory=list)
    truncated: bool = False
    """True if traversal was cut short by limits."""

    levels: Dict[str, int] = field(default_factory=dict)
    """node_id -> distance from root (0 = root)."""


# ── Symbol ID Utilities ────────────────────────────────────────


def make_symbol_id(file_path: str, qualified_name: str) -> str:
    """Create a deterministic symbol ID from file path and qualified name.

    Format: file_path::qualified_name
    This is stable across sessions and machines.
    """
    return f"{file_path}::{qualified_name}"


def normalize_qualified_name(
    file_path: str,
    name: str,
    parent_names: Optional[List[str]] = None,
) -> str:
    """Build a qualified name using file module path and parent hierarchy."""
    module = file_path.replace("/", ".").rsplit(".", 1)[0] if "." in file_path else file_path
    module = module.replace("\\", ".")
    if parent_names:
        return f"{module}.{'.'.join(parent_names)}.{name}"
    return f"{module}.{name}"


# ── Graph Implementation ───────────────────────────────────────


class SemanticRepositoryGraph:
    """In-memory directed graph of semantic code entities.

    Maintains:
    - nodes: Dict[node_id -> GraphNode]
    - edges: Dict[source_id -> Dict[target_id -> EdgeMetadata]]
    - reverse_edges: Dict[target_id -> Dict[source_id -> EdgeMetadata]]

    All traversals are bounded to prevent infinite loops from cycles.
    """

    # Default limits
    DEFAULT_MAX_DEPTH = 5
    DEFAULT_MAX_FAN_OUT = 100
    DEFAULT_MAX_NODES = 500

    def __init__(
        self,
        max_depth: int = DEFAULT_MAX_DEPTH,
        max_fan_out: int = DEFAULT_MAX_FAN_OUT,
        max_nodes: int = DEFAULT_MAX_NODES,
    ) -> None:
        self._max_depth = max_depth
        self._max_fan_out = max_fan_out
        self._max_nodes = max_nodes

        # Core storage
        self._nodes: Dict[str, GraphNode] = {}
        self._edges: Dict[str, Dict[str, List[EdgeMetadata]]] = {}
        # source_id -> target_id -> list of EdgeMetadata (supports parallel edges)

        # Reverse index for efficient dependents/callers queries
        self._reverse_edges: Dict[str, Dict[str, List[EdgeMetadata]]] = {}
        # target_id -> source_id -> list of EdgeMetadata

        # Indexes
        self._by_file: Dict[str, List[str]] = {}  # file_path -> [node_ids]
        self._by_name: Dict[str, List[str]] = {}  # name -> [node_ids]
        self._by_kind: Dict[str, List[str]] = {}  # kind -> [node_ids]

        # Edge type indexes
        self._edges_by_type: Dict[str, Dict[str, Dict[str, List[EdgeMetadata]]]] = {}
        # relationship.value -> source_id -> target_id -> list of EdgeMetadata

    # ── Node Operations ─────────────────────────────────────────

    def add_node(self, node: GraphNode) -> None:
        """Add a node to the graph. Replaces existing node with same ID."""
        self._nodes[node.id] = node

        # Update indexes
        self._by_file.setdefault(node.file_path, []).append(node.id)
        self._by_name.setdefault(node.name, []).append(node.id)
        self._by_kind.setdefault(node.kind, []).append(node.id)

    def remove_node(self, node_id: str) -> None:
        """Remove a node and all its edges."""
        if node_id not in self._nodes:
            return

        node = self._nodes[node_id]

        # Remove from indexes
        if node_id in self._by_file.get(node.file_path, []):
            self._by_file[node.file_path].remove(node_id)

        if node_id in self._by_name.get(node.name, []):
            self._by_name[node.name].remove(node_id)

        if node_id in self._by_kind.get(node.kind, []):
            self._by_kind[node.kind].remove(node_id)

        # Remove outgoing edges
        if node_id in self._edges:
            for target_id in self._edges[node_id]:
                if target_id in self._reverse_edges:
                    self._reverse_edges[target_id].pop(node_id, None)
            del self._edges[node_id]

        # Remove incoming edges
        if node_id in self._reverse_edges:
            for source_id in self._reverse_edges[node_id]:
                if source_id in self._edges:
                    self._edges[source_id].pop(node_id, None)
            del self._reverse_edges[node_id]

        # Remove from edge type indexes
        for rel_type in list(self._edges_by_type.keys()):
            if node_id in self._edges_by_type[rel_type]:
                for target_id in list(self._edges_by_type[rel_type][node_id].keys()):
                    if target_id in self._edges_by_type[rel_type]:
                        self._edges_by_type[rel_type][target_id].pop(node_id, None)
                self._edges_by_type[rel_type].pop(node_id, None)

        # Remove node
        del self._nodes[node_id]

    def get_node(self, node_id: str) -> Optional[GraphNode]:
        """Get a node by its ID."""
        return self._nodes.get(node_id)

    def has_node(self, node_id: str) -> bool:
        return node_id in self._nodes

    def node_count(self) -> int:
        return len(self._nodes)

    # ── Edge Operations ─────────────────────────────────────────

    def add_edge(
        self,
        source_id: str,
        target_id: str,
        relationship: RelationshipType,
        confidence: ConfidenceLevel = ConfidenceLevel.EXACT,
        source_lines: Optional[List[int]] = None,
        resolution_detail: Optional[str] = None,
        weight: float = 1.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Add a directed edge between two nodes.

        Both source and target must already exist in the graph.
        """
        if source_id not in self._nodes:
            raise ValueError(f"Source node not found: {source_id}")
        if target_id not in self._nodes:
            # Allow unresolved targets with a warning
            confidence = ConfidenceLevel.UNRESOLVED

        edge_meta = EdgeMetadata(
            relationship=relationship,
            confidence=confidence,
            source_lines=source_lines,
            resolution_detail=resolution_detail,
            weight=min(1.0, max(0.0, weight)),
            metadata=metadata or {},
        )

        # Forward edges
        self._edges.setdefault(source_id, {}).setdefault(target_id, []).append(edge_meta)

        # Reverse edges
        self._reverse_edges.setdefault(target_id, {}).setdefault(source_id, []).append(edge_meta)

        # Edge type index
        rel_key = relationship.value
        self._edges_by_type.setdefault(rel_key, {}).setdefault(source_id, {}).setdefault(target_id, []).append(edge_meta)

    def get_edges(
        self, source_id: str, target_id: Optional[str] = None
    ) -> List[GraphEdge]:
        """Get edges from a source node, optionally to a specific target."""
        source_edges = self._edges.get(source_id, {})
        if target_id:
            metas = source_edges.get(target_id, [])
            return [GraphEdge(source_id, target_id, m) for m in metas]
        result = []
        for tid, metas in source_edges.items():
            for m in metas:
                result.append(GraphEdge(source_id, tid, m))
        return result

    def get_reverse_edges(
        self, target_id: str, source_id: Optional[str] = None
    ) -> List[GraphEdge]:
        """Get edges pointing to a target node."""
        rev = self._reverse_edges.get(target_id, {})
        if source_id:
            metas = rev.get(source_id, [])
            return [GraphEdge(source_id, target_id, m) for m in metas]
        result = []
        for sid, metas in rev.items():
            for m in metas:
                result.append(GraphEdge(sid, target_id, m))
        return result

    def edge_count(self) -> int:
        """Count total edges (including parallel edges per type)."""
        total = 0
        for source_map in self._edges.values():
            for target_list in source_map.values():
                total += len(target_list)
        return total

    # ── Lookup Methods ──────────────────────────────────────────

    def symbols_in_file(self, file_path: str) -> List[GraphNode]:
        """Get all symbols in a file."""
        node_ids = self._by_file.get(file_path, [])
        return [self._nodes[nid] for nid in node_ids if nid in self._nodes]

    def find_symbols_by_name(self, name: str) -> List[GraphNode]:
        """Find symbols by short name."""
        node_ids = self._by_name.get(name, [])
        return [self._nodes[nid] for nid in node_ids if nid in self._nodes]

    def symbols_by_kind(self, kind: str) -> List[GraphNode]:
        """Get all symbols of a given kind."""
        node_ids = self._by_kind.get(kind, [])
        return [self._nodes[nid] for nid in node_ids if nid in self._nodes]

    def all_nodes(self) -> List[GraphNode]:
        """Get all nodes in the graph."""
        return list(self._nodes.values())

    # ── Relationship Queries ────────────────────────────────────

    def dependencies_of(self, node_id: str, relationship_types: Optional[Set[RelationshipType]] = None) -> List[GraphEdge]:
        """Get outgoing edges (what this node depends on)."""
        if relationship_types is None:
            return self.get_edges(node_id)

        result = []
        for edge in self.get_edges(node_id):
            if edge.metadata.relationship in relationship_types:
                result.append(edge)
        return result

    def dependents_of(self, node_id: str, relationship_types: Optional[Set[RelationshipType]] = None) -> List[GraphEdge]:
        """Get incoming edges (what depends on this node)."""
        if relationship_types is None:
            return self.get_reverse_edges(node_id)

        result = []
        for edge in self.get_reverse_edges(node_id):
            if edge.metadata.relationship in relationship_types:
                result.append(edge)
        return result

    def callers_of(self, node_id: str) -> List[GraphEdge]:
        """Get edges that CALL this node."""
        return self.dependents_of(node_id, {RelationshipType.CALLS})

    def callees_of(self, node_id: str) -> List[GraphEdge]:
        """Get edges where this node CALLS something."""
        return self.dependencies_of(node_id, {RelationshipType.CALLS})

    def tests_for_symbol(self, node_id: str) -> List[GraphEdge]:
        """Get TESTS edges pointing to this symbol."""
        return self.dependents_of(node_id, {RelationshipType.TESTS})

    def symbols_tested_by(self, test_node_id: str) -> List[GraphEdge]:
        """Get TESTS edges from this test symbol."""
        return self.get_edges(test_node_id)

    # ── Traversal ───────────────────────────────────────────────

    def traverse_dependents(
        self,
        node_id: str,
        max_depth: Optional[int] = None,
        max_nodes: Optional[int] = None,
        relationship_types: Optional[Set[RelationshipType]] = None,
    ) -> TraversalResult:
        """Traverse the graph from a node following INCOMING edges.

        Returns all nodes that transitively depend on the given node.
        Bounded by max_depth and max_nodes.
        """
        result = TraversalResult()
        depth = max_depth or self._max_depth
        node_limit = max_nodes or self._max_nodes

        visited: Set[str] = set()
        queue: List[str] = [node_id]
        result.levels[node_id] = 0
        visited.add(node_id)

        current_depth = 0

        while queue and len(visited) <= node_limit:
            level_size = len(queue)
            if current_depth > depth:
                break

            for _ in range(level_size):
                current_id = queue.pop(0)
                if current_id not in self._nodes:
                    continue

                # Get dependents (incoming edges)
                edges = self.dependents_of(current_id, relationship_types)
                for edge in edges[:self._max_fan_out]:
                    source_id = edge.source_id
                    if source_id not in visited:
                        visited.add(source_id)
                        result.levels[source_id] = current_depth + 1
                        queue.append(source_id)
                        if len(visited) <= node_limit:
                            node = self._nodes.get(source_id)
                            if node:
                                result.nodes.append(node)
                                result.edges.append(edge)

                if len(edges) > self._max_fan_out:
                    result.truncated = True

            current_depth += 1

        if len(visited) > node_limit:
            result.truncated = True

        return result

    def traverse_dependencies(
        self,
        node_id: str,
        max_depth: Optional[int] = None,
        max_nodes: Optional[int] = None,
        relationship_types: Optional[Set[RelationshipType]] = None,
    ) -> TraversalResult:
        """Traverse the graph from a node following OUTGOING edges.

        Returns all nodes that this node transitively depends on.
        """
        result = TraversalResult()
        depth = max_depth or self._max_depth
        node_limit = max_nodes or self._max_nodes

        visited: Set[str] = set()
        queue: List[str] = [node_id]
        result.levels[node_id] = 0
        visited.add(node_id)

        current_depth = 0

        while queue and len(visited) <= node_limit:
            level_size = len(queue)
            if current_depth > depth:
                break

            for _ in range(level_size):
                current_id = queue.pop(0)
                if current_id not in self._nodes:
                    continue

                edges = self.dependencies_of(current_id, relationship_types)
                for edge in edges[:self._max_fan_out]:
                    target_id = edge.target_id
                    if target_id not in visited:
                        visited.add(target_id)
                        result.levels[target_id] = current_depth + 1
                        queue.append(target_id)
                        if len(visited) <= node_limit:
                            node = self._nodes.get(target_id)
                            if node:
                                result.nodes.append(node)
                                result.edges.append(edge)

                if len(edges) > self._max_fan_out:
                    result.truncated = True

            current_depth += 1

        if len(visited) > node_limit:
            result.truncated = True

        return result

    def traverse_neighborhood(
        self,
        node_id: str,
        depth: int = 2,
        max_nodes: int = 50,
    ) -> TraversalResult:
        """Traverse both directions (dependencies + dependents) up to given depth."""
        result = TraversalResult()
        visited: Set[str] = set()
        queue: List[tuple[str, int, str]] = [(node_id, 0, "self")]
        visited.add(node_id)
        result.levels[node_id] = 0

        while queue and len(visited) <= max_nodes:
            current_id, current_depth, direction = queue.pop(0)
            if current_depth >= depth:
                continue

            node = self._nodes.get(current_id)
            if node and current_id != node_id:
                result.nodes.append(node)

            # Outgoing edges (dependencies)
            for edge in self.get_edges(current_id)[:self._max_fan_out]:
                tid = edge.target_id
                if tid not in visited:
                    visited.add(tid)
                    result.levels[tid] = current_depth + 1
                    queue.append((tid, current_depth + 1, "out"))
                    result.edges.append(edge)

            # Incoming edges (dependents)
            for edge in self.get_reverse_edges(current_id)[:self._max_fan_out]:
                sid = edge.source_id
                if sid not in visited:
                    visited.add(sid)
                    result.levels[sid] = current_depth + 1
                    queue.append((sid, current_depth + 1, "in"))
                    result.edges.append(edge)

        if len(visited) > max_nodes:
            result.truncated = True

        return result

    # ── Serialization ───────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the graph to a dictionary."""
        nodes = []
        for node in self._nodes.values():
            nodes.append({
                "id": node.id,
                "name": node.name,
                "qualified_name": node.qualified_name,
                "kind": node.kind,
                "file_path": node.file_path,
                "language": node.language,
                "start_line": node.start_line,
                "end_line": node.end_line,
                "parent_id": node.parent_id,
                "signature": node.signature,
                "docstring": node.docstring,
                "metadata": node.metadata,
            })

        edges = []
        for source_id, target_map in self._edges.items():
            for target_id, metas in target_map.items():
                for meta in metas:
                    edges.append({
                        "source_id": source_id,
                        "target_id": target_id,
                        "relationship": meta.relationship.value,
                        "confidence": meta.confidence.value,
                        "source_lines": meta.source_lines,
                        "resolution_detail": meta.resolution_detail,
                        "weight": meta.weight,
                        "metadata": meta.metadata,
                    })

        return {
            "nodes": nodes,
            "edges": edges,
            "stats": {
                "node_count": len(nodes),
                "edge_count": len(edges),
            },
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any], **kwargs) -> SemanticRepositoryGraph:
        """Deserialize a graph from a dictionary."""
        graph = cls(**kwargs)
        for node_data in data.get("nodes", []):
            node = GraphNode(
                id=node_data["id"],
                name=node_data["name"],
                qualified_name=node_data.get("qualified_name", node_data["name"]),
                kind=node_data["kind"],
                file_path=node_data["file_path"],
                language=node_data.get("language", ""),
                start_line=node_data.get("start_line", 0),
                end_line=node_data.get("end_line", 0),
                parent_id=node_data.get("parent_id"),
                signature=node_data.get("signature"),
                docstring=node_data.get("docstring"),
                metadata=node_data.get("metadata", {}),
            )
            graph.add_node(node)

        for edge_data in data.get("edges", []):
            graph.add_edge(
                source_id=edge_data["source_id"],
                target_id=edge_data["target_id"],
                relationship=RelationshipType(edge_data["relationship"]),
                confidence=ConfidenceLevel(edge_data.get("confidence", "exact")),
                source_lines=edge_data.get("source_lines"),
                resolution_detail=edge_data.get("resolution_detail"),
                weight=edge_data.get("weight", 1.0),
                metadata=edge_data.get("metadata"),
            )

        return graph

    # ── Stats ────────────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        """Return summary statistics about the graph."""
        kind_counts: Dict[str, int] = {}
        for node in self._nodes.values():
            kind_counts[node.kind] = kind_counts.get(node.kind, 0) + 1

        rel_counts: Dict[str, int] = {}
        for source_map in self._edges.values():
            for target_list in source_map.values():
                for meta in target_list:
                    key = meta.relationship.value
                    rel_counts[key] = rel_counts.get(key, 0) + 1

        return {
            "node_count": len(self._nodes),
            "edge_count": self.edge_count(),
            "file_count": len(self._by_file),
            "kinds": dict(sorted(kind_counts.items(), key=lambda x: -x[1])),
            "relationships": dict(sorted(rel_counts.items(), key=lambda x: -x[1])),
        }

    def clear(self) -> None:
        """Clear all nodes and edges."""
        self._nodes.clear()
        self._edges.clear()
        self._reverse_edges.clear()
        self._by_file.clear()
        self._by_name.clear()
        self._by_kind.clear()
        self._edges_by_type.clear()
