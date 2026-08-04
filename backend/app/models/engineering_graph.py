"""
Phase 18 — Engineering Knowledge Graph (EKG) models.

A unified, higher-abstraction knowledge layer ABOVE the existing stores:

    Phase 12 Semantic Graph   — code symbols & relationships
    Phase 13 Repository Memory — durable engineering knowledge
    Phase 15 Collaboration    — handoffs, decisions, conflicts
    Phase 16 Autonomy         — goals, plan versions, replans
    Phase 17 Reasoning        — consensus, contradictions, notebook

The EKG reuses those entities as NODES and links them with typed,
temporal EDGES so the system can answer engineering questions:

- Why was this implemented?
- What introduced this symbol?
- Which repair fixed this issue?
- Which decision caused this architecture?

Phase 19A — Cross-Repository Knowledge Graph. Every node and edge belongs
to a repository namespace (`repository_id`), so repositories with
identical file or symbol names never collide. Cross-repository
relationships (DEPENDS_ON_REPOSITORY, SHARES_LIBRARY, IMPORTS_PACKAGE,
IMPLEMENTS_SHARED_INTERFACE, REFERENCES_SHARED_COMPONENT,
USES_SHARED_MEMORY, CALLS_EXTERNAL_SERVICE) are explicit, deterministic
links recorded in an OrganizationKnowledgeGraphService — never inferred.

Security invariant (unchanged since Phase 13-17): nodes/edges expose ONLY
verified engineering evidence, decisions, and provenance — never
chain-of-thought, hidden prompts, or internal reasoning.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.models.base import new_id


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Bounds (Safety & Performance) ───────────────────────────────
MAX_NEIGHBORHOOD_NODES = 200       # bounded neighborhood traversal
MAX_HISTORY_ENTRIES = 100          # bounded temporal history
MAX_QUERY_RESULTS = 50             # bounded query result nodes
MAX_EDGES_PER_NODE = 100           # bounded edges per node per query
MAX_NODES_PER_RUN_INGEST = 200     # max graph nodes created from one run
MAX_EXPLAIN_EVIDENCE = 20          # bounded provenance evidence per node
SUMMARY_MAX_LEN = 500

# Phase 19A — repository namespace bounds.
DEFAULT_REPOSITORY_ID = "default"  # backward-compatible namespace
MAX_REPOSITORIES_PER_ORG = 64      # bounded org registry
MAX_CROSS_EDGES_PER_REPO = 100     # bounded cross-repo edges per repo


# ── Node Types (§3) ─────────────────────────────────────────────


class EKNodeType(str, Enum):
    """Kinds of engineering entities stored in the graph."""

    # Code
    REPOSITORY = "repository"
    FOLDER = "folder"
    FILE = "file"
    MODULE = "module"
    PACKAGE = "package"
    CLASS = "class"
    INTERFACE = "interface"
    FUNCTION = "function"
    METHOD = "method"

    # Requirements / planning
    REQUIREMENT = "requirement"
    ACCEPTANCE_CRITERION = "acceptance_criterion"
    IMPLEMENTATION_PLAN = "implementation_plan"
    PLAN_VERSION = "plan_version"
    GOAL = "goal"

    # Implementation
    PATCH = "patch"
    COMMIT_CANDIDATE = "commit_candidate"

    # Verification
    TEST = "test"
    TEST_SUITE = "test_suite"

    # Review
    REVIEW_FINDING = "review_finding"
    QUALITY_GATE = "quality_gate"

    # Reasoning / collaboration
    EVIDENCE = "evidence"
    CONSENSUS = "consensus"
    CONTRADICTION = "contradiction"
    NOTEBOOK_ENTRY = "notebook_entry"
    DECISION = "decision"

    # Execution
    RUN = "run"
    AGENT = "agent"

    # Knowledge
    REPOSITORY_MEMORY = "repository_memory"


# ── Relationship Types (§4) ─────────────────────────────────────


class EKRelationshipType(str, Enum):
    """Typed, temporal relationships between engineering entities.

    Reuses Phase 12 relationship vocabulary where it applies and adds
    engineering-lifecycle relationships (SATISFIES, PRODUCED_BY, etc.).
    """

    # Code structure (Phase 12 compatible)
    CALLS = "calls"
    IMPORTS = "imports"
    CONTAINS = "contains"
    DEPENDS_ON = "depends_on"
    IMPLEMENTS = "implements"
    TESTS = "tests"
    REFERENCES = "references"
    AFFECTS = "affects"
    MODIFIES = "modifies"

    # Engineering lifecycle
    SATISFIES = "satisfies"             # implementation → requirement
    CREATED_DURING = "created_during"   # artifact → run
    PRODUCED_BY = "produced_by"         # artifact → agent
    DERIVED_FROM = "derived_from"       # plan_version → plan_version
    SUPPORTS = "supports"               # consensus → decision
    CONTRADICTS = "contradicts"         # contradiction → consensus
    SUPERSEDES = "supersedes"           # graph version → previous
    USES_MEMORY = "uses_memory"         # agent context → repository_memory
    VALIDATED_BY = "validated_by"       # patch → test / quality gate
    REVIEWED_BY = "reviewed_by"         # patch → review
    APPROVED_BY = "approved_by"         # patch → quality gate

    # Phase 19A — cross-repository relationships. Explicit, deterministic
    # links recorded via OrganizationKnowledgeGraphService.link_repositories
    # (and the shared-component linking helpers) — never inferred by an LLM.
    DEPENDS_ON_REPOSITORY = "depends_on_repository"         # repo → repo
    SHARES_LIBRARY = "shares_library"                       # repo → repo
    IMPORTS_PACKAGE = "imports_package"                     # repo → repo
    IMPLEMENTS_SHARED_INTERFACE = "implements_shared_interface"  # repo → repo
    REFERENCES_SHARED_COMPONENT = "references_shared_component"  # repo → repo
    USES_SHARED_MEMORY = "uses_shared_memory"               # repo → repo
    CALLS_EXTERNAL_SERVICE = "calls_external_service"       # repo → repo


# ── Node Status ─────────────────────────────────────────────────


class EKNodeStatus(str, Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"


# ── Graph Node ──────────────────────────────────────────────────


class EKNode(BaseModel):
    """One engineering entity in the graph.

    Provenance is retained on every node so evidence origins are never
    lost (§9): the requirement, plan version, agent, review finding,
    quality gate, memory entry and run that produced the entity.
    """

    node_id: str = Field(default_factory=lambda: f"EKN-{new_id().upper()[:10]}")
    node_type: EKNodeType = Field(description="Entity kind")
    name: str = Field(description="Short display name", max_length=200)
    qualified_name: str = Field(default="", max_length=500)
    kind: str = Field(default="", max_length=50)

    # External source reference (stable, non-graph id from the source store)
    source_ref: str = Field(
        default="", max_length=200,
        description="Stable reference in the source store (e.g. run_id, "
                    "consensus_id, memory_id, symbol id)",
    )
    source_type: str = Field(
        default="", max_length=50,
        description="Source store: run | consensus | notebook | memory | "
                    "goal | plan_version | code_symbol | test | review | gate",
    )

    payload: Dict[str, Any] = Field(
        default_factory=dict,
        description="Bounded, evidence-only metadata (never CoT)",
    )
    provenance: Dict[str, Any] = Field(
        default_factory=dict,
        description="Provenance chain: requirements, plan version, agent, "
                    "review finding, quality gate, memory, run id",
    )

    status: EKNodeStatus = Field(default=EKNodeStatus.ACTIVE)
    graph_version: int = Field(default=1)

    # Phase 19A — repository namespace. Every node belongs to a repository
    # so identical file/symbol names across repositories never collide.
    repository_id: str = Field(
        default=DEFAULT_REPOSITORY_ID, max_length=64,
        description="Repository namespace this node belongs to",
    )

    created_at: str = Field(default_factory=_utcnow_iso)
    updated_at: str = Field(default_factory=_utcnow_iso)

    def summary(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "node_type": self.node_type.value,
            "name": self.name[:200],
            "qualified_name": self.qualified_name[:200],
            "source_ref": self.source_ref[:100],
            "source_type": self.source_type,
            "status": self.status.value,
            "graph_version": self.graph_version,
            "repository_id": self.repository_id[:64],
            "created_at": self.created_at,
        }


# ── Graph Edge ──────────────────────────────────────────────────


class EKEdge(BaseModel):
    """One typed, directed relationship between two EK nodes."""

    edge_id: str = Field(default_factory=lambda: f"EKE-{new_id().upper()[:10]}")
    source_id: str = Field(description="Source EK node_id")
    target_id: str = Field(description="Target EK node_id")
    relationship: EKRelationshipType = Field(description="Relationship kind")
    weight: float = Field(default=1.0, ge=0.0, le=1.0)

    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Bounded edge metadata (evidence refs, source run)",
    )
    provenance: Dict[str, Any] = Field(
        default_factory=dict,
        description="Provenance chain for this relationship",
    )

    graph_version: int = Field(default=1)

    # Phase 19A — repository namespace. In-repo edges inherit the source
    # node's namespace; cross-repository edges are recorded separately in
    # the organization graph (CrossRepositoryEdge).
    repository_id: str = Field(
        default=DEFAULT_REPOSITORY_ID, max_length=64,
        description="Repository namespace this edge belongs to",
    )
    created_at: str = Field(default_factory=_utcnow_iso)

    def summary(self) -> Dict[str, Any]:
        return {
            "edge_id": self.edge_id,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "relationship": self.relationship.value,
            "weight": round(self.weight, 2),
            "graph_version": self.graph_version,
            "repository_id": self.repository_id[:64],
            "created_at": self.created_at,
        }


# ── Graph Version (§6) ──────────────────────────────────────────


class GraphVersion(BaseModel):
    """One version record of the engineering knowledge graph.

    Incremental evolution: repository changes or a completed run bump the
    version and record WHICH nodes/edges changed — never a full rebuild.
    """

    version: int = Field(description="Monotonic graph version")
    run_id: str = Field(default="", description="Run that triggered the bump")
    summary: str = Field(default="", max_length=SUMMARY_MAX_LEN)

    updated_nodes: List[str] = Field(default_factory=list, max_length=MAX_NODES_PER_RUN_INGEST)
    updated_edges: List[str] = Field(default_factory=list, max_length=MAX_EDGES_PER_NODE)
    superseded_node_ids: List[str] = Field(default_factory=list, max_length=MAX_NODES_PER_RUN_INGEST)

    timestamp: str = Field(default_factory=_utcnow_iso)

    def summary(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "run_id": self.run_id,
            "summary": self.summary[:200],
            "updated_nodes": len(self.updated_nodes),
            "updated_edges": len(self.updated_edges),
            "superseded_nodes": len(self.superseded_node_ids),
            "timestamp": self.timestamp,
        }


# ── Query Planning (§8) ─────────────────────────────────────────


class RetrievalStrategy(str, Enum):
    """The retrieval strategy a KnowledgeQueryPlanner selects."""

    SEMANTIC_GRAPH = "semantic_graph"        # Phase 12 code structure
    REPOSITORY_MEMORY = "repository_memory" # Phase 13 knowledge
    CONSENSUS = "consensus"                 # Phase 17 shared agreement
    NOTEBOOK = "notebook"                   # Phase 17 engineering timeline
    HISTORY = "history"                     # temporal / historical runs
    KNOWLEDGE_GRAPH = "knowledge_graph"     # EKG unified traversal
    MULTI = "multi"                         # merge several strategies
    # Phase 19A — organization-wide traversal across linked repositories.
    CROSS_REPOSITORY = "cross_repository"


class QueryScope(str, Enum):
    """Retrieval scope for a KnowledgeQueryPlanner plan.

    AUTO lets the planner decide: if the query references a registered
    repository or uses organization vocabulary, a cross-repository plan is
    selected automatically; otherwise the retrieval stays repository-local.
    """

    AUTO = "auto"
    LOCAL = "local"             # repository-local retrieval
    ORGANIZATION = "organization"  # organization-wide retrieval


class RetrievalPlan(BaseModel):
    """The planner's chosen retrieval strategy for a user query."""

    query: str = Field(description="The original query", max_length=500)
    intent: str = Field(
        description="Classified intent: explain_implementation | "
                    "find_related_requirements | historical_fixes | "
                    "affected_tests | architecture_decisions | "
                    "engineering_history | previous_solutions | "
                    "notebook_entries | quality_evidence | "
                    "cross_repository | general",
    )
    strategy: RetrievalStrategy = Field(description="Selected strategy")
    key_terms: List[str] = Field(default_factory=list, max_length=20)
    target_kinds: List[EKNodeType] = Field(default_factory=list, max_length=10)
    # Phase 19A — repository routing.
    scope: QueryScope = Field(default=QueryScope.AUTO, description="Retrieval scope")
    repository_ids: Optional[List[str]] = Field(
        default=None, description="Namespace filter (None = all accessible)",
    )
    cross_repository: bool = Field(
        default=False, description="Whether the plan traverses repositories",
    )
    rationale: str = Field(default="", max_length=300)

    def summary(self) -> Dict[str, Any]:
        return {
            "query": self.query[:200],
            "intent": self.intent,
            "strategy": self.strategy.value,
            "key_terms": self.key_terms[:10],
            "target_kinds": [k.value for k in self.target_kinds[:10]],
            "scope": self.scope.value,
            "cross_repository": self.cross_repository,
            "rationale": self.rationale[:200],
        }


