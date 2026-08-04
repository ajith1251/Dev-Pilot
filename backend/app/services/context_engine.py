"""
Phase 13 — ContextEngine service.

Central orchestrator that assembles AgentContext from all available
sources: Phase 12 semantic graph, Phase 5 RAG, Phase 11 run history,
Phase 13 repository memory, and current run evidence.

All context items retain provenance and are ranked, deduplicated,
budgeted, and compressed deterministically before delivery to agents.
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import Any, Dict, List, Optional

from app.code_intelligence.agent_graph_helper import (
    extract_symbols_from_changed_files,
    extract_symbols_from_plan,
    get_graph_context_markdown,
)
from app.core.logging import logger
from app.models.context import (
    AgentContext,
    BudgetCategory,
    ContextBudget,
    ContextCategory,
    ContextItem,
    ContextMetrics,
    ContextSourceType,
    Provenance,
)
from app.models.engineering_graph import DEFAULT_REPOSITORY_ID, QueryScope
from app.models.memory import MemoryQuery, MemoryStatus
from app.services.repository_memory_service import RepositoryMemoryService


# ── Default Budget ─────────────────────────────────────────────


def _default_budget() -> ContextBudget:
    """Default context budget configuration."""
    return ContextBudget(
        max_total_tokens=8000,
        reserved_instructions=2000,
        reserved_output=2000,
    )


# ── Token Estimation ───────────────────────────────────────────


def _estimate_tokens(text: str) -> int:
    """Rough token estimation (4 chars per token)."""
    return len(text) // 4


# ── Dedup Key ──────────────────────────────────────────────────


def _make_dedup_key(content: str) -> str:
    """Create a deterministic dedup key from content."""
    return hashlib.sha256(content.encode()).hexdigest()[:32]


# ── ContextEngine ──────────────────────────────────────────────


class ContextEngine:
    """Central engine for building agent-specific context.

    Combines evidence from:
    - Phase 2 repository metadata
    - Phase 5 lexical/vector retrieval
    - Phase 12 semantic graph
    - Phase 11 run history
    - Phase 13 repository memory
    - Current run evidence (plan, tests, repairs, reviews)
    """

    def __init__(
        self,
        budget: Optional[ContextBudget] = None,
        code_intelligence_service: Optional[Any] = None,
        postgres_run_store: Optional[Any] = None,
        memory_service: Optional[RepositoryMemoryService] = None,
        engineering_graph: Optional[Any] = None,
        organization_graph: Optional[Any] = None,
    ) -> None:
        self._budget = budget or _default_budget()
        self._cis = code_intelligence_service
        self._store = postgres_run_store
        self._memory_service = memory_service
        # Phase 18 — Engineering Knowledge Graph (query via KnowledgeQueryPlanner)
        self._engineering_graph = engineering_graph
        # Phase 19A — Organization Knowledge Graph (cross-repository retrieval)
        self._organization_graph = organization_graph

    def _get_engineering_graph(self) -> Any:
        """Lazily initialize the Phase 18 EngineeringKnowledgeGraph."""
        if self._engineering_graph is None:
            try:
                from app.services.engineering_graph_service import (
                    EngineeringKnowledgeGraphService,
                )

                self._engineering_graph = EngineeringKnowledgeGraphService()
            except Exception as exc:
                logger.debug("EKG unavailable for context: %s", exc)
                self._engineering_graph = None
        return self._engineering_graph

    def _get_organization_graph(self) -> Any:
        """Lazily initialize the Phase 19A OrganizationKnowledgeGraph."""
        if self._organization_graph is None:
            try:
                from app.services.organization_graph_service import (
                    OrganizationKnowledgeGraphService,
                )

                self._organization_graph = OrganizationKnowledgeGraphService()
            except Exception as exc:
                logger.debug("Org graph unavailable for context: %s", exc)
                self._organization_graph = None
        return self._organization_graph

    # ── Main Entry Point ───────────────────────────────────────

    async def build_context(
        self,
        task: str,
        agent_type: str,
        repository_path: Optional[str] = None,
        symbol_names: Optional[List[str]] = None,
        file_paths: Optional[List[str]] = None,
        plan_text: Optional[str] = None,
        requirements_text: Optional[str] = None,
        run_id: Optional[str] = None,
        test_failures: Optional[List[Dict[str, Any]]] = None,
        repair_history: Optional[List[Dict[str, Any]]] = None,
        review_findings: Optional[List[Dict[str, Any]]] = None,
        cross_agent_notes: Optional[List[str]] = None,
        handoffs: Optional[List[Any]] = None,
        include_organization_context: bool = False,
    ) -> AgentContext:
        """Build agent-specific context from all available sources.

        Args:
            task: The task description.
            agent_type: One of 'planner', 'coding', 'test', 'repair', 'reviewer'.
            repository_path: Path to the repository.
            symbol_names: Primary symbol names to focus on.
            file_paths: Primary file paths to include.
            plan_text: The implementation plan text (if available).
            requirements_text: Requirements text (if available).
            run_id: Current run ID (for historical retrieval).
            test_failures: Current test failure details.
            repair_history: Current repair attempt history.
            review_findings: Previous review findings.
            cross_agent_notes: Shared notes from prior agents in this run
                (Phase 15 cross-agent context sharing).
            handoffs: Structured Phase 15 agent handoffs (bounded selection).
            include_organization_context: Phase 20 A3 — force the organization
                scope for cross-repository retrieval (used by multi-repo runs).
                When False, org evidence is gated on the AUTO planner detecting
                cross-repository vocabulary (strict isolation preserved).

        Returns:
            AgentContext with ranked, deduplicated, budgeted context.
        """
        metrics = ContextMetrics()
        budget = self._budget.config_for_agent(agent_type)

        # Collect all context candidates
        candidates: List[ContextItem] = []

        # 1. Task context (always included)
        candidates.extend(self._build_task_context(task, requirements_text))

        # 2. Repository summary (from graph if available)
        candidates.extend(self._build_repository_summary(repository_path))

        # 3. Plan context (if available)
        if plan_text:
            candidates.extend(self._build_plan_context(plan_text))

        # 4. Graph context (Phase 12 semantic graph)
        graph_items = self._build_graph_context(
            symbol_names=symbol_names,
            file_paths=file_paths,
            repository_path=repository_path,
        )
        candidates.extend(graph_items)
        metrics.graph_items = len(graph_items)

        # 5. Historical run context (Phase 11)
        if run_id:
            history_items = await self._build_run_history_context(
                run_id=run_id,
                symbol_names=symbol_names,
            )
            candidates.extend(history_items)
            metrics.run_history_items = len(history_items)

        # 6. Test failure context
        if test_failures:
            failure_items = self._build_test_failure_context(test_failures)
            candidates.extend(failure_items)
            metrics.test_failure_items = len(failure_items)

        # 7. Repair history
        if repair_history:
            repair_items = self._build_repair_history_context(repair_history)
            candidates.extend(repair_items)
            metrics.repair_history_items = len(repair_items)

        # 8. Review findings
        if review_findings:
            review_items = self._build_review_finding_context(review_findings)
            candidates.extend(review_items)

        # 8b. Cross-agent notes (Phase 15)
        if cross_agent_notes:
            notes_items = self._build_cross_agent_notes(cross_agent_notes)
            candidates.extend(notes_items)
            metrics.cross_agent_items = len(notes_items)

        # 8c. Structured agent handoffs (Phase 15 collaboration)
        if handoffs:
            handoff_items = self._build_handoff_context(handoffs)
            candidates.extend(handoff_items)
            metrics.handoff_items = len(handoff_items)

        # 9. Repository memory (Phase 13)
        if repository_path and symbol_names:
            memory_items = await self._build_repository_memory_context(
                repository_path=repository_path,
                symbol_names=symbol_names,
            )
            candidates.extend(memory_items)
            metrics.memory_items = len(memory_items)

        # 10. Engineering Knowledge Graph (Phase 18) — unified retrieval
        ekg_items = await self._build_engineering_graph_context(task)
        candidates.extend(ekg_items)
        metrics.graph_items += len(ekg_items)

        # 10b. Organization Knowledge Graph (Phase 19A) — cross-repository
        #      evidence. By default only adds context when the query vocabulary
        #      (or an explicitly configured org graph) indicates cross-repository
        #      relevance — strict isolation is preserved otherwise. Phase 20 A3:
        #      multi-repo runs force the ORGANIZATION scope for the planner.
        org_scope = (
            QueryScope.ORGANIZATION
            if include_organization_context
            else QueryScope.AUTO
        )
        org_items = await self._build_organization_graph_context(task, scope=org_scope)
        candidates.extend(org_items)
        metrics.graph_items += len(org_items)

        metrics.candidates_considered = len(candidates)

        # ── Context Pipeline ───────────────────────────────────

        # Rank candidates
        ranked = self._rank_candidates(candidates, task, agent_type)

        # Deduplicate
        deduped, duplicate_count = self._deduplicate(ranked)
        metrics.duplicates_removed = duplicate_count

        # Apply token budget
        tokens_before = self._count_tokens(deduped)
        metrics.tokens_before = tokens_before

        selected = self._apply_budget(deduped, budget)
        metrics.items_selected = len(selected)
        metrics.tokens_after = self._count_tokens(selected)

        # Assemble into AgentContext
        return self._assemble_context(
            items=selected,
            task=task,
            agent_type=agent_type,
            repository_path=repository_path,
            budget=budget,
            metrics=metrics,
        )

    # ── Context Builders ───────────────────────────────────────

    def _build_task_context(
        self,
        task: str,
        requirements_text: Optional[str] = None,
    ) -> List[ContextItem]:
        """Build task description context."""
        items: List[ContextItem] = []
        content = task
        if requirements_text:
            content += f"\n\n{requirements_text}"

        items.append(ContextItem(
            content=content[:2000],
            category=ContextCategory.TASK,
            provenance=Provenance(
                source=ContextSourceType.REQUIREMENTS,
                score=1.0,
                detail="Task description and requirements",
            ),
            estimated_tokens=_estimate_tokens(content[:2000]),
        ))
        return items

    def _build_repository_summary(
        self,
        repository_path: Optional[str] = None,
    ) -> List[ContextItem]:
        """Build repository summary from graph stats."""
        items: List[ContextItem] = []
        if not repository_path or not self._cis:
            return items

        graph = self._cis.get_current_graph()
        if not graph:
            return items

        stats = graph.stats()
        lines = [
            f"Node count: {stats.get('node_count', 0)}",
            f"Edge count: {stats.get('edge_count', 0)}",
            f"File count: {stats.get('file_count', 0)}",
        ]

        kinds = stats.get("kinds", {})
        if kinds:
            lines.append("Symbol kinds: " + ", ".join(
                f"{k}={v}" for k, v in sorted(kinds.items(), key=lambda x: -x[1])[:10]
            ))

        rels = stats.get("relationships", {})
        if rels:
            lines.append("Relationship types: " + ", ".join(
                f"{k}={v}" for k, v in sorted(rels.items(), key=lambda x: -x[1])[:8]
            ))

        content = "\n".join(lines)
        items.append(ContextItem(
            content=content,
            category=ContextCategory.REPOSITORY_SUMMARY,
            provenance=Provenance(
                source=ContextSourceType.GRAPH,
                score=0.9,
                detail="Repository statistics from semantic graph",
            ),
            estimated_tokens=_estimate_tokens(content),
        ))
        return items

    def _build_plan_context(self, plan_text: str) -> List[ContextItem]:
        """Build implementation plan context."""
        items: List[ContextItem] = []
        content = plan_text[:2000]
        items.append(ContextItem(
            content=content,
            category=ContextCategory.IMPLEMENTATION_PLAN,
            provenance=Provenance(
                source=ContextSourceType.IMPLEMENTATION_PLAN,
                score=0.95,
                detail="Implementation plan from Planning phase",
            ),
            estimated_tokens=_estimate_tokens(content),
        ))
        return items

    def _build_graph_context(
        self,
        symbol_names: Optional[List[str]] = None,
        file_paths: Optional[List[str]] = None,
        repository_path: Optional[str] = None,
    ) -> List[ContextItem]:
        """Build context from the Phase 12 semantic graph.

        Phase 15: uses the injected CodeIntelligenceService (when available)
        so graph evidence is integration-testable and does not build a
        fresh on-disk index per call. Falls back to the module-level helper
        only when no CIS was injected (backwards compatible).
        """
        items: List[ContextItem] = []
        if not symbol_names and not file_paths:
            return items

        try:
            ctx = self._format_graph_context(
                symbol_names=symbol_names,
                file_paths=file_paths,
                repository_path=repository_path,
            )
            # Filter placeholder strings from GraphAwareRetriever
            # (e.g. "(No relevant symbols found in graph)").
            if ctx and ctx.startswith("(No"):
                ctx = ""
            if ctx:
                items.append(ContextItem(
                    content=ctx,
                    category=ContextCategory.GRAPH_EVIDENCE,
                    provenance=Provenance(
                        source=ContextSourceType.GRAPH,
                        score=0.85,
                        detail="Semantic graph relationships and symbols",
                    ),
                    estimated_tokens=_estimate_tokens(ctx),
                ))
        except Exception as exc:
            logger.debug("Graph context unavailable: %s", exc)

        return items

    def _format_graph_context(
        self,
        symbol_names: Optional[List[str]] = None,
        file_paths: Optional[List[str]] = None,
        repository_path: Optional[str] = None,
    ) -> str:
        """Format semantic graph context using the injected CIS or fallback."""
        # Preferred path: use the injected service's loaded graph so
        # integration tests can mock the CIS and verify graph evidence.
        if self._cis is not None:
            try:
                graph = self._cis.get_current_graph()
                if graph is not None:
                    from app.code_intelligence.graph_retriever import GraphAwareRetriever

                    retriever = GraphAwareRetriever(graph=graph)
                    return retriever.get_agent_context(
                        symbol_names=(symbol_names or [])[:15],
                        file_paths=(file_paths or [])[:10],
                        max_context=25,
                    )
            except Exception as exc:
                logger.debug("Graph context via CIS unavailable: %s", exc)

        # Fallback: module-level helper (original Phase 13 behavior).
        return get_graph_context_markdown(
            symbol_names=(symbol_names or [])[:15],
            file_paths=(file_paths or [])[:10],
            max_context=25,
            repo_path=repository_path,
        )

    async def _build_run_history_context(
        self,
        run_id: str,
        symbol_names: Optional[List[str]] = None,
    ) -> List[ContextItem]:
        """Build context from Phase 11 historical runs."""
        items: List[ContextItem] = []
        if not self._store or not run_id:
            return items

        try:
            run = await self._store.get(run_id)
            if not run:
                return items

            parts = [f"Previous run: {run.status.value}"]
            if run.source.title:
                parts.append(f"Task: {run.source.title[:200]}")
            if run.total_duration_ms:
                parts.append(f"Duration: {run.total_duration_ms:.0f}ms")

            # Include failure info if present
            if run.failure:
                parts.append(f"Failure: {run.failure.message[:200]}")

            # Include relevant stage results
            for sr in run.stage_results[-3:]:  # last 3 stages
                if sr.error:
                    parts.append(f"Stage '{sr.stage.value}': {sr.error[:200]}")

            content = "\n".join(parts)
            items.append(ContextItem(
                content=content[:1000],
                category=ContextCategory.RUN_HISTORY,
                provenance=Provenance(
                    source=ContextSourceType.RUN_MEMORY,
                    score=0.6,
                    run_id=run.run_id,
                    detail=f"Historical run ({run.status.value})",
                ),
                estimated_tokens=_estimate_tokens(content[:1000]),
            ))
        except Exception:
            pass

        return items

    def _build_test_failure_context(
        self,
        test_failures: List[Dict[str, Any]],
    ) -> List[ContextItem]:
        """Build context from test failures."""
        items: List[ContextItem] = []
        parts = [f"Test failures ({len(test_failures)}):"]
        for i, f in enumerate(test_failures[:10]):
            name = f.get("test_name", f.get("name", f"failure_{i}"))
            message = f.get("message", "")[:300]
            file_path = f.get("file_path", "")
            parts.append(f"\n  [{i+1}] {name}")
            if file_path:
                parts.append(f"      File: {file_path}")
            if message:
                parts.append(f"      Error: {message}")

        content = "\n".join(parts)
        items.append(ContextItem(
            content=content,
            category=ContextCategory.PREVIOUS_FAILURES,
            provenance=Provenance(
                source=ContextSourceType.TEST_FAILURE,
                score=0.9,
                detail="Test failures from current run",
            ),
            estimated_tokens=_estimate_tokens(content),
        ))
        return items

    def _build_repair_history_context(
        self,
        repair_history: List[Dict[str, Any]],
    ) -> List[ContextItem]:
        """Build context from repair attempt history."""
        items: List[ContextItem] = []
        parts = [f"Repair attempts ({len(repair_history)}):"]
        for i, r in enumerate(repair_history[:5]):
            status = r.get("status", r.get("attempt_status", "unknown"))
            reason = r.get("reason", "")[:200]
            parts.append(f"\n  [{i+1}] Status: {status}")
            if reason:
                parts.append(f"      Reason: {reason}")

        content = "\n".join(parts)
        items.append(ContextItem(
            content=content,
            category=ContextCategory.PREVIOUS_REPAIRS,
            provenance=Provenance(
                source=ContextSourceType.REPAIR_HISTORY,
                score=0.8,
                detail="Repair attempt history",
            ),
            estimated_tokens=_estimate_tokens(content),
        ))
        return items

    def _build_review_finding_context(
        self,
        review_findings: List[Dict[str, Any]],
    ) -> List[ContextItem]:
        """Build context from review findings."""
        items: List[ContextItem] = []
        parts = [f"Review findings ({len(review_findings)}):"]
        for i, f in enumerate(review_findings[:5]):
            title = f.get("title", f.get("finding", f"finding_{i}"))
            severity = f.get("severity", "unknown")
            parts.append(f"\n  [{i+1}] [{severity}] {title}")

        content = "\n".join(parts)
        items.append(ContextItem(
            content=content,
            category=ContextCategory.REVIEW_FINDINGS,
            provenance=Provenance(
                source=ContextSourceType.REVIEW_FINDING,
                score=0.7,
                detail="Previous review findings",
            ),
            estimated_tokens=_estimate_tokens(content),
        ))
        return items

    def _build_cross_agent_notes(
        self,
        notes: List[str],
    ) -> List[ContextItem]:
        """Build context from shared notes left by prior agents in the same run.

        Phase 15 cross-agent context sharing: each stage boundary can append
        a concise note about what it decided/observed. Later agents receive
        these notes so they can build on prior agent output instead of
        starting from a blank slate.
        """
        items: List[ContextItem] = []
        if not notes:
            return items

        parts = [f"Prior agent notes ({len(notes)}):"]
        for i, n in enumerate(notes[-8:], start=1):
            parts.append(f"\n  [{i}] {n[:300]}")

        content = "\n".join(parts)
        items.append(ContextItem(
            content=content,
            category=ContextCategory.AGENT_NOTES,
            provenance=Provenance(
                source=ContextSourceType.CROSS_AGENT,
                score=0.75,
                detail="Shared notes from prior agents in this run",
            ),
            estimated_tokens=_estimate_tokens(content),
        ))
        return items

    def _build_handoff_context(
        self,
        handoffs: List[Any],
    ) -> List[ContextItem]:
        """Build context from structured Phase 15 agent handoffs.

        Only engineering evidence (summary, decisions, affected symbols,
        evidence refs) — never chain-of-thought. Bounded by the caller
        (CollaborationService.retrieve_relevant_handoffs).
        """
        items: List[ContextItem] = []
        if not handoffs:
            return items

        for handoff in handoffs[:8]:
            parts = [
                f"Handoff {handoff.from_agent} → {handoff.to_agent}",
                f"  Summary: {handoff.summary[:300]}",
            ]
            if handoff.decisions:
                parts.append("  Decisions:")
                for d in handoff.decisions[:5]:
                    parts.append(f"    - {d[:200]}")
            if handoff.affected_symbols:
                parts.append(
                    "  Affected symbols: " + ", ".join(handoff.affected_symbols[:10])
                )
            if handoff.evidence_refs:
                refs = [
                    f"{e.type.value}:{e.reference[:80]}"
                    for e in handoff.evidence_refs[:5]
                ]
                parts.append("  Evidence: " + "; ".join(refs))
            if handoff.warnings:
                parts.append("  Warnings: " + "; ".join(handoff.warnings[:3]))
            if handoff.validation and handoff.status.value != "unverified":
                parts.append(f"  Validation: {handoff.status.value}")

            content = "\n".join(parts)
            items.append(ContextItem(
                content=content,
                category=ContextCategory.AGENT_HANDOFF,
                provenance=Provenance(
                    source=ContextSourceType.HANDOFF,
                    score=0.72,
                    detail=f"Handoff {handoff.from_agent} → {handoff.to_agent}",
                ),
                estimated_tokens=_estimate_tokens(content),
            ))

        return items

    async def _build_repository_memory_context(
        self,
        repository_path: str,
        symbol_names: List[str],
    ) -> List[ContextItem]:
        """Build context from Phase 13 repository memory.

        Queries the RepositoryMemoryService for memories relevant
        to the given symbol names. Returns ranked memory items.
        Gracefully degrades if memory service is unavailable.

        Uses os.path.basename() to derive a repository_id from the
        full filesystem path, matching how memories are typically stored.
        """
        items: List[ContextItem] = []
        if not self._memory_service or not symbol_names:
            return items

        # Derive short repo identifier from the full path (matching Path.name in indexing)
        repo_id = os.path.basename(repository_path.rstrip("/\\")) or repository_path

        try:
            memories = await self._memory_service.get_memories_for_symbols(
                repository_id=repo_id,
                symbol_names=symbol_names,
                limit=5,
            )
            for mem in memories:
                parts = [
                    f"[{mem.memory_type.value}] {mem.content}",
                    f"    Confidence: {mem.confidence:.2f}",
                    f"    Status: {mem.status.value}",
                ]
                if mem.evidence:
                    parts.append(f"    Evidence: {mem.evidence[0].description[:100]}")

                content = "\n".join(parts)
                items.append(ContextItem(
                    content=content,
                    category=ContextCategory.REPOSITORY_MEMORY,
                    provenance=Provenance(
                        source=ContextSourceType.REPOSITORY_MEMORY,
                        score=min(mem.confidence, 0.9),
                        memory_id=mem.memory_id,
                        detail=f"Repository memory ({mem.memory_type.value})",
                    ),
                    estimated_tokens=_estimate_tokens(content),
                ))
        except Exception as exc:
            logger.debug("Repository memory context unavailable: %s", exc)

        return items

    async def _build_engineering_graph_context(
        self,
        task: str,
    ) -> List[ContextItem]:
        """Build context from the Phase 18 Engineering Knowledge Graph.

        Uses the KnowledgeQueryPlanner to select the minimal retrieval
        strategy for the task, then formats bounded evidence-only results.
        Gracefully degrades when the graph is unavailable.
        """
        items: List[ContextItem] = []
        graph = self._get_engineering_graph()
        if graph is None:
            return items
        try:
            result = await graph.query(task, limit=10)
            if not result.nodes:
                return items

            parts = [f"Engineering knowledge graph ({result.strategy.value}, "
                     f"v{result.version}):"]
            for node in result.nodes[:8]:
                parts.append(
                    f"  - [{node.node_type.value}] {node.name[:120]}"
                    f"  ({node.source_type}:{node.source_ref[:60]})"
                )
            if result.edges:
                rels = sorted({e.relationship.value for e in result.edges[:10]})
                parts.append(f"  Relationships: {', '.join(rels)}")
            if result.truncated:
                parts.append("  (truncated)")

            content = "\n".join(parts)
            items.append(ContextItem(
                content=content,
                category=ContextCategory.GRAPH_EVIDENCE,
                provenance=Provenance(
                    source=ContextSourceType.GRAPH,
                    score=0.82,
                    detail=f"Engineering knowledge graph ({result.strategy.value})",
                ),
                estimated_tokens=_estimate_tokens(content),
            ))
        except Exception as exc:
            logger.debug("EKG context unavailable: %s", exc)
        return items

    async def _build_organization_graph_context(
        self,
        task: str,
        scope: QueryScope = QueryScope.AUTO,
    ) -> List[ContextItem]:
        """Build context from the Phase 19A Organization Knowledge Graph.

        With the default AUTO scope, only runs a cross-repository query when the
        org graph has registered repositories AND the planner's AUTO scope
        detects cross-repository vocabulary (deterministic) — an explicit
        ORGANIZATION scope (Phase 20 A3 multi-repo runs) bypasses that gate.
        Evidence is attributed per-repository so an agent can see which
        namespace contributed each node — repository isolation is preserved
        because the org graph only exposes explicitly linked repositories
        through the organization scope.
        """
        items: List[ContextItem] = []
        org = self._get_organization_graph()
        if org is None or not org.repositories():
            return items
        try:
            result = await org.query(task, limit=10, scope=scope)
            plan = result.plan
            # AUTO keeps strict isolation: only surface cross-repository
            # evidence; otherwise the local EKG context already covers
            # repository-local retrieval. An explicit ORGANIZATION scope is
            # unconditional.
            if scope == QueryScope.AUTO and (
                plan is None or not plan.cross_repository
            ):
                return items
            if not result.nodes:
                return items

            parts = [
                f"Organization knowledge graph (cross-repository, "
                f"v{result.version}):"
            ]
            for node in result.nodes[:8]:
                repo = node.repository_id or DEFAULT_REPOSITORY_ID
                parts.append(
                    f"  - [{node.node_type.value}] {node.name[:120]} "
                    f"(repo:{repo}, {node.source_type}:{node.source_ref[:60]})"
                )
            if result.repositories:
                contrib = ", ".join(
                    f"{r}:{c}" for r, c in list(result.repositories.items())[:8]
                )
                parts.append(f"  Repositories contributing: {contrib}")
            if result.edges:
                rels = sorted({e.relationship.value for e in result.edges[:10]})
                parts.append(f"  Relationships: {', '.join(rels)}")
            if result.truncated:
                parts.append("  (truncated)")

            content = "\n".join(parts)
            items.append(ContextItem(
                content=content,
                category=ContextCategory.GRAPH_EVIDENCE,
                provenance=Provenance(
                    source=ContextSourceType.GRAPH,
                    score=0.80,
                    detail=(
                        "Organization knowledge graph "
                        f"({result.strategy.value})"
                    ),
                ),
                estimated_tokens=_estimate_tokens(content),
            ))
        except Exception as exc:
            logger.debug("Org-graph context unavailable: %s", exc)
        return items

    # ── Ranking ────────────────────────────────────────────────

    def _rank_candidates(
        self,
        candidates: List[ContextItem],
        task: str,
        agent_type: str,
    ) -> List[ContextItem]:
        """Rank context items by relevance to the task and agent type.

        Uses deterministic factors:
        - Category priority (task > primary code > dependencies > ...)
        - Provenance source weight (graph > vector > memory > ...)
        - Core/leaf symbol distinction
        """
        # Start with base scores from provenance
        for item in candidates:
            base = item.provenance.score

            # Boost task descriptions
            if item.category == ContextCategory.TASK:
                base = max(base, 1.0)
            elif item.category == ContextCategory.IMPLEMENTATION_PLAN:
                base = max(base, 0.95)
            elif item.category == ContextCategory.PRIMARY_CODE:
                base = max(base, 0.9)
            elif item.category == ContextCategory.DEPENDENCIES:
                base = max(base, 0.8)
            elif item.category == ContextCategory.CALLERS:
                base = max(base, 0.7)
            elif item.category == ContextCategory.CALLEES:
                base = max(base, 0.65)
            elif item.category == ContextCategory.RELATED_TESTS:
                base = max(base, 0.6)
            elif item.category == ContextCategory.WARNINGS:
                base = max(base, 0.5)

            item.provenance.score = min(base, 1.0)

        # Sort by score descending
        candidates.sort(key=lambda x: x.provenance.score, reverse=True)
        return candidates

    # ── Deduplication ──────────────────────────────────────────

    def _deduplicate(
        self, candidates: List[ContextItem]
    ) -> tuple[List[ContextItem], int]:
        """Deduplicate context items by content hash.

        When duplicates are found, the item with the highest score
        is kept and all provenances are preserved by merging the
        losing item's provenance into the survivor's
        `merged_provenances` list (Phase 15 fix).
        """
        seen: Dict[str, ContextItem] = {}
        duplicate_count = 0

        for item in candidates:
            # Generate dedup key from content
            key = item.dedup_key or _make_dedup_key(item.content)

            if key in seen:
                duplicate_count += 1
                existing = seen[key]
                if item.provenance.score > existing.provenance.score:
                    # Promote the higher-scored item, preserving the
                    # lower-scored item's provenance on the survivor.
                    # NOTE: the survivor's own provenance stays in
                    # `provenance`; only the loser's provenance (and any
                    # already-merged ones) goes into merged_provenances.
                    item.merged_provenances = [
                        *existing.merged_provenances,
                        existing.provenance,
                        *item.merged_provenances,
                    ]
                    item.dedup_key = key
                    seen[key] = item
                else:
                    # Keep existing; merge the incoming item's provenance.
                    existing.merged_provenances = [
                        *existing.merged_provenances,
                        item.provenance,
                        *item.merged_provenances,
                    ]
            else:
                item.dedup_key = key
                seen[key] = item

        return list(seen.values()), duplicate_count

    # ── Token Budgeting ────────────────────────────────────────

    def _apply_budget(
        self,
        items: List[ContextItem],
        budget: ContextBudget,
    ) -> List[ContextItem]:
        """Apply token budget, allocating to categories in priority order.

        Higher-priority categories get their full budget first.
        When budget is exhausted, remaining items are dropped.
        """
        if not items:
            return []

        available = budget.available_context_tokens
        used = 0
        selected: List[ContextItem] = []

        # Build category budget map
        cat_budgets: Dict[str, int] = {}
        for bc in budget.categories:
            cat_budgets[bc.category.value] = min(
                int(available * bc.percentage / 100),
                bc.max_tokens,
            )

        # Process items in score order
        for item in sorted(items, key=lambda x: x.provenance.score, reverse=True):
            if used >= available:
                break

            tokens = item.estimated_tokens or _estimate_tokens(item.content)
            cat = item.category.value
            cat_limit = cat_budgets.get(cat, int(available * 0.1))

            # Check if this category has budget remaining
            cat_used = sum(
                it.estimated_tokens or _estimate_tokens(it.content)
                for it in selected
                if it.category == item.category
            )
            if cat_used + tokens > cat_limit:
                continue

            selected.append(item)
            used += tokens

        return selected

    @staticmethod
    def _count_tokens(items: List[ContextItem]) -> int:
        """Count total estimated tokens for a list of items."""
        return sum(
            it.estimated_tokens or _estimate_tokens(it.content)
            for it in items
        )

    # ── Context Assembly ───────────────────────────────────────

    def _assemble_context(
        self,
        items: List[ContextItem],
        task: str,
        agent_type: str,
        repository_path: Optional[str] = None,
        budget: Optional[ContextBudget] = None,
        metrics: Optional[ContextMetrics] = None,
    ) -> AgentContext:
        """Assemble ranked/deduplicated items into an AgentContext."""
        ctx = AgentContext(
            task=task,
            agent_type=agent_type,
            repository_path=repository_path,
            budget=budget or _default_budget(),
            metrics=metrics or ContextMetrics(),
        )
        ctx.raw_items = items

        # Assemble each category
        for item in items:
            content = item.content
            if not content:
                continue

            if item.category == ContextCategory.TASK:
                pass  # Task is already set
            elif item.category == ContextCategory.REPOSITORY_SUMMARY:
                ctx.repository_summary += content + "\n"
            elif item.category == ContextCategory.IMPLEMENTATION_PLAN:
                ctx.implementation_plan += content + "\n"
            elif item.category == ContextCategory.PRIMARY_CODE:
                ctx.primary_symbols += content + "\n"
            elif item.category == ContextCategory.DEPENDENCIES:
                ctx.dependencies += content + "\n"
            elif item.category == ContextCategory.CALLERS:
                ctx.callers += content + "\n"
            elif item.category == ContextCategory.CALLEES:
                ctx.callees += content + "\n"
            elif item.category == ContextCategory.CODE_CHUNKS:
                ctx.code_chunks += content + "\n"
            elif item.category == ContextCategory.RELATED_TESTS:
                ctx.related_tests += content + "\n"
            elif item.category == ContextCategory.PREVIOUS_FAILURES:
                ctx.previous_failures += content + "\n"
            elif item.category == ContextCategory.PREVIOUS_REPAIRS:
                ctx.previous_repairs += content + "\n"
            elif item.category == ContextCategory.REVIEW_FINDINGS:
                ctx.review_findings += content + "\n"
            elif item.category == ContextCategory.REPOSITORY_MEMORY:
                ctx.repository_memory += content + "\n"
            elif item.category == ContextCategory.RUN_HISTORY:
                ctx.historical_memory += content + "\n"
            elif item.category == ContextCategory.CONSTRAINTS:
                ctx.constraints += content + "\n"
            elif item.category == ContextCategory.WARNINGS:
                ctx.warnings += content + "\n"
            elif item.category == ContextCategory.GRAPH_EVIDENCE:
                ctx.related_symbols += content + "\n"
            elif item.category == ContextCategory.AGENT_NOTES:
                ctx.agent_notes += content + "\n"
            elif item.category == ContextCategory.AGENT_HANDOFF:
                ctx.agent_handoffs += content + "\n"

        return ctx

    # ── Diagnostics ────────────────────────────────────────────

    def explain_context(self, ctx: AgentContext) -> str:
        """Produce a human-readable explanation of why context was selected.

        Returns a formatted string showing rankings, sources, and budget usage.
        """
        lines = [
            f"Context for: {ctx.agent_type}",
            f"Task: {ctx.task[:100]}...",
            "",
            "=== Context Selection ===",
            f"Candidates considered: {ctx.metrics.candidates_considered}",
            f"Items selected: {ctx.metrics.items_selected}",
            f"Duplicates removed: {ctx.metrics.duplicates_removed}",
            f"Estimated tokens: {ctx.metrics.tokens_before} → {ctx.metrics.tokens_after}",
            "",
            "=== Sources ===",
        ]

        # Count by source
        source_counts: Dict[str, int] = {}
        for item in ctx.raw_items:
            src = item.provenance.source.value
            source_counts[src] = source_counts.get(src, 0) + 1
        for src, count in sorted(source_counts.items(), key=lambda x: -x[1]):
            lines.append(f"  {src}: {count} items")

        # Show top items with provenance
        lines.extend(["", "=== Top Context Items ==="])
        for i, item in enumerate(ctx.raw_items[:10]):
            lines.append(
                f"  [{i+1}] {item.category.value} "
                f"(score={item.provenance.score:.2f}, "
                f"source={item.provenance.source.value})"
            )
            if item.provenance.detail:
                lines.append(f"       {item.provenance.detail[:100]}")

        return "\n".join(lines)
