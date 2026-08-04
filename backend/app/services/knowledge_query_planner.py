"""
Phase 18 — KnowledgeQueryPlanner.

Instead of blindly searching everything, the planner classifies a user
query and selects the MINIMUM required retrieval strategy (§8):

    User Query → Query Planner → Select Retrieval Strategy →
    Semantic Graph | Repository Memory | Notebook | Consensus | History |
    Knowledge Graph → Merge → Rank → ContextEngine

Intent classification is 100% deterministic (keyword/lexical) — no LLM
and no paid APIs. Retrieval is bounded by the EKG caps.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from app.core.logging import logger
from app.models.engineering_graph import (
    MAX_QUERY_RESULTS,
    DEFAULT_REPOSITORY_ID,
    EKNode,
    EKNodeType,
    GraphQueryResult,
    QueryScope,
    RetrievalPlan,
    RetrievalStrategy,
)

# ── Intent classifiers ──────────────────────────────────────────
# Deterministic lexical rules mapping queries to intents/strategies.

_INTENT_RULES: List[Dict[str, Any]] = [
    # engineering_history precedes historical_fixes: the phrase "engineering
    # history" / "historically" is more specific than the bare word "history"
    # (which historical_fixes also matches) and than generic how/why/explain.
    {
        "intent": "engineering_history",
        "strategy": RetrievalStrategy.HISTORY,
        "keywords": ["engineering history", "historically", "timeline",
                     "chronology", "history of", "what happened", "history"],
        "kinds": [EKNodeType.NOTEBOOK_ENTRY, EKNodeType.RUN],
    },
    {
        "intent": "historical_fixes",
        "strategy": RetrievalStrategy.HISTORY,
        "keywords": ["historical fix", "past fix", "previous fix", "earlier fix",
                     "what fixed", "which repair", "repair fixed"],
        "kinds": [EKNodeType.PATCH, EKNodeType.RUN],
    },
    # Phase 19A — organization-wide / cross-repository retrieval. Listed
    # BEFORE the generic "explain_implementation" rule because the specific
    # cross-repository vocabulary must win over the broad how/why/explain
    # keywords that would otherwise swallow it.
    {
        "intent": "cross_repository",
        "strategy": RetrievalStrategy.CROSS_REPOSITORY,
        "keywords": ["cross repository", "cross-repository", "across repositories",
                     "other repository", "another repository", "whole organization",
                     "organization wide", "organization-wide", "org wide",
                     "all repositories", "linked repository"],
        "kinds": [EKNodeType.REPOSITORY, EKNodeType.FILE, EKNodeType.FUNCTION],
    },
    {
        "intent": "explain_implementation",
        "strategy": RetrievalStrategy.KNOWLEDGE_GRAPH,
        "keywords": ["explain", "why", "how", "implement", "what does", "what is"],
        "kinds": [EKNodeType.PATCH, EKNodeType.IMPLEMENTATION_PLAN, EKNodeType.FILE],
    },
    {
        "intent": "find_related_requirements",
        "strategy": RetrievalStrategy.KNOWLEDGE_GRAPH,
        "keywords": ["requirement", "requirements", "acceptance", "criterion", "criteria"],
        "kinds": [EKNodeType.REQUIREMENT, EKNodeType.ACCEPTANCE_CRITERION],
    },
    {
        "intent": "affected_tests",
        "strategy": RetrievalStrategy.SEMANTIC_GRAPH,
        "keywords": ["affected test", "tests affected", "which test", "test impact",
                     "what tests", "test suite", "break tests"],
        "kinds": [EKNodeType.TEST, EKNodeType.TEST_SUITE],
    },
    {
        "intent": "architecture_decisions",
        "strategy": RetrievalStrategy.KNOWLEDGE_GRAPH,
        "keywords": ["architecture", "decision", "decided", "design", "architectural"],
        "kinds": [EKNodeType.DECISION, EKNodeType.GOAL],
    },
    {
        "intent": "previous_solutions",
        "strategy": RetrievalStrategy.REPOSITORY_MEMORY,
        "keywords": ["previous solution", "successful", "worked before", "memory",
                     "known solution", "similar problem", "already solved"],
        "kinds": [EKNodeType.REPOSITORY_MEMORY, EKNodeType.RUN],
    },
    {
        "intent": "notebook_entries",
        "strategy": RetrievalStrategy.NOTEBOOK,
        "keywords": ["notebook", "engineering notebook", "entry", "log"],
        "kinds": [EKNodeType.NOTEBOOK_ENTRY],
    },
    {
        "intent": "quality_evidence",
        "strategy": RetrievalStrategy.CONSENSUS,
        "keywords": ["quality evidence", "consensus", "agreement", "confidence",
                     "gate decision", "approved", "evidence"],
        "kinds": [EKNodeType.QUALITY_GATE, EKNodeType.CONSENSUS],
    },
]


def _tokenize(text: str) -> List[str]:
    """Lowercase, split on non-alphanumeric, drop stopword-ish tokens."""
    tokens = re.split(r"[^a-zA-Z0-9]+", text.lower())
    return [t for t in tokens if t and len(t) > 1]


_STOP = {
    "the", "and", "for", "this", "that", "with", "from", "were", "was",
    "has", "have", "had", "its", "are", "was", "did", "does", "is", "of",
}


class KnowledgeQueryPlanner:
    """Deterministic query planner over the engineering knowledge graph."""

    def __init__(self, graph: Any) -> None:
        """graph: EngineeringKnowledgeGraphService instance."""
        self._graph = graph

    # ── Plan ────────────────────────────────────────────────────

    def plan(self, query: str) -> RetrievalPlan:
        """Classify the query and select the minimal retrieval strategy."""
        q = query.strip()
        tokens = [t for t in _tokenize(q) if t not in _STOP]
        matched: Optional[Dict[str, Any]] = None
        for rule in _INTENT_RULES:
            if any(kw in q.lower() for kw in rule["keywords"]):
                matched = rule
                break

        if matched is None:
            matched = {
                "intent": "general",
                "strategy": RetrievalStrategy.KNOWLEDGE_GRAPH,
                "keywords": [],
                "kinds": [EKNodeType.FILE, EKNodeType.FUNCTION, EKNodeType.REQUIREMENT],
            }

        return RetrievalPlan(
            query=q[:500],
            intent=matched["intent"],
            strategy=matched["strategy"],
            key_terms=tokens[:20],
            target_kinds=matched["kinds"],
            rationale=(
                f"Intent '{matched['intent']}' → strategy "
                f"{matched['strategy'].value} (lexical match, no LLM)"
            ),
        )

    # ── Retrieve ────────────────────────────────────────────────

    async def retrieve(
        self,
        query_text: str,
        *,
        limit: int = MAX_QUERY_RESULTS,
        target_kinds: Optional[List[EKNodeType]] = None,
        scope: QueryScope = QueryScope.AUTO,
        repository_ids: Optional[List[str]] = None,
    ) -> GraphQueryResult:
        """Execute the plan and return bounded merged results.

        Phase 19A — scope & repository routing:
        - scope=LOCAL: strict namespace isolation; only `repository_ids`
          (or the single repository behind the graph) is searched.
        - scope=ORGANIZATION: organization-wide retrieval (the graph passed
          in is an _OrgGraphView over all linked repositories).
        - scope=AUTO: the planner decides from the query vocabulary; if a
          cross-repository intent is detected the result is tagged with the
          CROSS_REPOSITORY strategy.
        """
        plan = self.plan(query_text)
        q = query_text.strip().lower()

        # Phase 19A — apply explicit scope routing on top of the lexical plan.
        if scope == QueryScope.ORGANIZATION:
            plan.scope = QueryScope.ORGANIZATION
            plan.strategy = RetrievalStrategy.CROSS_REPOSITORY
            plan.intent = "cross_repository"
            plan.cross_repository = True
            plan.rationale = "Organization-wide scope requested → cross-repository traversal"
        elif scope == QueryScope.LOCAL:
            plan.scope = QueryScope.LOCAL
            plan.cross_repository = False
        else:
            # AUTO — if the vocabulary itself asked for cross-repository.
            plan.scope = QueryScope.AUTO
            plan.cross_repository = plan.strategy == RetrievalStrategy.CROSS_REPOSITORY

        if repository_ids:
            plan.repository_ids = repository_ids[:20]
            plan.cross_repository = (
                plan.cross_repository or len(repository_ids) > 1
            )

        limit = min(limit, MAX_QUERY_RESULTS)
        repo_filter = set(repository_ids) if repository_ids else None

        def _in_scope(node: EKNode) -> bool:
            if repo_filter is None:
                return True
            return (node.repository_id or DEFAULT_REPOSITORY_ID) in repo_filter

        nodes: List[EKNode] = []
        edges: List[Any] = []
        seen: set[str] = set()
        seen_repos: Dict[str, int] = {}

        def _admit(node: EKNode) -> bool:
            if node.node_id in seen or not _in_scope(node):
                return False
            seen.add(node.node_id)
            repo = node.repository_id or DEFAULT_REPOSITORY_ID
            seen_repos[repo] = seen_repos.get(repo, 0) + 1
            return True

        # 1. EKG lexical scan (name / qualified_name / payload match)
        key_terms = [t for t in plan.key_terms if len(t) >= 3]
        for node in self._graph.all_nodes(limit=2000):
            if target_kinds and node.node_type not in target_kinds:
                continue
            if plan.target_kinds and node.node_type not in plan.target_kinds:
                continue
            haystack = f"{node.name} {node.qualified_name} {node.source_ref}".lower()
            if not key_terms or any(t in haystack for t in key_terms):
                if _admit(node):
                    nodes.append(node)
            if len(nodes) >= limit:
                break

        # 2. Strategy-specific enrichment
        if plan.strategy in (RetrievalStrategy.HISTORY, RetrievalStrategy.NOTEBOOK):
            # Include run nodes + notebook nodes with the query in payload
            for node in self._graph.all_nodes(limit=2000):
                if node.node_type not in (EKNodeType.RUN, EKNodeType.NOTEBOOK_ENTRY):
                    continue
                hay = f"{node.name} {node.qualified_name} {node.source_ref}".lower()
                if any(t in hay for t in key_terms) and _admit(node):
                    nodes.append(node)
                if len(nodes) >= limit:
                    break
        elif plan.strategy == RetrievalStrategy.REPOSITORY_MEMORY:
            for node in self._graph.all_nodes(limit=2000):
                if node.node_type != EKNodeType.REPOSITORY_MEMORY:
                    continue
                hay = f"{node.name} {node.qualified_name}".lower()
                if any(t in hay for t in key_terms) and _admit(node):
                    nodes.append(node)
                if len(nodes) >= limit:
                    break
        elif plan.strategy == RetrievalStrategy.CONSENSUS:
            for node in self._graph.all_nodes(limit=2000):
                if node.node_type != EKNodeType.CONSENSUS:
                    continue
                hay = f"{node.name} {node.qualified_name}".lower()
                if any(t in hay for t in key_terms) and _admit(node):
                    nodes.append(node)
                if len(nodes) >= limit:
                    break
        elif plan.strategy == RetrievalStrategy.CROSS_REPOSITORY:
            # Phase 19A — cross-repository pass: include REPOSITORY nodes
            # so the caller can see which repositories contributed evidence.
            for node in self._graph.all_nodes(limit=2000):
                if node.node_type != EKNodeType.REPOSITORY:
                    continue
                if _admit(node):
                    nodes.append(node)
                if len(nodes) >= limit:
                    break

        # 3. Phase 19: merge SEMANTIC results (cosine over node payloads)
        #    into the lexical set — fills remaining slots within the same
        #    bound. Deterministic hashed n-gram provider, no API needed.
        semantic_used = False
        semantic_matches = 0
        semantic_top_score = 0.0
        try:
            # Semantic search is a RECALL booster: it must NOT be restricted
            # to the plan's inferred kinds (lexical intent kinds are a
            # precision filter — e.g. a "memory" query plans REPOSITORY_MEMORY
            # but the semantically relevant node may be a REQUIREMENT). Only
            # an EXPLICIT caller-provided kind filter is honored.
            sem_hits = await self._graph.semantic_search(
                q, limit=limit, target_kinds=target_kinds or None,
            )
            if sem_hits:
                semantic_top_score = sem_hits[0].get("score", 0.0)
                for hit in sem_hits:
                    nid = hit.get("node_id")
                    if not nid or nid in seen:
                        continue
                    node = self._graph.get_node(nid)
                    if node is None:
                        continue
                    if not _in_scope(node):
                        continue
                    seen.add(nid)
                    repo = node.repository_id or DEFAULT_REPOSITORY_ID
                    seen_repos[repo] = seen_repos.get(repo, 0) + 1
                    nodes.append(node)
                    semantic_matches += 1  # count nodes actually merged
                    if len(nodes) >= limit:
                        break
                semantic_used = semantic_matches > 0
        except Exception as exc:
            # Semantic retrieval is best-effort; lexical results still stand.
            logger.debug("EKG semantic merge skipped: %s", exc)

        # 4. Collect edges between selected nodes (bounded)
        selected_ids = {n.node_id for n in nodes}
        for node in nodes:
            for edge in self._graph.get_edges(node.node_id)[:50]:
                if edge.target_id in selected_ids and len(edges) < 100:
                    edges.append(edge)
            for edge in self._graph.get_reverse_edges(node.node_id)[:50]:
                if edge.source_id in selected_ids and len(edges) < 100:
                    edges.append(edge)

        return GraphQueryResult(
            query=plan.query,
            strategy=plan.strategy,
            nodes=nodes[:limit],
            edges=edges[:100],
            truncated=len(seen) > limit,
            total_nodes=len(seen),
            version=self._graph.current_version().version,
            plan=plan,
            semantic_used=semantic_used,
            semantic_matches=semantic_matches,
            semantic_top_score=semantic_top_score,
            scope=plan.scope,
            repository_ids=plan.repository_ids or None,
            repositories=dict(
                sorted(seen_repos.items(), key=lambda x: -x[1])[:20]
            ),
        )