class GraphQueryResult(BaseModel):
    """Bounded result of a graph retrieval."""

    query: str = Field(default="", max_length=500)
    strategy: RetrievalStrategy = Field(default=RetrievalStrategy.KNOWLEDGE_GRAPH)
    nodes: List[EKNode] = Field(default_factory=list, max_length=MAX_QUERY_RESULTS)
    edges: List[EKEdge] = Field(default_factory=list, max_length=MAX_EDGES_PER_NODE)
    truncated: bool = Field(default=False)
    total_nodes: int = Field(default=0)
    version: int = Field(default=1)
    plan: Optional[RetrievalPlan] = Field(default=None)

    # Phase 19 semantic retrieval (merged into lexical results, bounded).
    semantic_used: bool = Field(default=False)
    semantic_matches: int = Field(default=0)
    semantic_top_score: float = Field(default=0.0)

    # Phase 19A — repository routing metadata on the result.
    scope: QueryScope = Field(default=QueryScope.AUTO)
    repository_ids: Optional[List[str]] = Field(default=None)
    repositories: Dict[str, int] = Field(
        default_factory=dict,
        description="repository_id -> node count contributed to this result",
    )

    def summary(self) -> Dict[str, Any]:
        return {
            "query": self.query[:200],
            "strategy": self.strategy.value,
            "nodes": len(self.nodes),
            "edges": len(self.edges),
            "truncated": self.truncated,
            "total_nodes": self.total_nodes,
            "version": self.version,
            "semantic_used": self.semantic_used,
            "semantic_matches": self.semantic_matches,
            "semantic_top_score": round(self.semantic_top_score, 4),
            "scope": self.scope.value,
            "repositories": dict(sorted(self.repositories.items(), key=lambda x: -x[1])[:10]),
            "plan": self.plan.summary() if self.plan else None,
        }


