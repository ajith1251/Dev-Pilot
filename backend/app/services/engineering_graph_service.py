"""
Phase 18 — EngineeringKnowledgeGraphService.

A unified, higher-abstraction knowledge layer above the existing stores
(Phase 12 semantic graph, 13 repository memory, 15 collaboration,
16 autonomy, 17 reasoning). The EKG stores engineering entities as typed,
temporal NODES and links them with provenance-bearing EDGES.

Responsibilities (§7):
- node creation / edge creation
- graph updates / graph versioning (incremental, never full rebuild)
- graph traversal (bounded neighborhood / dependencies / dependents)
- provenance (every node retains its evidence origins)
- historical traversal (temporal node history across versions)
- graph statistics

Persistence: mirrors to PostgreSQL (ekg_nodes / ekg_edges / ekg_versions,
migration 011) when available, with an in-memory copy so the system
degrades gracefully when the DB is down. Recovery: `recover()` rehydrates
persisted state after restart.

Security invariant: nodes/edges expose ONLY verified engineering evidence,
decisions, and provenance — never chain-of-thought.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any, Callable, Dict, List, Optional, Set

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import logger
from app.db.models import EKEdgeModel, EKNodeModel, GraphVersionModel
from app.db.session import create_session_factory
from app.models.engineering_graph import (
    MAX_EDGES_PER_NODE,
    MAX_EXPLAIN_EVIDENCE,
    MAX_HISTORY_ENTRIES,
    MAX_NEIGHBORHOOD_NODES,
    MAX_NODES_PER_RUN_INGEST,
    MAX_QUERY_RESULTS,
    SUMMARY_MAX_LEN,
    DEFAULT_REPOSITORY_ID,
    EKEdge,
    EKNode,
    EKNodeStatus,
    EKNodeType,
    EKRelationshipType,
    GraphQueryResult,
    GraphStats,
    GraphVersion,
    NodeHistory,
    NodeHistoryEntry,
    QueryScope,
    RetrievalStrategy,
)

# ── Stable node id derivation ────────────────────────────────────
# Deterministic ids so re-ingesting the same entity (e.g. a run that
# already created a consensus node) does not duplicate nodes.

_NODE_ID_PREFIX = {
    EKNodeType.REPOSITORY: "REPO",
    EKNodeType.FOLDER: "FOLDER",
    EKNodeType.FILE: "FILE",
    EKNodeType.MODULE: "MODULE",
    EKNodeType.PACKAGE: "PKG",
    EKNodeType.CLASS: "CLS",
    EKNodeType.INTERFACE: "IFACE",
    EKNodeType.FUNCTION: "FN",
    EKNodeType.METHOD: "M",
    EKNodeType.REQUIREMENT: "REQ",
    EKNodeType.ACCEPTANCE_CRITERION: "AC",
    EKNodeType.IMPLEMENTATION_PLAN: "PLAN",
    EKNodeType.PLAN_VERSION: "PLANV",
    EKNodeType.GOAL: "GOAL",
    EKNodeType.PATCH: "PATCH",
    EKNodeType.COMMIT_CANDIDATE: "COMMIT",
    EKNodeType.TEST: "TEST",
    EKNodeType.TEST_SUITE: "TSUITE",
    EKNodeType.REVIEW_FINDING: "FIND",
    EKNodeType.QUALITY_GATE: "GATE",
    EKNodeType.EVIDENCE: "EV",
    EKNodeType.CONSENSUS: "CS",
    EKNodeType.CONTRADICTION: "CD",
    EKNodeType.NOTEBOOK_ENTRY: "NB",
    EKNodeType.DECISION: "DEC",
    EKNodeType.RUN: "RUN",
    EKNodeType.AGENT: "AGT",
    EKNodeType.REPOSITORY_MEMORY: "MEM",
}


_NODE_ID_MAX = 40  # must fit ekg_nodes.node_id / ekg_edges.source_id (String(40))


def _stable_id(
    node_type: EKNodeType, *parts: str, repository_id: str = ""
) -> str:
    """Deterministic stable node id: PREFIX::part1::part2...

    Bounded to _NODE_ID_MAX chars so ids always fit the String(40)
    ekg_nodes.node_id / ekg_edges.source_id columns (a truncated-but-
    deterministic suffix keeps the id stable across re-ingests).

    Phase 19A: when a repository namespace is provided (and it differs from
    the backward-compatible "default" namespace), the repository is folded
    into the id so identical file/symbol names across repositories produce
    DIFFERENT node ids — preventing cross-repository collisions.
    """
    prefix = _NODE_ID_PREFIX.get(node_type, "EKN")
    cleaned = [p.replace("::", "_")[:120] for p in parts if p]
    if repository_id and repository_id != DEFAULT_REPOSITORY_ID:
        cleaned.insert(0, f"repo:{repository_id}")
    suffix = "::".join(cleaned) if cleaned else "unknown"
    raw = f"{prefix}::{suffix}"
    if len(raw) <= _NODE_ID_MAX:
        return raw
    # Too long: keep a readable head plus a deterministic short digest so the
    # id is stable AND unique-enough for upsert/dedup semantics.
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
    head = f"{prefix}::{suffix}"[: _NODE_ID_MAX - 11]  # room for :: + digest
    return f"{head}::{digest}"[: _NODE_ID_MAX]


class EngineeringKnowledgeGraphService:
    """Unified engineering knowledge graph (§7).

    Phase 19 semantic layer: node payloads are embedded into a
    similarity-preserving vector space (deterministic hashed n-gram
    provider by default, no API) and searched with cosine similarity.
    The index is derived in-memory from node text, so it survives restart
    deterministically; when pgvector is available the vectors are ALSO
    mirrored to ekg_embeddings (migration 012) as a durable copy.
    """

    _SEMANTIC_MAX_NODES = 2000  # bounded index size

    def __init__(
        self,
        session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
        database_url: Optional[str] = None,
        embedding_provider: Optional[Any] = None,
        repository_id: Optional[str] = None,
    ) -> None:
        self._factory = session_factory
        # Phase 19A — repository namespace for this graph. When set, every
        # node/edge is stamped with it, stable ids fold it in, and recovery
        # only rehydrates nodes from THIS namespace (strict isolation across
        # repositories persisted in a shared PostgreSQL schema).
        self._repository_id = repository_id or DEFAULT_REPOSITORY_ID
        # Explicit URL mirrors PostgresRunStore: contract tests and the demo
        # point at TEST_DATABASE_URL while the default factory only reads
        # DATABASE_URL. When set, an owned engine is created and tracked so
        # it can be disposed (no pool leak per service instance).
        self._database_url = database_url
        self._owned_engine: Optional[Any] = None
        # In-memory adjacency (authoritative during the process)
        self._nodes: Dict[str, EKNode] = {}
        self._edges: Dict[str, Dict[str, List[EKEdge]]] = {}
        # source_id -> target_id -> edges
        self._reverse: Dict[str, Dict[str, List[EKEdge]]] = {}
        # target_id -> source_id -> edges
        self._versions: List[GraphVersion] = []
        self._node_history: Dict[str, List[NodeHistoryEntry]] = {}
        self._version = 1
        # Phase 19 semantic index: node_id -> embedding vector (derived,
        # bounded). Lazily rebuilt on demand so restart recovery is exact.
        self._embedding_provider = embedding_provider
        self._embedder: Optional[Any] = None
        self._semantic: Dict[str, List[float]] = {}
        self._pg_ok: Optional[bool] = None

    # ── Factory access ──────────────────────────────────────────

    def _get_factory(self) -> Optional[async_sessionmaker[AsyncSession]]:
        if self._factory is None:
            try:
                if self._database_url:
                    from app.db.database import create_async_engine

                    engine = create_async_engine(self._database_url)
                    if engine is None:
                        raise RuntimeError(
                            f"Failed to create engine for {self._database_url}"
                        )
                    self._owned_engine = engine
                    self._factory = create_session_factory(engine=engine)
                else:
                    self._factory = create_session_factory()
            except Exception as exc:
                logger.debug("EKG DB unavailable (in-memory): %s", exc)
                self._factory = None
        return self._factory

    async def dispose(self) -> None:
        """Dispose an owned engine (no-op when using the shared engine)."""
        if self._owned_engine is not None:
            try:
                await self._owned_engine.dispose()
            except Exception as exc:
                logger.debug("EKG dispose (non-critical): %s", exc)
            self._owned_engine = None
            self._factory = None

    async def _with_session(self, callback: Callable, fallback: Any = None) -> Any:
        factory = self._get_factory()
        if factory is None:
            return fallback
        try:
            async with factory() as session:
                return await callback(session)
        except Exception as exc:
            logger.debug("EKG DB op failed (in-memory fallback): %s", exc)
            return fallback

    # ── Node operations ─────────────────────────────────────────

    def add_node(
        self,
        node_type: EKNodeType,
        name: str,
        *,
        node_id: Optional[str] = None,
        qualified_name: str = "",
        kind: str = "",
        source_ref: str = "",
        source_type: str = "",
        payload: Optional[Dict[str, Any]] = None,
        provenance: Optional[Dict[str, Any]] = None,
        graph_version: Optional[int] = None,
        repository_id: Optional[str] = None,
    ) -> EKNode:
        """Create or replace a node (upsert by stable id)."""
        repo_id = repository_id or self._repository_id
        nid = node_id or _stable_id(
            node_type, source_type, source_ref, name, repository_id=repo_id
        )
        existing = self._nodes.get(nid)
        now_version = graph_version or self._version
        node = EKNode(
            node_id=nid,
            node_type=node_type,
            name=name[:200],
            qualified_name=qualified_name[:500],
            kind=kind[:50],
            source_ref=source_ref[:200],
            source_type=source_type[:50],
            payload=payload or {},
            provenance=provenance or {},
            status=EKNodeStatus.ACTIVE,
            graph_version=now_version,
            repository_id=repo_id,
            created_at=existing.created_at if existing else _now_iso(),
            updated_at=_now_iso(),
        )
        if existing:
            self._record_history(existing)
        self._nodes[nid] = node
        return node

    def _record_history(self, node: EKNode) -> None:
        """Snapshot a node into its temporal history before mutation."""
        entry = NodeHistoryEntry(
            node_id=node.node_id,
            graph_version=node.graph_version,
            status=node.status,
            payload=node.payload,
            provenance=node.provenance,
            created_at=node.created_at,
        )
        hist = self._node_history.setdefault(node.node_id, [])
        hist.append(entry)
        if len(hist) > MAX_HISTORY_ENTRIES:
            self._node_history[node.node_id] = hist[-MAX_HISTORY_ENTRIES:]

    def get_node(self, node_id: str) -> Optional[EKNode]:
        return self._nodes.get(node_id)

    def find_nodes(
        self,
        *,
        node_type: Optional[EKNodeType] = None,
        name: Optional[str] = None,
        source_ref: Optional[str] = None,
        source_type: Optional[str] = None,
        repository_id: Optional[str] = None,
        limit: int = MAX_QUERY_RESULTS,
    ) -> List[EKNode]:
        """Find nodes by simple filters (in-memory index scan).

        Phase 19A: an optional `repository_id` filter enforces namespace
        isolation — only nodes belonging to the given repository are
        returned.
        """
        result: List[EKNode] = []
        for node in self._nodes.values():
            if node_type is not None and node.node_type != node_type:
                continue
            if name is not None and name.lower() not in node.name.lower():
                continue
            if source_ref is not None and node.source_ref != source_ref:
                continue
            if source_type is not None and node.source_type != source_type:
                continue
            # Phase 19A — namespace isolation filter.
            if repository_id is not None and node.repository_id != repository_id:
                continue
            result.append(node)
            if len(result) >= limit:
                break
        return result

    def all_nodes(self, limit: int = MAX_QUERY_RESULTS) -> List[EKNode]:
        return list(self._nodes.values())[:limit]

    # ── Edge operations ─────────────────────────────────────────

    def add_edge(
        self,
        source_id: str,
        target_id: str,
        relationship: EKRelationshipType,
        *,
        weight: float = 1.0,
        metadata: Optional[Dict[str, Any]] = None,
        provenance: Optional[Dict[str, Any]] = None,
        graph_version: Optional[int] = None,
    ) -> Optional[EKEdge]:
        """Create an edge; skip (dedup) if an identical edge exists."""
        if source_id not in self._nodes or target_id not in self._nodes:
            logger.debug("EKG edge skipped: missing endpoint %s -> %s", source_id, target_id)
            return None

        existing = self._edges.get(source_id, {}).get(target_id, [])
        for e in existing:
            if e.relationship == relationship:
                # Update weight/metadata in place, keep stable edge id.
                e.weight = min(1.0, max(0.0, weight))
                e.metadata = metadata or e.metadata
                return e

        # In-repository edges inherit the source node's namespace (Phase 19A).
        source_repo = self._nodes.get(source_id).repository_id
        edge = EKEdge(
            source_id=source_id,
            target_id=target_id,
            relationship=relationship,
            weight=min(1.0, max(0.0, weight)),
            metadata=metadata or {},
            provenance=provenance or {},
            graph_version=graph_version or self._version,
            repository_id=source_repo,
            created_at=_now_iso(),
        )
        self._edges.setdefault(source_id, {}).setdefault(target_id, []).append(edge)
        self._reverse.setdefault(target_id, {}).setdefault(source_id, []).append(edge)
        return edge

    def get_edges(
        self, source_id: str, target_id: Optional[str] = None
    ) -> List[EKEdge]:
        src = self._edges.get(source_id, {})
        if target_id:
            return src.get(target_id, [])[:MAX_EDGES_PER_NODE]
        result: List[EKEdge] = []
        for tid in src:
            result.extend(src[tid])
        return result[:MAX_EDGES_PER_NODE]

    def get_reverse_edges(self, target_id: str) -> List[EKEdge]:
        rev = self._reverse.get(target_id, {})
        result: List[EKEdge] = []
        for sid in rev:
            result.extend(rev[sid])
        return result[:MAX_EDGES_PER_NODE]

    # ── Traversal ───────────────────────────────────────────────

    def neighborhood(
        self,
        node_id: str,
        depth: int = 2,
        max_nodes: int = MAX_NEIGHBORHOOD_NODES,
    ) -> GraphQueryResult:
        """Bounded bidirectional BFS around a node (§11)."""
        result = GraphQueryResult(
            query=f"neighborhood:{node_id}",
            strategy=RetrievalStrategy.KNOWLEDGE_GRAPH,
            version=self._version,
        )
        if node_id not in self._nodes:
            return result

        visited: Set[str] = {node_id}
        queue: List[tuple[str, int]] = [(node_id, 0)]
        seen_edges: Set[tuple[str, str, str]] = set()
        root = self._nodes[node_id]
        result.nodes.append(root)
        result.total_nodes = 1

        while queue and len(visited) <= max_nodes:
            current, level = queue.pop(0)
            if level >= depth:
                continue

            for edge in (self.get_edges(current) + self.get_reverse_edges(current))[:MAX_EDGES_PER_NODE]:
                neighbor = edge.target_id if edge.source_id == current else edge.source_id
                ekey = (edge.edge_id, edge.source_id, edge.target_id)
                if ekey not in seen_edges:
                    seen_edges.add(ekey)
                    result.edges.append(edge)
                if neighbor in visited:
                    continue
                visited.add(neighbor)
                result.total_nodes += 1
                node = self._nodes.get(neighbor)
                if node and len(result.nodes) < MAX_QUERY_RESULTS:
                    result.nodes.append(node)
                if len(visited) <= max_nodes:
                    queue.append((neighbor, level + 1))

        result.truncated = len(visited) > max_nodes or len(seen_edges) > MAX_EDGES_PER_NODE
        return result

    def dependencies(self, node_id: str, depth: int = 3, max_nodes: int = 100) -> GraphQueryResult:
        """Outgoing traversal: what does this node depend on?"""
        result = GraphQueryResult(
            query=f"dependencies:{node_id}",
            strategy=RetrievalStrategy.KNOWLEDGE_GRAPH,
            version=self._version,
        )
        if node_id not in self._nodes:
            return result
        visited: Set[str] = {node_id}
        queue: List[tuple[str, int]] = [(node_id, 0)]
        seen_edges: Set[tuple[str, str, str]] = set()
        while queue and len(visited) <= max_nodes:
            current, level = queue.pop(0)
            if level >= depth:
                continue
            for edge in self.get_edges(current):
                ekey = (edge.edge_id, edge.source_id, edge.target_id)
                if ekey not in seen_edges:
                    seen_edges.add(ekey)
                    result.edges.append(edge)
                if edge.target_id in visited:
                    continue
                visited.add(edge.target_id)
                node = self._nodes.get(edge.target_id)
                if node and len(result.nodes) < MAX_QUERY_RESULTS:
                    result.nodes.append(node)
                queue.append((edge.target_id, level + 1))
        result.total_nodes = len(visited)
        result.truncated = len(visited) > max_nodes
        return result

    # ── History (§5) ────────────────────────────────────────────

    def history(self, node_id: str) -> NodeHistory:
        """Temporal history of a node across graph versions."""
        return NodeHistory(
            node_id=node_id,
            current=self._nodes.get(node_id),
            entries=self._node_history.get(node_id, [])[:MAX_HISTORY_ENTRIES],
        )

    # ── Explain (§9, §11) ───────────────────────────────────────

    def explain(self, node_id: str) -> Dict[str, Any]:
        """Provenance + related evidence for a node."""
        node = self._nodes.get(node_id)
        if node is None:
            return {"node_id": node_id, "found": False}
        incoming = self.get_reverse_edges(node_id)[:MAX_EXPLAIN_EVIDENCE]
        outgoing = self.get_edges(node_id)[:MAX_EXPLAIN_EVIDENCE]
        related: List[Dict[str, Any]] = []
        for edge in incoming + outgoing:
            other_id = edge.target_id if edge.source_id == node_id else edge.source_id
            other = self._nodes.get(other_id)
            related.append({
                "edge_id": edge.edge_id,
                "relationship": edge.relationship.value,
                "direction": "incoming" if edge.target_id == node_id else "outgoing",
                "node_id": other_id,
                "node_type": other.node_type.value if other else "unknown",
                "name": other.name if other else "",
                "source_ref": other.source_ref if other else "",
                "source_type": other.source_type if other else "",
            })
        history = self._node_history.get(node_id, [])[:MAX_HISTORY_ENTRIES]
        return {
            "node_id": node_id,
            "found": True,
            "node": node.summary(),
            "provenance": node.provenance,
            "payload": node.payload,
            "related": related,
            "history_entries": [h.summary() for h in history],
        }

    # ── Versioning (§6) ─────────────────────────────────────────

    def increment_version(
        self,
        *,
        run_id: str = "",
        summary: str = "",
        updated_nodes: Optional[List[str]] = None,
        updated_edges: Optional[List[str]] = None,
        superseded_node_ids: Optional[List[str]] = None,
    ) -> GraphVersion:
        """Bump the graph version and record what changed (incremental)."""
        self._version += 1
        version = GraphVersion(
            version=self._version,
            run_id=run_id,
            summary=summary[:SUMMARY_MAX_LEN],
            updated_nodes=(updated_nodes or [])[:MAX_NODES_PER_RUN_INGEST],
            updated_edges=(updated_edges or [])[:MAX_EDGES_PER_NODE],
            superseded_node_ids=(superseded_node_ids or [])[:MAX_NODES_PER_RUN_INGEST],
        )
        self._versions.append(version)
        # Mark superseded nodes
        for nid in version.superseded_node_ids:
            node = self._nodes.get(nid)
            if node:
                self._record_history(node)
                node.status = EKNodeStatus.SUPERSEDED
        return version

    def current_version(self) -> GraphVersion:
        return GraphVersion(
            version=self._version,
            run_id=self._versions[-1].run_id if self._versions else "",
            summary=self._versions[-1].summary if self._versions else "Initial graph",
        )

    def version_history(self, limit: int = 20) -> List[GraphVersion]:
        return self._versions[-limit:]

    # ── Stats ───────────────────────────────────────────────────

    def stats(self) -> GraphStats:
        node_types: Dict[str, int] = {}
        for node in self._nodes.values():
            node_types[node.node_type.value] = node_types.get(node.node_type.value, 0) + 1
        rel_types: Dict[str, int] = {}
        for src in self._edges.values():
            for tgt in src.values():
                for edge in tgt:
                    rel_types[edge.relationship.value] = rel_types.get(edge.relationship.value, 0) + 1
        run_ids = {
            n.source_ref for n in self._nodes.values()
            if n.node_type in (EKNodeType.RUN,) and n.source_ref
        }
        repo_ids = {
            n.source_ref for n in self._nodes.values()
            if n.node_type in (EKNodeType.REPOSITORY,) and n.source_ref
        }
        return GraphStats(
            version=self._version,
            node_count=len(self._nodes),
            edge_count=sum(
                len(tgt) for src in self._edges.values() for tgt in src.values()
            ),
            node_types=node_types,
            relationship_types=rel_types,
            run_count=len(run_ids),
            repository_count=len(repo_ids),
            last_updated=self._versions[-1].timestamp if self._versions else _now_iso(),
        )

    # ── Run ingestion (§10) ─────────────────────────────────────

    async def record_run(self, run: Any, reasoning_outcome: Optional[Dict[str, Any]] = None) -> GraphVersion:
        """Enrich the graph from a completed run.

        Links goals → plans → patches → tests → review → quality gate →
        notebook → consensus → memory into one temporal graph. Idempotent:
        re-ingesting the same run upserts nodes and dedups edges.
        """
        run_id = getattr(run, "run_id", "")
        if not run_id:
            return self.current_version()
        repo = getattr(run, "repository_path", None) or ""
        title = _safe_title(run)

        updated_nodes: List[str] = []
        updated_edges: List[str] = []

        # Run node
        run_node = self.add_node(
            EKNodeType.RUN, f"run:{run_id}", source_ref=run_id, source_type="run",
            qualified_name=run_id,
            payload={
                "status": getattr(getattr(run, "status", None), "value", "") or str(getattr(run, "status", "")),
                "title": title[:200],
                "repository": repo[:200],
            },
            provenance={"run_id": run_id, "source": "orchestration"},
        )
        updated_nodes.append(run_node.node_id)

        # Repository node + edge
        if repo:
            repo_name = repo.rstrip("/\\").split("/")[-1].split("\\")[-1] or repo
            repo_node = self.add_node(
                EKNodeType.REPOSITORY, repo_name, source_ref=repo_name, source_type="repository",
                qualified_name=repo_name, provenance={"run_id": run_id, "source": "orchestration"},
            )
            if self.add_edge(run_node.node_id, repo_node.node_id, EKRelationshipType.REFERENCES,
                             metadata={"run_id": run_id}):
                updated_edges.append(self._last_edge_id(run_node.node_id, repo_node.node_id))

        # Requirements → plan
        if getattr(run, "requirements", None):
            reqs = getattr(run.requirements, "requirements", None) or []
            for req in reqs[:10]:
                desc = getattr(req, "description", "")[:200]
                if not desc:
                    continue
                req_node = self.add_node(
                    EKNodeType.REQUIREMENT, desc[:100], source_ref=run_id, source_type="run",
                    payload={"description": desc}, provenance={"run_id": run_id, "source": "requirements"},
                )
                updated_nodes.append(req_node.node_id)

        plan = getattr(run, "plan", None)
        if plan is not None:
            plan_summary = (getattr(plan, "summary", None) or getattr(plan, "objective", "") or "")[:200]
            plan_node = self.add_node(
                EKNodeType.IMPLEMENTATION_PLAN, plan_summary[:100] or "implementation_plan",
                source_ref=run_id, source_type="run", qualified_name=plan_summary,
                payload={
                    "objective": (getattr(plan, "objective", "") or "")[:200],
                    "step_count": len(getattr(plan, "steps", None) or []),
                },
                provenance={"run_id": run_id, "source": "planning"},
            )
            updated_nodes.append(plan_node.node_id)

        # Patch
        patch_set = getattr(run, "patch_set", None)
        changed_files: List[str] = []
        if patch_set is not None:
            changes = getattr(patch_set, "changes", None) or []
            changed_files = [getattr(c, "path", "") for c in changes if getattr(c, "path", "")]
            patch_node = self.add_node(
                EKNodeType.PATCH, f"patch:{run_id}", source_ref=run_id, source_type="run",
                qualified_name=f"patch:{run_id}",
                payload={"files_changed": len(changed_files), "files": changed_files[:20]},
                provenance={"run_id": run_id, "source": "coding"},
            )
            updated_nodes.append(patch_node.node_id)
            for fpath in changed_files[:10]:
                file_node = self.add_node(
                    EKNodeType.FILE, fpath.split("/")[-1], source_ref=fpath, source_type="file",
                    qualified_name=fpath, provenance={"run_id": run_id, "source": "patch"},
                )
                updated_nodes.append(file_node.node_id)
                if self.add_edge(patch_node.node_id, file_node.node_id, EKRelationshipType.MODIFIES,
                                 metadata={"run_id": run_id}):
                    updated_edges.append(self._last_edge_id(patch_node.node_id, file_node.node_id))

        # Tests
        test_result = getattr(run, "test_result", None)
        if test_result is not None:
            status = getattr(test_result, "status", None)
            status_v = getattr(status, "value", str(status))
            # Phase 19b: persist which test files ran so replans can drive
            # smart test selection from EKG impact edges (patch → test).
            test_files = self._extract_test_files(run)
            test_node = self.add_node(
                EKNodeType.TEST_SUITE, f"tests:{run_id}", source_ref=run_id, source_type="run",
                qualified_name=f"tests:{run_id}",
                payload={
                    "status": status_v,
                    "tests_total": getattr(test_result, "tests_total", 0),
                    "tests_failed": getattr(test_result, "tests_failed", 0),
                    "test_files": test_files,
                },
                provenance={"run_id": run_id, "source": "testing"},
            )
            updated_nodes.append(test_node.node_id)
            if patch_node := self._find_run_node(run_id, EKNodeType.PATCH):
                if self.add_edge(patch_node.node_id, test_node.node_id, EKRelationshipType.VALIDATED_BY,
                                 metadata={"run_id": run_id}):
                    updated_edges.append(self._last_edge_id(patch_node.node_id, test_node.node_id))

        # Repair
        repair_result = getattr(run, "repair_result", None)
        if repair_result is not None:
            repair_node = self.add_node(
                EKNodeType.PATCH, f"repair:{run_id}", source_ref=run_id, source_type="run",
                qualified_name=f"repair:{run_id}",
                payload={
                    "status": getattr(repair_result, "status", None)
                    and getattr(repair_result.status, "value", str(repair_result.status)) or "",
                    "stop_reason": (getattr(repair_result, "stop_reason", "") or "")[:200],
                },
                provenance={"run_id": run_id, "source": "repair"},
            )
            updated_nodes.append(repair_node.node_id)

        # Review + quality gate
        review_report = getattr(run, "review_report", None)
        if review_report is not None:
            review_node = self.add_node(
                EKNodeType.REVIEW_FINDING, f"review:{run_id}", source_ref=run_id, source_type="run",
                qualified_name=f"review:{run_id}",
                payload={
                    "verdict": getattr(review_report, "verdict", "")[:50],
                    "finding_count": len(getattr(review_report, "findings", None) or []),
                },
                provenance={"run_id": run_id, "source": "review"},
            )
            updated_nodes.append(review_node.node_id)

        gate = getattr(run, "quality_gate_result", None)
        if gate is not None:
            decision = getattr(gate, "decision", None)
            decision_v = getattr(decision, "value", str(decision))
            gate_node = self.add_node(
                EKNodeType.QUALITY_GATE, f"gate:{run_id}", source_ref=run_id, source_type="run",
                qualified_name=f"gate:{run_id}",
                payload={"decision": decision_v, "blocking_findings": len(getattr(gate, "blocking_findings", None) or [])},
                provenance={"run_id": run_id, "source": "quality_gate"},
            )
            updated_nodes.append(gate_node.node_id)
            if patch_node := self._find_run_node(run_id, EKNodeType.PATCH):
                if self.add_edge(patch_node.node_id, gate_node.node_id, EKRelationshipType.APPROVED_BY,
                                 metadata={"run_id": run_id}):
                    updated_edges.append(self._last_edge_id(patch_node.node_id, gate_node.node_id))

        # Reasoning artifacts (consensus, contradictions, notebook) → graph
        outcome = reasoning_outcome or {}
        consensus_list = outcome.get("consensus") or []
        contradictions = outcome.get("contradictions") or []
        notebook = outcome.get("notebook")
        for c in consensus_list[:10]:
            topic = getattr(c, "topic", "")[:100]
            cid = getattr(c, "consensus_id", "")
            c_node = self.add_node(
                EKNodeType.CONSENSUS, f"consensus:{topic}", node_id=_stable_id(EKNodeType.CONSENSUS, cid),
                source_ref=cid, source_type="consensus", qualified_name=f"consensus:{topic}",
                payload={
                    "status": getattr(c.status, "value", ""),
                    "confidence": round(getattr(getattr(c, "confidence", None), "value", 0) or 0, 2),
                    "final_decision": getattr(c, "final_decision", "")[:200],
                },
                provenance={"run_id": run_id, "source": "reasoning"},
            )
            updated_nodes.append(c_node.node_id)
            if self.add_edge(run_node.node_id, c_node.node_id, EKRelationshipType.PRODUCED_BY,
                             metadata={"run_id": run_id}):
                updated_edges.append(self._last_edge_id(run_node.node_id, c_node.node_id))
        for cd in contradictions[:10]:
            cid = getattr(cd, "contradiction_id", "")
            cd_node = self.add_node(
                EKNodeType.CONTRADICTION, f"contradiction:{getattr(cd, 'kind', '')}", node_id=_stable_id(EKNodeType.CONTRADICTION, cid),
                source_ref=cid, source_type="contradiction",
                qualified_name=getattr(cd, "description", "")[:200],
                payload={
                    "kind": getattr(cd.kind, "value", "") if getattr(cd, "kind", None) else "",
                    "resolution": getattr(cd, "resolution", "")[:50],
                },
                provenance={"run_id": run_id, "source": "reasoning"},
            )
            updated_nodes.append(cd_node.node_id)
        if notebook is not None:
            nid = getattr(notebook, "notebook_id", "") or f"NB-{run_id}"
            nb_node = self.add_node(
                EKNodeType.NOTEBOOK_ENTRY, f"notebook:{run_id}", node_id=_stable_id(EKNodeType.NOTEBOOK_ENTRY, nid),
                source_ref=nid, source_type="notebook", qualified_name=f"notebook:{run_id}",
                payload={
                    "task": getattr(notebook, "task", "")[:200],
                    "accepted_decisions": len(getattr(notebook, "accepted_decisions", None) or []),
                    "rejected_decisions": len(getattr(notebook, "rejected_decisions", None) or []),
                    "conflicts": len(getattr(notebook, "conflicts", None) or []),
                    "consensus": len(getattr(notebook, "consensus", None) or []),
                    "timeline": len(getattr(notebook, "timeline", None) or []),
                },
                provenance={"run_id": run_id, "source": "reasoning"},
            )
            updated_nodes.append(nb_node.node_id)
            if self.add_edge(run_node.node_id, nb_node.node_id, EKRelationshipType.PRODUCED_BY,
                             metadata={"run_id": run_id}):
                updated_edges.append(self._last_edge_id(run_node.node_id, nb_node.node_id))

        # Version bump
        version = self.increment_version(
            run_id=run_id,
            summary=f"Run {run_id} ingested ({title[:120]})",
            updated_nodes=updated_nodes,
            updated_edges=updated_edges,
        )
        # Persist the new nodes/edges/version (best-effort)
        await self._persist_ingested_nodes(updated_nodes)
        await self._persist_ingested_edges(updated_edges)
        await self._persist_version(version)
        # Phase 19: embed new nodes + mirror to pgvector when available
        await self._ensure_semantic_index()
        await self._persist_semantic_pg(updated_nodes)
        return version

    # ── Impact-edge test selection (Phase 12d closure) ───────────

    def select_tests_for_changes(
        self,
        changed_files: List[str],
        *,
        limit: int = 10,
    ) -> List[str]:
        """Select test files from EKG impact edges (patch → test evidence).

        Walks: changed file → FILE node (source_ref match) → reverse
        MODIFIES edge → PATCH node → VALIDATED_BY edge → TEST_SUITE node,
        collecting the test file paths recorded on each test suite payload
        (`test_files`). This replaces the lazy per-repo semantic-graph
        cache with cross-run graph evidence: a replan's test set is driven
        by the impact edges the EKG already persists for every ingested
        run.

        Returns [] when no evidence exists (graceful degradation, never
        raises) — the same pattern as the rest of the codebase.
        """
        if not changed_files:
            return []
        wanted = set(changed_files)
        selected: List[str] = []
        seen: Set[str] = set()
        try:
            # 1. FILE nodes matching the changed paths.
            file_nodes = [
                n for n in self._nodes.values()
                if n.node_type == EKNodeType.FILE
                and (n.source_ref in wanted or n.qualified_name in wanted)
            ]
            # 2. PATCH nodes that MODIFIES those files (reverse edge).
            patch_ids: Set[str] = set()
            for fnode in file_nodes:
                for edge in self.get_reverse_edges(fnode.node_id):
                    if edge.relationship != EKRelationshipType.MODIFIES:
                        continue
                    src = self._nodes.get(edge.source_id)
                    if src is not None and src.node_type == EKNodeType.PATCH:
                        patch_ids.add(edge.source_id)
            # 3. TEST_SUITE nodes VALIDATED_BY those patches → test files.
            for pid in patch_ids:
                for edge in self.get_edges(pid):
                    if edge.relationship != EKRelationshipType.VALIDATED_BY:
                        continue
                    suite = self._nodes.get(edge.target_id)
                    if suite is None or suite.node_type != EKNodeType.TEST_SUITE:
                        continue
                    for tf in (suite.payload or {}).get("test_files", []) or []:
                        if isinstance(tf, str) and tf and tf not in seen:
                            seen.add(tf)
                            selected.append(tf)
                            if len(selected) >= limit:
                                return selected
        except Exception as exc:
            logger.debug("EKG impact-edge test selection unavailable: %s", exc)
        return selected

    @staticmethod
    def _extract_test_files(run: Any) -> List[str]:
        """Extract test file paths from a completed run (best-effort).

        Sources, in order: the plan's impact-driven test strategy, then the
        test result's failing test file paths, then pytest command args from
        process results. Deduplicated and bounded (evidence-only, never
        chain-of-thought).
        """
        files: List[str] = []
        seen: Set[str] = set()

        def _add(fp: Any) -> None:
            if isinstance(fp, str) and fp.strip() and fp.strip() not in seen:
                seen.add(fp.strip())
                files.append(fp.strip())

        # 1. Plan test strategy: "impact-driven tests: a, b, c"
        plan = getattr(run, "plan", None)
        if plan is not None:
            strategy = (getattr(plan, "test_strategy", "") or "").strip()
            if strategy.startswith("impact-driven tests:"):
                for part in strategy.split("impact-driven tests:", 1)[1].split(","):
                    _add(part)

        # 2. Failing test file paths
        test_result = getattr(run, "test_result", None)
        if test_result is not None:
            for failure in getattr(test_result, "failures", None) or []:
                _add(getattr(failure, "file_path", None))
            # 3. pytest command args that look like test paths
            for pr in getattr(test_result, "process_results", None) or []:
                cmd = (getattr(pr, "command", "") or "").strip()
                for arg in cmd.split():
                    if arg.endswith(".py") and not arg.startswith("-"):
                        _add(arg)
        return files[:20]

    # ── Query entry (delegates to KnowledgeQueryPlanner) ─────────

    async def query(self, query_text: str, **kwargs: Any) -> GraphQueryResult:
        """Query the graph via the KnowledgeQueryPlanner."""
        try:
            from app.services.knowledge_query_planner import KnowledgeQueryPlanner

            planner = KnowledgeQueryPlanner(graph=self)
            return await planner.retrieve(query_text, **kwargs)
        except Exception as exc:
            logger.debug("EKG query planner unavailable: %s", exc)
            return GraphQueryResult(query=query_text[:500], strategy=RetrievalStrategy.KNOWLEDGE_GRAPH,
                                    version=self._version)

    # ── Phase 19: Semantic retrieval (merged by the planner) ──────

    def _get_embedder(self) -> Any:
        """Lazy-create the embedding provider (deterministic hashed n-gram
        provider by default — similarity-preserving, no API)."""
        if self._embedder is None:
            if self._embedding_provider is not None:
                self._embedder = self._embedding_provider
            else:
                from app.rag.embeddings import create_embedding_service
                from app.config import settings

                # The global EMBEDDING_PROVIDER='fake' is the Phase 5
                # hash-random test provider (NOT similarity-preserving); the
                # EKG semantic layer needs real overlap, so 'fake' maps to
                # the deterministic hashed provider. 'hashed'/'openai' pass
                # through as configured.
                provider = settings.EMBEDDING_PROVIDER
                if provider == "fake":
                    provider = "hashed"
                self._embedder = create_embedding_service(
                    provider=provider,
                    model=settings.EMBEDDING_MODEL,
                    dimension=settings.EMBEDDING_DIMENSION,
                )
        return self._embedder

    def _node_embedding_text(self, node: EKNode) -> str:
        """Bounded, evidence-only text for a node (never chain-of-thought)."""
        parts = [node.name, node.qualified_name, node.kind, node.source_type]
        for key in ("summary", "description", "objective", "topic", "title",
                    "final_decision", "status"):
            val = (node.payload or {}).get(key)
            if isinstance(val, str):
                parts.append(val)
        text = " ".join(str(p) for p in parts if p)[:600]
        return text or node.node_id

    async def _ensure_semantic_index(self) -> int:
        """Embed any nodes missing from the in-memory index (bounded)."""
        embedder = self._get_embedder()
        embeddable = [
            n for n in self._nodes.values()
            if n.node_id not in self._semantic
        ][: self._SEMANTIC_MAX_NODES]
        if not embeddable:
            return len(self._semantic)
        texts = [self._node_embedding_text(n) for n in embeddable]
        result = embedder.embed_documents(texts)
        for node, vec in zip(embeddable, result.embeddings):
            self._semantic[node.node_id] = vec
        return len(self._semantic)

    async def semantic_search(
        self,
        query_text: str,
        *,
        limit: int = MAX_QUERY_RESULTS,
        target_kinds: Optional[List[EKNodeType]] = None,
    ) -> List[Dict[str, Any]]:
        """Cosine-similarity search over node payloads (bounded, in-memory).

        Returns up to `limit` entries: {node_id, node_type, name, score}.
        Deterministic: the hashed n-gram provider reproduces the same
        vectors for the same text in any process.
        """
        limit = min(max(limit, 1), MAX_QUERY_RESULTS)
        embedder = self._get_embedder()
        await self._ensure_semantic_index()
        qvec = embedder.embed_query(query_text)
        scored: List[Dict[str, Any]] = []
        for nid, vec in self._semantic.items():
            node = self._nodes.get(nid)
            if node is None:
                continue
            if target_kinds and node.node_type not in target_kinds:
                continue
            sim = _cosine_sim(qvec, vec)
            if sim <= 0.0:
                continue
            scored.append({
                "node_id": nid,
                "node_type": node.node_type.value,
                "name": node.name[:200],
                "score": round(sim, 4),
            })
        scored.sort(key=lambda x: -x["score"])
        return scored[:limit]

    def semantic_stats(self) -> Dict[str, Any]:
        """Observability: index size + provider model."""
        embedder = self._get_embedder()
        return {
            "embedded": len(self._semantic),
            "nodes": len(self._nodes),
            "provider": getattr(embedder, "model", ""),
            "dimension": getattr(embedder, "dimension", 0),
        }

    async def _persist_semantic_pg(self, node_ids: List[str]) -> None:
        """Best-effort mirror of node embeddings to ekg_embeddings (012)."""
        if not node_ids or not self._semantic_pg_available():
            return
        embedder = self._get_embedder()
        try:
            import asyncpg

            url = self._semantic_pg_url()
            if not url:
                return
            conn = await asyncpg.connect(url)
            try:
                for nid in node_ids:
                    vec = self._semantic.get(nid)
                    if vec is None:
                        continue
                    await conn.execute(
                        """
                        INSERT INTO ekg_embeddings (node_id, embedding, model)
                        VALUES ($1, $2::vector, $3)
                        ON CONFLICT (node_id)
                        DO UPDATE SET embedding = EXCLUDED.embedding,
                                      model = EXCLUDED.model
                        """,
                        nid, _vec_literal(vec), getattr(embedder, "model", ""),
                    )
            finally:
                await conn.close()
        except Exception as exc:
            logger.debug("EKG semantic PG mirror failed (in-memory continues): %s", exc)

    def _semantic_pg_url(self) -> str:
        """Plain postgresql:// URL for the asyncpg semantic mirror."""
        from app.config import settings

        url = self._database_url or settings.TEST_DATABASE_URL or settings.DATABASE_URL or ""
        if not url:
            return ""
        return url.replace("postgresql+asyncpg://", "postgresql://")

    def _semantic_pg_available(self) -> bool:
        """Cached probe: is pgvector usable for the semantic mirror?"""
        if self._pg_ok is not None:
            return self._pg_ok
        url = self._semantic_pg_url()
        if not url:
            self._pg_ok = False
            return False
        try:
            import asyncio
            import asyncpg

            loop = asyncio.new_event_loop()
            try:
                conn = loop.run_until_complete(asyncpg.connect(url))
                try:
                    row = loop.run_until_complete(
                        conn.fetchrow(
                            "SELECT 1 FROM pg_extension WHERE extname='vector'"
                        )
                    )
                    self._pg_ok = row is not None
                finally:
                    loop.run_until_complete(conn.close())
            finally:
                loop.close()
            return self._pg_ok
        except Exception as exc:
            logger.debug("EKG pgvector probe failed (in-memory): %s", exc)
            self._pg_ok = False
            return False

    # ── Persistence ─────────────────────────────────────────────

    async def _persist_ingested_nodes(self, node_ids: List[str]) -> None:
        if not node_ids:
            return
        async def _impl(session: AsyncSession) -> None:
            for nid in node_ids:
                node = self._nodes.get(nid)
                if node is None:
                    continue
                stmt = select(EKNodeModel).where(EKNodeModel.node_id == nid)
                model = (await session.execute(stmt)).scalar_one_or_none()
                if model is None:
                 session.add(EKNodeModel(
                         node_id=node.node_id,
                         node_type=node.node_type.value,
                         name=node.name,
                         qualified_name=node.qualified_name,
                         kind=node.kind,
                         source_ref=node.source_ref,
                         source_type=node.source_type,
                         payload=node.payload or None,
                         provenance=node.provenance or None,
                         status=node.status.value,
                         graph_version=node.graph_version,
                         repository_id=node.repository_id,
                     ))
                else:
                    model.name = node.name
                    model.qualified_name = node.qualified_name
                    model.payload = node.payload or None
                    model.provenance = node.provenance or None
                    model.status = node.status.value
                    model.graph_version = node.graph_version
                    model.repository_id = node.repository_id
            await session.commit()
        await self._with_session(_impl)

    async def _persist_ingested_edges(self, edge_ids: List[str]) -> None:
        if not edge_ids:
            return
        async def _impl(session: AsyncSession) -> None:
            seen: Set[str] = set()
            for eid in edge_ids:
                if eid in seen:
                    continue
                seen.add(eid)
                edge = self._find_edge(eid)
                if edge is None:
                    continue
                stmt = select(EKEdgeModel).where(EKEdgeModel.edge_id == eid)
                model = (await session.execute(stmt)).scalar_one_or_none()
                if model is None:
                    session.add(EKEdgeModel(
                        edge_id=edge.edge_id,
                        source_id=edge.source_id,
                        target_id=edge.target_id,
                        relationship=edge.relationship.value,
                        weight=edge.weight,
                        metadata_json=edge.metadata or None,
                        provenance=edge.provenance or None,
                         graph_version=edge.graph_version,
                         repository_id=edge.repository_id,
                     ))
            await session.commit()
        await self._with_session(_impl)

    async def _persist_version(self, version: GraphVersion) -> None:
        async def _impl(session: AsyncSession) -> None:
            stmt = select(GraphVersionModel).where(GraphVersionModel.version == version.version)
            model = (await session.execute(stmt)).scalar_one_or_none()
            if model is None:
                session.add(GraphVersionModel(
                    version=version.version,
                    run_id=version.run_id,
                    summary=version.summary,
                    updated_nodes=version.updated_nodes or None,
                    updated_edges=version.updated_edges or None,
                    superseded_node_ids=version.superseded_node_ids or None,
                ))
                await session.commit()
        await self._with_session(_impl)

    # ── Recovery (§5 / Demo F) ──────────────────────────────────

    async def recover(self) -> None:
        """Rehydrate graph state from PostgreSQL after restart."""
        # Reset in-memory state
        self._nodes.clear()
        self._edges.clear()
        self._reverse.clear()
        self._versions.clear()
        self._node_history.clear()
        self._version = 1

        async def _load(session: AsyncSession) -> None:
            node_stmt = select(EKNodeModel).order_by(EKNodeModel.id)
            # Phase 19A — when this graph is bound to a repository namespace,
            # only rehydrate that namespace's nodes (strict isolation across
            # repositories persisted in a shared PostgreSQL schema). The
            # column may not exist on un-migrated deployments; the query is
            # wrapped so recovery degrades gracefully to the in-memory copy.
            if self._repository_id != DEFAULT_REPOSITORY_ID:
                node_stmt = node_stmt.where(
                    EKNodeModel.repository_id == self._repository_id
                )
            node_rows = (await session.execute(node_stmt)).scalars().all()
            for m in node_rows:
                node = EKNode(
                    node_id=m.node_id,
                    node_type=EKNodeType(m.node_type),
                    name=m.name,
                    qualified_name=m.qualified_name or "",
                    kind=m.kind or "",
                    source_ref=m.source_ref or "",
                    source_type=m.source_type or "",
                    payload=m.payload or {},
                    provenance=m.provenance or {},
                    status=EKNodeStatus(m.status),
                    graph_version=m.graph_version or 1,
                    repository_id=getattr(m, "repository_id", None) or self._repository_id,
                )
                if m.created_at is not None:
                    try:
                        node.created_at = m.created_at.isoformat()
                    except Exception:
                        pass
                self._nodes[node.node_id] = node

            edge_stmt = select(EKEdgeModel).order_by(EKEdgeModel.id)
            if self._repository_id != DEFAULT_REPOSITORY_ID:
                edge_stmt = edge_stmt.where(
                    EKEdgeModel.repository_id == self._repository_id
                )
            edge_rows = (await session.execute(edge_stmt)).scalars().all()
            for m in edge_rows:
                edge = EKEdge(
                    edge_id=m.edge_id,
                    source_id=m.source_id,
                    target_id=m.target_id,
                    relationship=EKRelationshipType(m.relationship),
                    weight=m.weight or 1.0,
                    metadata=m.metadata_json or {},
                    provenance=m.provenance or {},
                    graph_version=m.graph_version or 1,
                    repository_id=getattr(m, "repository_id", None)
                    or self._repository_id,
                )
                if m.created_at is not None:
                    try:
                        edge.created_at = m.created_at.isoformat()
                    except Exception:
                        pass
                self._edges.setdefault(edge.source_id, {}).setdefault(edge.target_id, []).append(edge)
                self._reverse.setdefault(edge.target_id, {}).setdefault(edge.source_id, []).append(edge)

            version_rows = (await session.execute(
                select(GraphVersionModel).order_by(GraphVersionModel.version)
            )).scalars().all()
            for m in version_rows:
                self._versions.append(GraphVersion(
                    version=m.version,
                    run_id=m.run_id or "",
                    summary=m.summary or "",
                    updated_nodes=m.updated_nodes or [],
                    updated_edges=m.updated_edges or [],
                    superseded_node_ids=m.superseded_node_ids or [],
                ))
            if self._versions:
                self._version = self._versions[-1].version

        await self._with_session(_load)
        # Phase 19: restore the semantic index (derived from node text
        # deterministically — exact even without a PG mirror).
        self._semantic.clear()
        await self._ensure_semantic_index()
        logger.debug("EKG recovered: %d nodes, %d edges, %d embedded, version %d",
                     len(self._nodes), self._edges_count(), len(self._semantic),
                     self._version)

    # ── Helpers ─────────────────────────────────────────────────

    def _edges_count(self) -> int:
        return sum(len(tgt) for src in self._edges.values() for tgt in src.values())

    def _last_edge_id(self, source_id: str, target_id: str) -> str:
        edges = self._edges.get(source_id, {}).get(target_id, [])
        return edges[-1].edge_id if edges else ""

    def _find_edge(self, edge_id: str) -> Optional[EKEdge]:
        for src in self._edges.values():
            for tgt in src.values():
                for edge in tgt:
                    if edge.edge_id == edge_id:
                        return edge
        return None

    def _find_run_node(self, run_id: str, node_type: EKNodeType) -> Optional[EKNode]:
        for node in self._nodes.values():
            if node.source_ref == run_id and node.node_type == node_type:
                return node
        return None


def _cosine_sim(a: List[float], b: List[float]) -> float:
    """Cosine similarity with a safe guard against zero vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _vec_literal(vec: List[float]) -> str:
    """Render a vector as a pgvector literal string."""
    return "[" + ",".join(str(round(float(v), 6)) for v in vec) + "]"


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _safe_title(run: Any) -> str:
    try:
        source = getattr(run, "source", None)
        if source is not None:
            return (getattr(source, "title", "") or "")[:200]
    except Exception:
        pass
    return ""
