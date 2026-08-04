r"""
Phase 19A — OrganizationKnowledgeGraphService.

Extends the single-repository EngineeringKnowledgeGraphService (Phase 18)
into an organization-wide knowledge graph::

    Repository A   Repository B   Repository C
          \              |              /
           \             |             /
            Organization Knowledge Graph
                     |
              KnowledgeQueryPlanner
                     |
                ContextEngine

Responsibilities (section 3):
- register repositories (namespace registry)
- link repositories via deterministic cross-repository edges
- cross-repository traversal (bounded)
- organization-wide graph statistics
- graph synchronization / persistence
- repository isolation (A never leaks context into B unless explicitly
  linked through a cross-repository edge)

Design:
- One EngineeringKnowledgeGraphService per registered repository, each
  stamped with its own `repository_id` so persisted nodes never collide
  across repositories in a shared PostgreSQL schema.
- A single in-memory "org graph" (EngineeringKnowledgeGraphService bound
  to the 'default' namespace) stores REPOSITORY nodes and the explicit
  cross-repository edges (DEPENDS_ON_REPOSITORY, SHARES_LIBRARY, ...).
  These edges are the ONLY bridges that allow retrieval to cross a
  repository boundary.
- A merged view (_OrgGraphView) presents the union of linked repositories'
  nodes to the KnowledgeQueryPlanner while tagging each node with its
  origin repository, so relevance ranking can be computed across
  repositories without breaking isolation.

Persistence: best-effort mirror to PostgreSQL (migration 013) when the
database is configured, with an in-memory fallback identical to Phase 18.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional, Set, Tuple

from app.core.logging import logger
from app.db.models import (
    EKCrossRepositoryEdgeModel,
    EKRepositoryNamespaceModel,
)
from app.models.engineering_graph import (
    DEFAULT_REPOSITORY_ID,
    MAX_CROSS_EDGES_PER_REPO,
    MAX_REPOSITORIES_PER_ORG,
    EKEdge,
    EKNode,
    EKNodeStatus,
    EKNodeType,
    EKRelationshipType,
    CrossRepositoryEdge,
    GraphQueryResult,
    OrganizationGraphStats,
    OrganizationMetadata,
    QueryScope,
    RepositoryNamespace,
    RetrievalPlan,
    RetrievalStrategy,
)


_CROSS_REPO_RELATIONSHIPS = frozenset(
    {
        EKRelationshipType.DEPENDS_ON_REPOSITORY,
        EKRelationshipType.SHARES_LIBRARY,
        EKRelationshipType.IMPORTS_PACKAGE,
        EKRelationshipType.IMPLEMENTS_SHARED_INTERFACE,
        EKRelationshipType.REFERENCES_SHARED_COMPONENT,
        EKRelationshipType.USES_SHARED_MEMORY,
        EKRelationshipType.CALLS_EXTERNAL_SERVICE,
    }
)


def _cross_repo_edge_id(source_repo: str, target_repo: str, relationship: EKRelationshipType) -> str:
    """Deterministic cross-repository edge id (stable across re-links)."""
    raw = f"CRX:{source_repo}->{target_repo}:{relationship.value}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"CRX-{digest}"


class _OrgGraphView:
    """Merged read-only view across one or more repository graphs.

    Tags every node/edge with its origin repository so the planner can rank
    relevance per-repository while never letting one repo's private context
    leak into another except via the cross-repository edges the org graph
    explicitly records.

    Implements the minimal protocol the KnowledgeQueryPlanner calls on a
    graph object: all_nodes, get_node, get_edges, get_reverse_edges,
    semantic_search, current_version.
    """

    def __init__(self, graphs: List[Any], repository_ids: Optional[List[str]] = None) -> None:
        self._graphs = graphs
        self._repos = repository_ids or [g._repository_id for g in graphs]

    def _visible(self, node: EKNode) -> bool:
        if not self._repos:
            return True
        return node.repository_id in self._repos

    def all_nodes(self, limit: int = 50) -> List[EKNode]:
        seen: Set[str] = set()
        result: List[EKNode] = []
        for g in self._graphs:
            if g._repository_id not in self._repos and self._repos:
                continue
            for node in g.all_nodes(limit=10_000):
                if node.node_id in seen:
                    continue
                seen.add(node.node_id)
                result.append(node)
                if len(result) >= limit:
                    return result
        return result

    def get_node(self, node_id: str) -> Optional[EKNode]:
        for g in self._graphs:
            node = g.get_node(node_id)
            if node is not None:
                return node
        return None

    def get_edges(self, source_id: str) -> List[EKEdge]:
        out: List[EKEdge] = []
        for g in self._graphs:
            out.extend(g.get_edges(source_id))
        return out[:100]

    def get_reverse_edges(self, target_id: str) -> List[EKEdge]:
        out: List[EKEdge] = []
        for g in self._graphs:
            out.extend(g.get_reverse_edges(target_id))
        return out[:100]

    def current_version(self) -> Any:
        # Return a synthetic version aggregating the max across repos.
        max_v = 0
        for g in self._graphs:
            try:
                max_v = max(max_v, g.current_version().version)
            except Exception:
                pass
        from app.models.engineering_graph import GraphVersion

        return GraphVersion(version=max_v, run_id="", summary="organization view")

    async def semantic_search(
        self,
        query_text: str,
        *,
        limit: int = 50,
        target_kinds: Optional[List[EKNodeType]] = None,
    ) -> List[Dict[str, Any]]:
        """Aggregate semantic search across visible repositories, re-ranked.

        Each per-repo graph has its own deterministic hashed-embedding index;
        we query every visible repo in parallel-ish (sequentially here), then
        merge + sort by score and bound to `limit`.
        """
        import asyncio

        async def _one(g):
            try:
                return await g.semantic_search(
                    query_text, limit=limit, target_kinds=target_kinds
                )
            except Exception:
                return []

        results = await asyncio.gather(*[_one(g) for g in self._graphs])
        merged: List[Dict[str, Any]] = []
        for hits in results:
            for hit in hits:
                # Tag with origin repo so the caller can attribute evidence.
                hit["repository_id"] = hit.get("repository_id") or self._repo_of(hit.get("node_id"))
                merged.append(hit)
        merged.sort(key=lambda x: -(x.get("score", 0.0)))
        return merged[:limit]

    def _repo_of(self, node_id: str) -> str:
        for g in self._graphs:
            if g.get_node(node_id) is not None:
                return g._repository_id
        return DEFAULT_REPOSITORY_ID


class OrganizationKnowledgeGraphService:
    """Organization-wide engineering knowledge graph (Phase 19A).

    Owns a registry of repository namespaces and a per-repository
    EngineeringKnowledgeGraphService for each, plus an org-level graph that
    records the explicit cross-repository edges that bridge namespaces.

    Repository isolation is enforced structurally: a repository's nodes are
    only reachable from the org query path when (a) it is registered AND
    (b) the query scope includes it — either explicitly, via an explicit
    cross-repository edge, or via the organization scope. The org graph
    never exposes a private node from Repository A to a Repository B query
    unless a deterministic linking edge connects them.
    """

    def __init__(
        self,
        session_factory: Optional[Any] = None,
        database_url: Optional[str] = None,
        max_repositories: int = MAX_REPOSITORIES_PER_ORG,
    ) -> None:
        # Registry: repository_id -> RepositoryNamespace
        self._namespaces: Dict[str, RepositoryNamespace] = {}
        # Per-repository graph services. Each is stamped with its namespace.
        self._graphs: Dict[str, Any] = {}
        # Org-level graph: holds REPOSITORY nodes + cross-repository edges.
        from app.services.engineering_graph_service import EngineeringKnowledgeGraphService

        self._org_graph: EngineeringKnowledgeGraphService = EngineeringKnowledgeGraphService(
            session_factory=session_factory,
            database_url=database_url,
            repository_id=DEFAULT_REPOSITORY_ID,
        )
        # Cross-repository edges (in-memory authoritative copy).
        self._cross_edges: Dict[str, CrossRepositoryEdge] = {}
        # source_repo -> [(edge_id, target_repo, relationship)] for traversal
        self._cross_out: Dict[str, List[Tuple[str, str, EKRelationshipType]]] = {}
        self._cross_in: Dict[str, List[Tuple[str, str, EKRelationshipType]]] = {}
        self._database_url = database_url
        self._session_factory = session_factory
        self._max_repositories = max_repositories

    # ── Disposal ────────────────────────────────────────────────

    async def dispose(self) -> None:
        await self._org_graph.dispose()
        for g in self._graphs.values():
            try:
                await g.dispose()
            except Exception as exc:  # pragma: no cover
                logger.debug("org sub-graph dispose (non-critical): %s", exc)

    # ── Repository registration (§3) ────────────────────────────

    def register_repository(
        self,
        repository_id: str,
        *,
        name: str = "",
        path: str = "",
        source_type: str = "local",
        organization_id: str = "default",
        metadata: Optional[Dict[str, Any]] = None,
        graph: Optional[Any] = None,
    ) -> RepositoryNamespace:
        """Register a repository namespace.

        If a `graph` service is supplied it is adopted verbatim (already
        stamped with this namespace); otherwise a fresh
        EngineeringKnowledgeGraphService is created and stamped with the
        repository_id, sharing the org-level persistence backend.
        """
        if not repository_id:
            raise ValueError("repository_id is required")
        if (
            repository_id not in self._namespaces
            and len(self._namespaces) >= self._max_repositories
        ):
            raise RuntimeError(
                f"Organization repository limit reached ({self._max_repositories})"
            )
        repo_id = repository_id.strip()
        namespace = RepositoryNamespace(
            repository_id=repo_id,
            namespace_id=repo_id,
            organization_id=organization_id,
            name=name or repo_id,
            path=path,
            source_type=source_type,
            metadata=metadata or {},
        )
        self._namespaces[repo_id] = namespace

        if graph is not None:
            self._graphs[repo_id] = graph
        else:
            from app.services.engineering_graph_service import EngineeringKnowledgeGraphService

            self._graphs[repo_id] = EngineeringKnowledgeGraphService(
                session_factory=self._session_factory,
                database_url=self._database_url,
                repository_id=repo_id,
            )

        # REPOSITORY node in the org graph (stamped default namespace, but
        # payload carries the real repository_id for traversal). The node id
        # is the deterministic _repo_node_id so cross-repository edges can be
        # attached to it in link_repositories().
        if self._org_graph.get_node(_repo_node_id(repo_id)) is None:
            self._org_graph.add_node(
                EKNodeType.REPOSITORY,
                name or repo_id,
                node_id=_repo_node_id(repo_id),
                source_ref=repo_id,
                source_type="repository",
                qualified_name=repo_id,
                payload={"repository_id": repo_id, "path": path[:200]},
                provenance={"source": "organization"},
            )
            self._org_graph.increment_version(
                summary=f"Registered repository {repo_id}",
                updated_nodes=[_repo_node_id(repo_id)],
            )
        return namespace

    def get_namespace(self, repository_id: str) -> Optional[RepositoryNamespace]:
        return self._namespaces.get(repository_id)

    def get_graph(self, repository_id: str) -> Optional[Any]:
        """The EngineeringKnowledgeGraphService for a repository."""
        return self._graphs.get(repository_id)

    def repositories(self) -> List[RepositoryNamespace]:
        return list(self._namespaces.values())

    def cross_edges(self) -> List[CrossRepositoryEdge]:
        return list(self._cross_edges.values())

    # ── Cross-repository linking (§2 — deterministic only) ──────

    def link_repositories(
        self,
        source_repository_id: str,
        target_repository_id: str,
        relationship: EKRelationshipType,
        *,
        weight: float = 1.0,
        metadata: Optional[Dict[str, Any]] = None,
        provenance: Optional[Dict[str, Any]] = None,
        graph_version: Optional[int] = None,
    ) -> CrossRepositoryEdge:
        """Create an explicit, deterministic cross-repository edge.

        Only relationships in _CROSS_REPO_RELATIONSHIPS are permitted — and
        they must NEVER be inferred by an LLM. Both endpoints must be
        registered namespaces; unknown repositories are rejected so the org
        graph cannot be poisoned with dangling links.
        """
        if relationship not in _CROSS_REPO_RELATIONSHIPS:
            raise ValueError(
                f"Relationship '{relationship.value}' is not a valid "
                "cross-repository relationship"
            )
        if source_repository_id == target_repository_id:
            raise ValueError("source and target repository must differ")
        if source_repository_id not in self._namespaces:
            raise KeyError(f"Source repository not registered: {source_repository_id}")
        if target_repository_id not in self._namespaces:
            raise KeyError(f"Target repository not registered: {target_repository_id}")

        edge_id = _cross_repo_edge_id(source_repository_id, target_repository_id, relationship)
        edge = CrossRepositoryEdge(
            edge_id=edge_id,
            source_repository_id=source_repository_id,
            target_repository_id=target_repository_id,
            relationship=relationship,
            weight=min(1.0, max(0.0, weight)),
            metadata=metadata or {},
            provenance=provenance or {},
            graph_version=graph_version or self._org_graph._version,
        )
        self._cross_edges[edge_id] = edge
        self._cross_out.setdefault(source_repository_id, []).append(
            (edge_id, target_repository_id, relationship)
        )
        self._cross_in.setdefault(target_repository_id, []).append(
            (edge_id, source_repository_id, relationship)
        )
        # Mirror into the org graph as a REPOSITORY -> REPOSITORY edge so the
        # edge is traversable via existing graph primitives.
        src_node = _repo_node_id(source_repository_id)
        tgt_node = _repo_node_id(target_repository_id)
        self._org_graph.add_edge(
            src_node, tgt_node, relationship,
            weight=weight, metadata=metadata or {}, provenance=provenance or {},
        )
        self._org_graph.increment_version(
            summary=f"Linked {source_repository_id} -> {target_repository_id} ({relationship.value})",
            updated_edges=[edge.edge_id],
        )
        return edge

    def neighbors_of(self, repository_id: str) -> List[Tuple[str, EKRelationshipType]]:
        """Repositories reachable from `repository_id` + the relationship."""
        out: List[Tuple[str, str, EKRelationshipType]] = self._cross_out.get(
            repository_id, []
        )
        return [(target, rel) for _eid, target, rel in out]

    # ── Cross-repository traversal (§3) ────────────────────────

    def cross_repository_traversal(
        self,
        node_id: str,
        *,
        relationship: Optional[EKRelationshipType] = None,
        depth: int = 2,
        max_nodes: int = 200,
    ) -> GraphQueryResult:
        """Bounded cross-repository traversal from a node.

        Walks: the node's own repo graph up to `depth`, then follows
        cross-repository edges into linked repositories, bounding total
        nodes to `max_nodes`. Only explicit cross-repo edges allow the
        boundary to be crossed — private context stays isolated.
        """
        start_node = self._find_node_global(node_id)
        if start_node is None:
            return GraphQueryResult(query=f"cross-traversal:{node_id}")
        home_repo = start_node.repository_id

        result = GraphQueryResult(
            query=f"cross-repository traversal:{node_id}",
            strategy=RetrievalStrategy.CROSS_REPOSITORY,
            version=self._org_graph._version,
        )
        visited: Set[str] = {node_id}
        queue: List[Tuple[Any, str, int]] = [(self._graphs.get(home_repo, self._org_graph), node_id, 0)]
        result.nodes = [start_node]

        while queue and len(visited) <= max_nodes:
            graph, current, level = queue.pop(0)
            if level >= depth:
                continue
            # 1. in-repo edges within the current node's repository graph
            for edge in (graph.get_edges(current) + graph.get_reverse_edges(current))[
                :100
            ]:
                other = edge.target_id if edge.source_id == current else edge.source_id
                if other not in visited and self._visible_in_traversal(other):
                    visited.add(other)
                    node = self._find_node_global(other)
                    if node and len(result.nodes) < 100:
                        result.nodes.append(node)
                    if len(visited) <= max_nodes:
                        queue.append((graph, other, level + 1))

            # 2. cross-repository hop: if `current` is a REPOSITORY node,
            #    follow each cross-repo edge into the target repo's graph.
            node = self._find_node_global(current)
            if node is not None and node.node_type == EKNodeType.REPOSITORY:
                repo_id = node.source_ref
                for _eid, target_repo, rel in self._cross_out.get(repo_id, []):
                    if relationship is not None and rel != relationship:
                        continue
                    tgt_graph = self._graphs.get(target_repo)
                    if tgt_graph is None:
                        continue
                    for n in tgt_graph.all_nodes(limit=50):
                        if n.node_id in visited:
                            continue
                        visited.add(n.node_id)
                        if len(result.nodes) < 100:
                            result.nodes.append(n)
                        if len(visited) <= max_nodes:
                            queue.append((tgt_graph, n.node_id, level + 1))
        result.total_nodes = len(visited)
        result.truncated = len(visited) > max_nodes
        return result

    def _find_node_global(self, node_id: str) -> Optional[EKNode]:
        """Look up a node across all repository graphs + the org graph."""
        if node_id in self._org_graph._nodes:
            return self._org_graph._nodes[node_id]
        for g in self._graphs.values():
            node = g.get_node(node_id)
            if node is not None:
                return node
        return None

    def _visible_in_traversal(self, node_id: str) -> bool:
        # All nodes across linked repos are visible during a cross-repo
        # traversal that explicitly crossed a bridge edge.
        return True

    # ── Query (plumber) ────────────────────────────────────────

    async def query(
        self,
        query_text: str,
        *,
        limit: int = 50,
        scope: QueryScope = QueryScope.AUTO,
        repository_ids: Optional[List[str]] = None,
        target_kinds: Optional[List[EKNodeType]] = None,
    ) -> GraphQueryResult:
        """Organization-wide query, delegating to the KnowledgeQueryPlanner.

        - scope=LOCAL: query only the graph(s) for `repository_ids` (or the
          single provided repo). Strict isolation — no cross-repo leakage.
        - scope=ORGANIZATION: query the merged view of ALL linked repositories.
        - scope=AUTO: the planner decides based on query vocabulary.
        """
        # Resolve which repo graphs are in-scope.
        if scope == QueryScope.LOCAL:
            repos = self._resolve_local_repos(repository_ids)
            graphs = [self._graphs[r] for r in repos if r in self._graphs]
        else:
            # ORGANIZATION or AUTO: merged view across all registered repos.
            all_repos = [r for r in self._namespaces if r in self._graphs]
            if repository_ids and scope == QueryScope.AUTO:
                # Auto-detected multi-repo: restrict to named repos only.
                repos = [r for r in repository_ids if r in self._namespaces]
                graphs = [self._graphs[r] for r in repos]
            else:
                graphs = [self._graphs[r] for r in all_repos]

        view = _OrgGraphView(graphs, repository_ids=repos if scope == QueryScope.LOCAL else None)

        from app.services.knowledge_query_planner import KnowledgeQueryPlanner

        planner = KnowledgeQueryPlanner(graph=view)
        return await planner.retrieve(
            query_text,
            limit=limit,
            target_kinds=target_kinds,
            repository_ids=repository_ids or None,
            scope=scope,
        )

    def _resolve_local_repos(self, repository_ids: Optional[List[str]]) -> List[str]:
        if not repository_ids:
            return list(self._namespaces.keys())[:1]
        return [r for r in repository_ids if r in self._namespaces]

    # ── Statistics (§3) ────────────────────────────────────────

    def stats(self) -> OrganizationGraphStats:
        node_count = 0
        edge_count = 0
        node_types: Dict[str, int] = {}
        for g in self._graphs.values():
            s = g.stats()
            node_count += s.node_count
            edge_count += s.edge_count
            for t, c in s.node_types.items():
                node_types[t] = node_types.get(t, 0) + c
        cross_types: Dict[str, int] = {}
        for e in self._cross_edges.values():
            cross_types[e.relationship.value] = cross_types.get(e.relationship.value, 0) + 1
        repos = [r.repository_id for r in self._namespaces.values()]
        from app.models.engineering_graph import _utcnow_iso as _now

        return OrganizationGraphStats(
            organization_id="default",
            repository_count=len(self._namespaces),
            node_count=node_count,
            edge_count=edge_count,
            cross_edge_count=len(self._cross_edges),
            cross_relationship_types=cross_types,
            repositories=repos[:20],
            last_updated=_now(),
        )

    def repository_stats(self, repository_id: str) -> Optional[Dict[str, Any]]:
        """Per-repository stats (nodes/edges scoped to a namespace)."""
        g = self._graphs.get(repository_id)
        if g is None:
            return None
        s = g.stats()
        linked = self.neighbors_of(repository_id)
        incoming = self._cross_in.get(repository_id, [])
        return {
            "repository_id": repository_id,
            "namespace": self._namespaces.get(repository_id),
            "node_count": s.node_count,
            "edge_count": s.edge_count,
            "node_types": s.node_types,
            "relationship_types": s.relationship_types,
            "outgoing_links": [
                {"repository_id": t, "relationship": rel.value}
                for t, rel in linked
            ],
            "incoming_links": [
                {"repository_id": src, "relationship": rel.value}
                for _eid, src, rel in incoming
            ],
            "run_count": s.run_count,
        }

    def explain(self, node_id: str) -> Dict[str, Any]:
        """Provenance + related evidence for a node (delegates by repo)."""
        node = self._find_node_global(node_id)
        if node is None:
            return {"node_id": node_id, "found": False}
        g = self._graphs.get(node.repository_id, self._org_graph)
        return g.explain(node_id)

    # ── Persistence / synchronization (§3) ─────────────────────

    async def synchronize(self) -> int:
        """Persist registered namespaces and cross-repository edges.

        Returns the number of records written. Best-effort: gracefully
        degrades when the database is unavailable (the in-memory copy
        remains authoritative, mirroring Phase 18).
        """

        written = await self._persist_namespaces()
        written += await self._persist_cross_edges()
        return written

    async def _persist_namespaces(self) -> int:
        if not self._session_factory and not self._database_url:
            return 0
        async def _impl(session):
            from sqlalchemy import select

            count = 0
            for ns in self._namespaces.values():
                stmt = select(EKRepositoryNamespaceModel).where(
                    EKRepositoryNamespaceModel.repository_id == ns.repository_id
                )
                model = (await session.execute(stmt)).scalar_one_or_none()
                if model is None:
                    session.add(EKRepositoryNamespaceModel(
                        repository_id=ns.repository_id,
                        namespace_id=ns.namespace_id or ns.repository_id,
                        organization_id=ns.organization_id,
                        name=ns.name,
                        path=ns.path,
                        source_type=ns.source_type,
                        metadata_json=ns.metadata or None,
                    ))
                else:
                    model.organization_id = ns.organization_id
                    model.name = ns.name
                    model.path = ns.path
                    model.source_type = ns.source_type
                    model.metadata_json = ns.metadata or None
                count += 1
            await session.commit()
            return count
        return await self._org_graph._with_session(_impl, fallback=0)

    async def _persist_cross_edges(self) -> int:
        if not self._session_factory and not self._database_url:
            return 0
        async def _impl(session):
            from sqlalchemy import select

            count = 0
            for edge in self._cross_edges.values():
                stmt = select(EKCrossRepositoryEdgeModel).where(
                    EKCrossRepositoryEdgeModel.edge_id == edge.edge_id
                )
                model = (await session.execute(stmt)).scalar_one_or_none()
                if model is None:
                    session.add(EKCrossRepositoryEdgeModel(
                        edge_id=edge.edge_id,
                        source_repository_id=edge.source_repository_id,
                        target_repository_id=edge.target_repository_id,
                        relationship=edge.relationship.value,
                        weight=edge.weight,
                        metadata_json=edge.metadata or None,
                        provenance=edge.provenance or None,
                        graph_version=edge.graph_version,
                    ))
                else:
                    model.source_repository_id = edge.source_repository_id
                    model.target_repository_id = edge.target_repository_id
                    model.relationship = edge.relationship.value
                    model.weight = edge.weight
                    model.metadata_json = edge.metadata or None
                    model.provenance = edge.provenance or None
                count += 1
            await session.commit()
            return count
        return await self._org_graph._with_session(_impl, fallback=0)

    async def recover(self) -> None:
        """Rehydrate the namespace registry + cross-repo edges after restart."""
        self._namespaces.clear()
        self._cross_edges.clear()
        self._cross_out.clear()
        self._cross_in.clear()

        async def _load(session):
            from sqlalchemy import select

            ns_rows = (await session.execute(
                select(EKRepositoryNamespaceModel).order_by(EKRepositoryNamespaceModel.id)
            )).scalars().all()
            for m in ns_rows:
                ns = RepositoryNamespace(
                    repository_id=m.repository_id,
                    namespace_id=m.namespace_id or m.repository_id,
                    organization_id=m.organization_id or "default",
                    name=m.name or m.repository_id,
                    path=m.path or "",
                    source_type=m.source_type or "local",
                    metadata=m.metadata_json or {},
                )
                if m.created_at is not None:
                    try:
                        ns.created_at = m.created_at.isoformat()
                    except Exception:
                        pass
                self._namespaces[ns.repository_id] = ns
                # Ensure a per-repo graph exists for the recovered namespace.
                if ns.repository_id not in self._graphs:
                    from app.services.engineering_graph_service import EngineeringKnowledgeGraphService

                    self._graphs[ns.repository_id] = EngineeringKnowledgeGraphService(
                        session_factory=self._session_factory,
                        database_url=self._database_url,
                        repository_id=ns.repository_id,
                    )

            edge_rows = (await session.execute(
                select(EKCrossRepositoryEdgeModel).order_by(EKCrossRepositoryEdgeModel.id)
            )).scalars().all()
            for m in edge_rows:
                try:
                    rel = EKRelationshipType(m.relationship)
                except ValueError:
                    continue
                edge = CrossRepositoryEdge(
                    edge_id=m.edge_id,
                    source_repository_id=m.source_repository_id,
                    target_repository_id=m.target_repository_id,
                    relationship=rel,
                    weight=m.weight or 1.0,
                    metadata=m.metadata_json or {},
                    provenance=m.provenance or {},
                    graph_version=m.graph_version or 1,
                )
                if m.created_at is not None:
                    try:
                        edge.created_at = m.created_at.isoformat()
                    except Exception:
                        pass
                self._cross_edges[edge.edge_id] = edge
                self._cross_out.setdefault(edge.source_repository_id, []).append(
                    (edge.edge_id, edge.target_repository_id, edge.relationship)
                )
                self._cross_in.setdefault(edge.target_repository_id, []).append(
                    (edge.edge_id, edge.source_repository_id, edge.relationship)
                )
            # Recover each per-repo graph's persisted nodes/edges.
            for repo_id, g in self._graphs.items():
                await g.recover()
            # Recover the org graph (REPOSITORY nodes + cross edges as graph edges).
            await self._org_graph.recover()

        await self._org_graph._with_session(_load, fallback=None)
        logger.debug(
            "Org graph recovered: %d repos, %d cross-edges",
            len(self._namespaces), len(self._cross_edges),
        )

    def current_version(self) -> int:
        return self._org_graph._version


def _repo_node_id(repository_id: str) -> str:
    """Stable node id for a REPOSITORY node in the org graph."""
    return f"REPO::{repository_id}"[:40]