# ── Graph Stats ─────────────────────────────────────────────────


class GraphStats(BaseModel):
    """Summary statistics about the engineering knowledge graph."""

    version: int = Field(default=1)
    node_count: int = Field(default=0)
    edge_count: int = Field(default=0)
    node_types: Dict[str, int] = Field(default_factory=dict)
    relationship_types: Dict[str, int] = Field(default_factory=dict)
    run_count: int = Field(default=0)
    repository_count: int = Field(default=0)
    last_updated: str = Field(default="")

    def summary(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "node_count": self.node_count,
            "edge_count": self.edge_count,
            "node_types": dict(sorted(self.node_types.items(), key=lambda x: -x[1])[:12]),
            "relationship_types": dict(
                sorted(self.relationship_types.items(), key=lambda x: -x[1])[:12]
            ),
            "run_count": self.run_count,
            "repository_count": self.repository_count,
            "last_updated": self.last_updated,
        }


# ── Node History (§5) ───────────────────────────────────────────


class NodeHistoryEntry(BaseModel):
    """One temporal snapshot of a graph node."""

    node_id: str = Field(description="The node")
    graph_version: int = Field(description="Version at which this snapshot existed")
    status: EKNodeStatus = Field(description="Status at that version")
    payload: Dict[str, Any] = Field(default_factory=dict)
    provenance: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default="")

    def summary(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "graph_version": self.graph_version,
            "status": self.status.value,
            "created_at": self.created_at,
        }


class NodeHistory(BaseModel):
    """Temporal history of a single node across graph versions."""

    node_id: str = Field(description="The node")
    current: Optional[EKNode] = Field(default=None)
    entries: List[NodeHistoryEntry] = Field(
        default_factory=list, max_length=MAX_HISTORY_ENTRIES
    )

    def summary(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "entries": len(self.entries),
            "current": self.current.summary() if self.current else None,
        }


# ── Phase 19A: Repository Namespaces ────────────────────────────


class RepositoryNamespace(BaseModel):
    """A registered repository namespace in the organization graph.

    Every engineering graph entity belongs to exactly one repository
    namespace so identical file/symbol names across repositories never
    collide. `organization_id` is future-ready (multi-tenant orgs).
    """

    repository_id: str = Field(description="Stable repository identifier")
    namespace_id: str = Field(
        default="", description="Canonical namespace (defaults to repository_id)",
    )
    organization_id: str = Field(
        default="default", description="Owning organization (future-ready)",
    )
    name: str = Field(default="", max_length=200, description="Display name")
    path: str = Field(default="", max_length=1024, description="Filesystem path")
    source_type: str = Field(
        default="local", max_length=50,
        description="local | github | org | shared",
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Bounded, evidence-only metadata (never CoT)",
    )
    created_at: str = Field(default_factory=_utcnow_iso)

    def summary(self) -> Dict[str, Any]:
        return {
            "repository_id": self.repository_id[:64],
            "namespace_id": self.namespace_id[:64],
            "organization_id": self.organization_id[:64],
            "name": self.name[:120],
            "path": self.path[:200],
            "source_type": self.source_type,
            "created_at": self.created_at,
        }


# ── Phase 19A: Cross-Repository Edges ───────────────────────────


class CrossRepositoryEdge(BaseModel):
    """One explicit, deterministic relationship between two repositories.

    These are the only bridges that allow private engineering context from
    Repository A to reach Repository B — repository isolation is enforced
    by requiring an explicit cross-repository edge before any traversal
    crosses the boundary.
    """

    edge_id: str = Field(default_factory=lambda: f"CRX-{new_id().upper()[:10]}")
    source_repository_id: str = Field(description="Source repository namespace")
    target_repository_id: str = Field(description="Target repository namespace")
    relationship: EKRelationshipType = Field(
        description="Cross-repository relationship kind"
    )
    weight: float = Field(default=1.0, ge=0.0, le=1.0)
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Bounded edge metadata (shared library, package, component)",
    )
    provenance: Dict[str, Any] = Field(
        default_factory=dict,
        description="Provenance: who linked these repositories and why",
    )
    graph_version: int = Field(default=1)
    created_at: str = Field(default_factory=_utcnow_iso)

    def summary(self) -> Dict[str, Any]:
        return {
            "edge_id": self.edge_id,
            "source_repository_id": self.source_repository_id[:64],
            "target_repository_id": self.target_repository_id[:64],
            "relationship": self.relationship.value,
            "weight": round(self.weight, 2),
            "graph_version": self.graph_version,
            "created_at": self.created_at,
        }


# ── Phase 19A: Organization Metadata ────────────────────────────


class OrganizationMetadata(BaseModel):
    """Metadata for the organization knowledge graph itself."""

    organization_id: str = Field(default="default", max_length=64)
    name: str = Field(default="Organization Knowledge Graph", max_length=200)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_utcnow_iso)

    def summary(self) -> Dict[str, Any]:
        return {
            "organization_id": self.organization_id[:64],
            "name": self.name[:120],
            "created_at": self.created_at,
        }


class OrganizationGraphStats(BaseModel):
    """Summary statistics for the organization knowledge graph."""

    organization_id: str = Field(default="default")
    repository_count: int = Field(default=0)
    node_count: int = Field(default=0)
    edge_count: int = Field(default=0)
    cross_edge_count: int = Field(default=0)
    cross_relationship_types: Dict[str, int] = Field(default_factory=dict)
    repositories: List[str] = Field(default_factory=list)
    last_updated: str = Field(default="")

    def summary(self) -> Dict[str, Any]:
        return {
            "organization_id": self.organization_id[:64],
            "repository_count": self.repository_count,
            "node_count": self.node_count,
            "edge_count": self.edge_count,
            "cross_edge_count": self.cross_edge_count,
            "cross_relationship_types": dict(
                sorted(self.cross_relationship_types.items(), key=lambda x: -x[1])[:12]
            ),
            "repositories": self.repositories[:20],
            "last_updated": self.last_updated,
        }


# Re-export for convenience.
__all__ = [
    "MAX_NEIGHBORHOOD_NODES",
    "MAX_HISTORY_ENTRIES",
    "MAX_QUERY_RESULTS",
    "MAX_EDGES_PER_NODE",
    "MAX_NODES_PER_RUN_INGEST",
    "MAX_EXPLAIN_EVIDENCE",
    "DEFAULT_REPOSITORY_ID",
    "MAX_REPOSITORIES_PER_ORG",
    "MAX_CROSS_EDGES_PER_REPO",
    "EKNodeType",
    "EKRelationshipType",
    "EKNodeStatus",
    "EKNode",
    "EKEdge",
    "GraphVersion",
    "RetrievalStrategy",
    "QueryScope",
    "RetrievalPlan",
    "GraphQueryResult",
    "GraphStats",
    "NodeHistoryEntry",
    "NodeHistory",
    "RepositoryNamespace",
    "CrossRepositoryEdge",
    "OrganizationMetadata",
    "OrganizationGraphStats",
]
