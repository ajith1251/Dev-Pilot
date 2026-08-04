"""
Phase 17 — CollaborativeReasoningEngine.

Collects agent evidence, compares it, aggregates bounded confidence,
detects contradictions, and produces shared engineering consensus plus a
structured Engineering Notebook per run.

Layering (per Phase 17 spec):

    CollaborationService          = records WHAT agents produced/shared
    CollaborativeReasoningEngine  = decides whether the evidence AGREES

Responsibilities:
- collect_evidence(run)       — gather deterministic evidence per agent
- compute_confidence(refs)    — evidence-driven bounded confidence (no LLM)
- detect_contradictions(run)  — claim vs test, scope vs impact, etc.
- build_consensus(run)        — per-topic consensus records
- build_notebook(run)         — shared engineering notebook
- analyze_run(run)            — one-call pipeline (detect → consensus → notebook)

Security invariant: exposes ONLY evidence, confidence, decisions and
consensus — never chain-of-thought. Deterministic evidence always outranks
agent claims; an unsupported claim can never flip a consensus.

Persistence: mirrors to PostgreSQL (evidence_consensus /
contradiction_records / engineering_notebooks, migration 010) when
available, and keeps an in-memory copy so the system degrades gracefully
when the DB is down. Recovery: `recover(run_id)` rehydrates persisted state
after restart.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import logger
from app.db.models import (
    ContradictionRecordModel,
    EngineeringNotebookModel,
    EvidenceConsensusModel,
)
from app.db.session import create_session_factory
from app.models.collaboration import (
    EvidenceRef,
    EvidenceType,
    HandoffStatus,
)
from app.models.reasoning import (
    MAX_CONSENSUS_PER_RUN,
    MAX_CONTRADICTIONS_PER_RUN,
    MAX_EVIDENCE_PER_CONSENSUS,
    MAX_NOTEBOOK_ENTRIES,
    ConfidenceScore,
    ConfidenceTier,
    ConsensusStatus,
    ContradictionKind,
    ContradictionRecord,
    EngineeringNotebook,
    EvidenceConsensus,
    NotebookEntry,
    NotebookEntryType,
)


# ── Evidence authority (deterministic outranks claims) ────────────
_EVIDENCE_AUTHORITY: Dict[EvidenceType, float] = {
    EvidenceType.PATCH: 1.0,
    EvidenceType.TEST_RESULT: 0.95,
    EvidenceType.QUALITY_GATE: 0.9,
    EvidenceType.FAILURE: 0.88,
    EvidenceType.SOURCE_CODE: 0.85,
    EvidenceType.GRAPH_RELATIONSHIP: 0.8,
    EvidenceType.REVIEW_FINDING: 0.6,
    EvidenceType.REPAIR: 0.55,
    EvidenceType.PLAN: 0.5,
    EvidenceType.RETRIEVAL: 0.4,
    EvidenceType.HISTORICAL_MEMORY: 0.3,
    EvidenceType.AGENT_CLAIM: 0.1,
}

_DETERMINISTIC_TYPES = {
    EvidenceType.PATCH,
    EvidenceType.TEST_RESULT,
    EvidenceType.QUALITY_GATE,
    EvidenceType.FAILURE,
    EvidenceType.SOURCE_CODE,
    EvidenceType.GRAPH_RELATIONSHIP,
}


def _tier_for(value: float) -> ConfidenceTier:
    if value >= 0.75:
        return ConfidenceTier.HIGH
    if value >= 0.5:
        return ConfidenceTier.MEDIUM
    if value > 0.0:
        return ConfidenceTier.LOW
    return ConfidenceTier.UNKNOWN


class CollaborativeReasoningEngine:
    """Deterministic reasoning over shared engineering evidence."""

    def __init__(
        self,
        session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
        collaboration: Any = None,
        impact_analyzer: Optional[Any] = None,
        engineering_graph: Any = None,
    ) -> None:
        self._factory = session_factory
        self._collaboration = collaboration
        # Injectable impact analyzer (Phase 12) for scope-vs-impact checks.
        self._impact_analyzer = impact_analyzer
        # Phase 18 — EngineeringKnowledgeGraph (write consensus/contradictions/
        # notebook/decisions into the graph; retrieve historical consensus).
        self._engineering_graph = engineering_graph
        # In-memory mirrors (always authoritative during the run process)
        self._consensus: Dict[str, List[EvidenceConsensus]] = {}
        self._contradictions: Dict[str, List[ContradictionRecord]] = {}
        self._notebooks: Dict[str, EngineeringNotebook] = {}

    # ── Phase 18: Engineering Knowledge Graph ───────────────────

    def _get_engineering_graph(self) -> Any:
        """Lazily initialize the EngineeringKnowledgeGraph service."""
        if self._engineering_graph is None:
            try:
                from app.services.engineering_graph_service import (
                    EngineeringKnowledgeGraphService,
                )

                self._engineering_graph = EngineeringKnowledgeGraphService(
                    session_factory=self._factory
                )
            except Exception as exc:
                logger.debug("EKG unavailable for reasoning: %s", exc)
                self._engineering_graph = None
        return self._engineering_graph

    async def _sync_to_graph(
        self,
        run: Any,
        consensus: List[EvidenceConsensus],
        contradictions: List[ContradictionRecord],
        notebook: Optional[EngineeringNotebook],
    ) -> None:
        """Phase 18: write reasoning artifacts into the knowledge graph.

        Idempotent and non-fatal — graph enrichment never blocks reasoning.
        """
        graph = self._get_engineering_graph()
        if graph is None:
            return
        try:
            from app.models.engineering_graph import EKNodeType, EKRelationshipType

            run_id = run.run_id
            updated_nodes: List[str] = []
            updated_edges: List[str] = []

            run_node = graph.add_node(
                EKNodeType.RUN,
                f"run:{run_id}",
                source_ref=run_id, source_type="run",
                qualified_name=run_id,
                payload={
                    "status": getattr(getattr(run, "status", None), "value", "") or "",
                },
                provenance={"run_id": run_id, "source": "reasoning"},
            )
            updated_nodes.append(run_node.node_id)

            for c in consensus[:10]:
                cid = c.consensus_id
                c_node = graph.add_node(
                    EKNodeType.CONSENSUS, f"consensus:{c.topic[:80]}",
                    node_id=graph._stable_id(EKNodeType.CONSENSUS, cid),
                    source_ref=cid, source_type="consensus",
                    qualified_name=f"consensus:{c.topic}",
                    payload={
                        "status": c.status.value,
                        "confidence": round(c.confidence.value, 2),
                        "final_decision": c.final_decision[:200],
                    },
                    provenance={"run_id": run_id, "source": "reasoning"},
                )
                updated_nodes.append(c_node.node_id)
                edge = graph.add_edge(run_node.node_id, c_node.node_id,
                                      EKRelationshipType.PRODUCED_BY, metadata={"run_id": run_id})
                if edge:
                    updated_edges.append(edge.edge_id)

            for cd in contradictions[:10]:
                cid = cd.contradiction_id
                cd_node = graph.add_node(
                    EKNodeType.CONTRADICTION, f"contradiction:{getattr(cd.kind, 'value', '')}",
                    node_id=graph._stable_id(EKNodeType.CONTRADICTION, cid),
                    source_ref=cid, source_type="contradiction",
                    qualified_name=cd.description[:200],
                    payload={
                        "kind": getattr(cd.kind, "value", ""),
                        "resolution": cd.resolution[:50],
                    },
                    provenance={"run_id": run_id, "source": "reasoning"},
                )
                updated_nodes.append(cd_node.node_id)

            if notebook is not None:
                nid = notebook.notebook_id
                nb_node = graph.add_node(
                    EKNodeType.NOTEBOOK_ENTRY, f"notebook:{run_id}",
                    node_id=graph._stable_id(EKNodeType.NOTEBOOK_ENTRY, nid),
                    source_ref=nid, source_type="notebook",
                    qualified_name=f"notebook:{run_id}",
                    payload={
                        "accepted_decisions": len(notebook.accepted_decisions),
                        "rejected_decisions": len(notebook.rejected_decisions),
                        "conflicts": len(notebook.conflicts),
                        "consensus": len(notebook.consensus),
                        "timeline": len(notebook.timeline),
                    },
                    provenance={"run_id": run_id, "source": "reasoning"},
                )
                updated_nodes.append(nb_node.node_id)
                edge = graph.add_edge(run_node.node_id, nb_node.node_id,
                                      EKRelationshipType.PRODUCED_BY, metadata={"run_id": run_id})
                if edge:
                    updated_edges.append(edge.edge_id)

            graph.increment_version(
                run_id=run_id,
                summary=f"Reasoning artifacts for {run_id}",
                updated_nodes=updated_nodes,
                updated_edges=updated_edges,
            )
            await graph._persist_ingested_nodes(updated_nodes)
            await graph._persist_ingested_edges(updated_edges)
        except Exception as exc:
            logger.debug("EKG sync skipped (non-fatal): %s", exc)

    async def retrieve_historical_consensus(self, query_text: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Phase 18: retrieve historical consensus from the knowledge graph.

        Delegates to the EKG query planner (bounded, evidence-only).
        """
        graph = self._get_engineering_graph()
        if graph is None:
            return []
        try:
            result = await graph.query(query_text, limit=limit)
            items: List[Dict[str, Any]] = []
            for node in result.nodes:
                if node.node_type.value == "consensus":
                    items.append(node.summary())
            return items[:limit]
        except Exception as exc:
            logger.debug("Historical consensus retrieval failed: %s", exc)
            return []

    # ── Service accessors ────────────────────────────────────────

    def _get_collaboration(self) -> Any:
        if self._collaboration is None:
            try:
                from app.services.collaboration_service import CollaborationService

                self._collaboration = CollaborationService(session_factory=self._factory)
            except Exception as exc:
                logger.debug("Collaboration unavailable for reasoning: %s", exc)
                self._collaboration = None
        return self._collaboration

    def _get_impact_analyzer(self) -> Any:
        """Lazily build an impact analyzer over the run's repository graph."""
        if self._impact_analyzer is None:
            try:
                from app.code_intelligence.impact_analyzer import ImpactAnalysisService

                self._impact_analyzer = ImpactAnalysisService()
            except Exception as exc:
                logger.debug("Impact analyzer unavailable: %s", exc)
                self._impact_analyzer = None
        return self._impact_analyzer

    def _get_factory(self) -> Optional[async_sessionmaker[AsyncSession]]:
        if self._factory is None:
            try:
                self._factory = create_session_factory()
            except Exception as exc:
                logger.debug("Reasoning DB unavailable (in-memory): %s", exc)
                self._factory = None
        return self._factory

    async def _with_session(self, callback, fallback: Any = None) -> Any:
        factory = self._get_factory()
        if factory is None:
            return fallback
        try:
            async with factory() as session:
                return await callback(session)
        except Exception as exc:
            logger.debug("Reasoning DB op failed (in-memory fallback): %s", exc)
            return fallback

    # ── Confidence model (§4) ────────────────────────────────────

    def compute_confidence(self, evidence_refs: List[EvidenceRef]) -> ConfidenceScore:
        """Evidence-driven bounded confidence.

        Weighted by authority; deterministic evidence dominates claims.
        No LLM and no hidden reasoning — purely arithmetic over evidence.
        """
        if not evidence_refs:
            return ConfidenceScore(
                value=0.0,
                tier=ConfidenceTier.UNKNOWN,
                evidence_count=0,
                basis="No evidence available",
            )

        total = 0.0
        weight_sum = 0.0
        deterministic = 0
        claims = 0
        for ref in evidence_refs:
            authority = _EVIDENCE_AUTHORITY.get(ref.type, 0.1)
            weight = 1.0 if ref.confidence is None else max(0.0, min(1.0, float(ref.confidence)))
            total += authority * weight
            weight_sum += authority
            if ref.type in _DETERMINISTIC_TYPES:
                deterministic += 1
            else:
                claims += 1

        value = (total / weight_sum) if weight_sum > 0 else 0.0
        # Bounded: a claim-only mix can never reach HIGH.
        if deterministic == 0:
            value = min(value, 0.49)

        basis_parts = [f"{len(evidence_refs)} evidence item(s)"]
        if deterministic:
            basis_parts.append(f"{deterministic} deterministic")
        if claims:
            basis_parts.append(f"{claims} claim(s)")
        return ConfidenceScore(
            value=round(value, 3),
            tier=_tier_for(value),
            evidence_count=len(evidence_refs),
            deterministic_count=deterministic,
            claim_count=claims,
            basis=", ".join(basis_parts),
        )

    # ── Evidence collection ──────────────────────────────────────

    def collect_evidence(self, run: Any) -> Dict[str, List[EvidenceRef]]:
        """Gather deterministic evidence refs per agent from the run state."""
        evidence: Dict[str, List[EvidenceRef]] = {
            "planner": [],
            "coding": [],
            "testing": [],
            "repair": [],
            "reviewer": [],
            "graph": [],
            "memory": [],
            "gate": [],
        }

        # Planner — the plan is deterministic-ish evidence.
        if run.plan:
            evidence["planner"].append(EvidenceRef(
                type=EvidenceType.PLAN,
                reference=(run.plan.summary or run.plan.objective or "")[:200],
                detail="Implementation plan",
                confidence=0.6,
            ))

        # Coding — patch evidence is deterministic.
        if run.patch_set and run.patch_set.changes:
            evidence["coding"].append(EvidenceRef(
                type=EvidenceType.PATCH,
                reference=f"{len(run.patch_set.changes)} file(s) changed",
                detail="Patch produced by coding agent",
                confidence=1.0,
            ))

        # Testing — test result is deterministic.
        if run.test_result:
            status = run.test_result.status.value
            evidence["testing"].append(EvidenceRef(
                type=EvidenceType.TEST_RESULT,
                reference=status,
                detail=f"Tests: {getattr(run.test_result, 'tests_total', 0)} total, "
                       f"{getattr(run.test_result, 'tests_failed', 0)} failed",
                confidence=1.0,
            ))
            if run.test_result.failures:
                evidence["testing"].append(EvidenceRef(
                    type=EvidenceType.FAILURE,
                    reference=",".join(
                        getattr(f, "test_name", getattr(f, "name", ""))[:60]
                        for f in run.test_result.failures[:5]
                    ),
                    detail=f"{len(run.test_result.failures)} failure(s)",
                    confidence=1.0,
                ))

        # Repair — bounded repair evidence. RepairResult has no `summary`
        # field, so derive the detail from stop_reason when available.
        if run.repair_result:
            repair_detail = getattr(run.repair_result, "stop_reason", "") or ""
            evidence["repair"].append(EvidenceRef(
                type=EvidenceType.REPAIR,
                reference=run.repair_result.status.value,
                detail=repair_detail[:200],
                confidence=0.9,
            ))

        # Reviewer — findings + verdict.
        if run.review_report:
            findings = getattr(run.review_report, "findings", None) or []
            evidence["reviewer"].append(EvidenceRef(
                type=EvidenceType.REVIEW_FINDING,
                reference=getattr(run.review_report, "verdict", ""),
                detail=f"{len(findings)} finding(s)",
                confidence=0.8,
            ))

        # Quality gate — deterministic.
        if run.quality_gate_result:
            evidence["gate"].append(EvidenceRef(
                type=EvidenceType.QUALITY_GATE,
                reference=run.quality_gate_result.decision.value,
                detail="Deterministic quality gate decision",
                confidence=1.0,
            ))

        # Graph — semantic graph impact evidence (Phase 12).
        graph_refs = self._collect_graph_evidence(run)
        if graph_refs:
            evidence["graph"] = graph_refs

        # Memory — repository memory claims (low authority).
        mem_refs = self._collect_memory_evidence(run)
        if mem_refs:
            evidence["memory"] = mem_refs

        return evidence

    def _collect_graph_evidence(self, run: Any) -> List[EvidenceRef]:
        """Impact analysis over the run's changed files via the graph."""
        changed_files: List[str] = []
        if run.patch_set and run.patch_set.changes:
            changed_files = [c.path for c in run.patch_set.changes][:20]
        if not changed_files:
            return []
        try:
            analyzer = self._get_impact_analyzer()
            if analyzer is None:
                return []
            result = analyzer.analyze_files(
                file_paths=changed_files,
                max_depth=3,
                max_nodes=150,
            )
            affected = getattr(result, "affected_files", None) or []
            if not affected:
                return []
            return [EvidenceRef(
                type=EvidenceType.GRAPH_RELATIONSHIP,
                reference=affected[0][:200],
                detail=f"Impact analysis: {len(affected)} file(s) transitively affected",
                confidence=0.8,
            )]
        except Exception as exc:
            logger.debug("Graph evidence unavailable: %s", exc)
            return []

    def _collect_memory_evidence(self, run: Any) -> List[EvidenceRef]:
        """Historical memory claims (low authority, best-effort)."""
        try:
            collab = self._get_collaboration()
            memory = getattr(collab, "_memory_service", None)
            if memory is None:
                return []
            repo_id = None
            if run.repository_path:
                repo_id = run.repository_path.rstrip("/\\").split("/")[-1].split("\\")[-1]
            if not repo_id:
                return []
            stats = getattr(memory, "get_stats", None)
            if not callable(stats):
                return []
            stats_data = getattr(stats(repo_id), "model_dump", lambda: {})()
            return [EvidenceRef(
                type=EvidenceType.HISTORICAL_MEMORY,
                reference=repo_id,
                detail=f"Repository memory: {stats_data.get('total', 0)} memory item(s)",
                confidence=0.3,
            )]
        except Exception as exc:
            logger.debug("Memory evidence unavailable: %s", exc)
            return []

    # ── Contradiction detection (§3) ─────────────────────────────

    async def detect_contradictions(self, run: Any) -> List[ContradictionRecord]:
        """Detect contradictions between agent claims and deterministic evidence.

        Kinds:
        - CLAIM_VS_TEST: a handoff/claim says success but tests failed.
        - SCOPE_VS_IMPACT: the plan scoped changes to X but the impact graph
          says Y is also affected (deterministic evidence outranks scope claims).
        - CLAIM_VS_GATE: a claim says ready/approved but the gate rejected.
        """
        contradictions: List[ContradictionRecord] = []
        run_id = run.run_id

        # 1. Claim vs test — from collaboration handoffs (Phase 15 records).
        collab = self._get_collaboration()
        handoffs: List[Any] = []
        if collab is not None:
            try:
                handoffs = await collab.list_handoffs(run_id)
            except Exception as exc:
                logger.debug("Handoff listing failed: %s", exc)

        test_passed: Optional[bool] = None
        if run.test_result:
            test_passed = run.test_result.status.value in ("passed", "succeeded")

        for handoff in handoffs[:20]:
            # Only handoffs from agents that claim test outcomes can trigger
            # a claim-vs-test contradiction — a planner's "Plan complete" is
            # not a claim that tests passed.
            if getattr(handoff, "from_agent", "") not in ("coding", "testing", "repair"):
                continue
            claim_text = f"{handoff.summary} {' '.join(handoff.decisions)}".lower()
            claims_pass = any(w in claim_text for w in (
                "tests passed", "tests green", "tests succeeded", "test run passed",
                "passed", "succeeded", "success", "complete", "done",
            ))
            if claims_pass and test_passed is False:
                contradictions.append(ContradictionRecord(
                    run_id=run_id,
                    kind=ContradictionKind.CLAIM_VS_TEST,
                    description=(
                        f"{handoff.from_agent} claimed success but test evidence "
                        f"reports failure"
                    )[:300],
                    claim_evidence=EvidenceRef(
                        type=EvidenceType.AGENT_CLAIM,
                        reference=handoff.handoff_id,
                        detail=handoff.summary[:200],
                        confidence=0.4,
                    ),
                    deterministic_evidence=EvidenceRef(
                        type=EvidenceType.TEST_RESULT,
                        reference="failed",
                        detail="Actual test result reported failures",
                        confidence=1.0,
                    ),
                    resolution="deterministic_wins",
                ))

        # 2. Claim vs gate.
        if run.quality_gate_result:
            gate_decision = run.quality_gate_result.decision.value
            for handoff in handoffs[:20]:
                claim_text = f"{handoff.summary} {' '.join(handoff.decisions)}".lower()
                claims_ready = any(w in claim_text for w in (
                    "ready", "approved", "complete", "done", "quality gate passed",
                ))
                if claims_ready and gate_decision == "rejected":
                    contradictions.append(ContradictionRecord(
                        run_id=run_id,
                        kind=ContradictionKind.CLAIM_VS_GATE,
                        description=(
                            f"{handoff.from_agent} claimed readiness but the "
                            f"quality gate rejected"
                        )[:300],
                        claim_evidence=EvidenceRef(
                            type=EvidenceType.AGENT_CLAIM,
                            reference=handoff.handoff_id,
                            detail=handoff.summary[:200],
                            confidence=0.4,
                        ),
                        deterministic_evidence=EvidenceRef(
                            type=EvidenceType.QUALITY_GATE,
                            reference="rejected",
                            detail="Deterministic quality gate rejection",
                            confidence=1.0,
                        ),
                        resolution="deterministic_wins",
                    ))

        # 3. Scope vs impact — plan scope vs graph impact.
        scope_contradictions = self._detect_scope_vs_impact(run)
        contradictions.extend(scope_contradictions)

        # Persist new contradiction records (dedup by description).
        for c in contradictions[:MAX_CONTRADICTIONS_PER_RUN]:
            existing = [x for x in self._contradictions.get(run_id, [])
                        if x.description == c.description]
            if not existing:
                self._contradictions.setdefault(run_id, []).append(c)
                await self._persist_contradiction(c)

        return contradictions[:MAX_CONTRADICTIONS_PER_RUN]

    def _detect_scope_vs_impact(self, run: Any) -> List[ContradictionRecord]:
        """Scope vs impact: plan claims narrow scope, graph shows more."""
        changed_files: List[str] = []
        if run.patch_set and run.patch_set.changes:
            changed_files = [c.path for c in run.patch_set.changes][:20]
        if not changed_files:
            return []
        try:
            analyzer = self._get_impact_analyzer()
            if analyzer is None:
                return []
            result = analyzer.analyze_files(
                file_paths=changed_files,
                max_depth=3,
                max_nodes=150,
            )
            affected = getattr(result, "affected_files", None) or []
        except Exception as exc:
            logger.debug("Scope-vs-impact check skipped: %s", exc)
            return []

        if not affected:
            return []

        # If the plan declared affected_areas, check the impact stays inside.
        scope = set()
        if run.plan:
            for step in (run.plan.steps or []):
                for area in (getattr(step, "affected_areas", None) or []):
                    scope.add(str(area).lower())

        out_of_scope = [a for a in affected
                        if scope and not any(s in a.lower() for s in scope)]
        if not out_of_scope:
            return []

        return [ContradictionRecord(
            run_id=run.run_id,
            kind=ContradictionKind.SCOPE_VS_IMPACT,
            description=(
                f"Plan scoped changes to {sorted(scope)[:3]} but impact graph "
                f"shows additional affected file(s): {', '.join(out_of_scope[:3])}"
            )[:300],
            claim_evidence=EvidenceRef(
                type=EvidenceType.PLAN,
                reference=",".join(sorted(scope)[:3]),
                detail="Declared change area",
                confidence=0.6,
            ),
            deterministic_evidence=EvidenceRef(
                type=EvidenceType.GRAPH_RELATIONSHIP,
                reference=out_of_scope[0][:200],
                detail=f"{len(out_of_scope)} file(s) outside declared scope",
                confidence=0.8,
            ),
            resolution="unresolved",  # informational; does not reject the patch
        )]

    # ── Consensus (§2) ───────────────────────────────────────────

    async def build_consensus(
        self,
        run: Any,
        contradictions: Optional[List[ContradictionRecord]] = None,
    ) -> List[EvidenceConsensus]:
        """Build per-topic consensus records from collected evidence."""
        run_id = run.run_id
        evidence = self.collect_evidence(run)
        contradictions = contradictions or []

        consensus_list: List[EvidenceConsensus] = []

        # Topic: test_status
        test_refs = evidence["testing"]
        if test_refs:
            test_pass_refs = [r for r in test_refs if r.type == EvidenceType.TEST_RESULT
                              and r.reference in ("passed", "succeeded")]
            test_fail_refs = [r for r in test_refs if r.type == EvidenceType.TEST_RESULT
                              and r.reference not in ("passed", "succeeded")]
            supporting = test_pass_refs + [r for r in evidence["repair"]]
            failure_refs = [r for r in evidence["testing"]
                            if r.type == EvidenceType.FAILURE]
            conflicting = test_fail_refs + failure_refs
            conflicting = test_refs if not supporting else conflicting
            status = ConsensusStatus.AGREED if test_pass_refs else (
                ConsensusStatus.CONFLICTED if test_fail_refs else ConsensusStatus.UNKNOWN
            )
            consensus_list.append(EvidenceConsensus(
                run_id=run_id,
                topic="test_status",
                summary=(
                    f"Test evidence {'PASSED' if test_pass_refs else 'FAILED'} "
                    f"({len(test_refs)} evidence item(s))"
                )[:500],
                status=status,
                confidence=self.compute_confidence(
                    (supporting or conflicting or [])[:MAX_CONSENSUS_PER_RUN]
                ),
                supporting_evidence=supporting[:MAX_EVIDENCE_PER_CONSENSUS],
                conflicting_evidence=conflicting[:MAX_EVIDENCE_PER_CONSENSUS],
                final_decision="tests_passed" if test_pass_refs else (
                    "tests_failing" if test_fail_refs else "unknown"
                ),
                contributing_agents=["testing"],
            ))

        # Topic: patch_complete — coding claim vs patch + test evidence.
        coding_refs = evidence["coding"]
        if coding_refs:
            supporting = list(coding_refs)
            conflicting = [r for r in evidence["testing"]
                           if r.type == EvidenceType.FAILURE]
            has_failure = bool(conflicting)
            consensus_list.append(EvidenceConsensus(
                run_id=run_id,
                topic="patch_complete",
                summary=(
                    "Coding produced a patch; test evidence "
                    f"{'shows failures' if has_failure else 'does not contradict it'}"
                )[:500],
                status=(
                    ConsensusStatus.CONFLICTED if has_failure
                    else ConsensusStatus.AGREED
                ),
                confidence=self.compute_confidence(
                    (supporting + (conflicting or []))[:MAX_CONSENSUS_PER_RUN]
                ),
                supporting_evidence=supporting[:MAX_EVIDENCE_PER_CONSENSUS],
                conflicting_evidence=conflicting[:MAX_EVIDENCE_PER_CONSENSUS],
                final_decision="patch_conflicts_with_tests" if has_failure else "patch_consistent",
                contributing_agents=["coding", "testing"],
            ))

        # Topic: quality_gate
        gate_refs = evidence["gate"]
        if gate_refs:
            gate = gate_refs[0]
            approved = gate.reference == "approved"
            consensus_list.append(EvidenceConsensus(
                run_id=run_id,
                topic="quality_gate",
                summary=f"Quality gate {gate.reference.upper()}",
                status=(
                    ConsensusStatus.AGREED if approved
                    else ConsensusStatus.CONFLICTED
                ),
                confidence=self.compute_confidence(gate_refs),
                supporting_evidence=gate_refs,
                conflicting_evidence=[r for r in evidence["reviewer"]
                                      if r.type == EvidenceType.REVIEW_FINDING],
                final_decision=gate.reference,
                contributing_agents=["reviewer", "gate"],
            ))

        # Topic: scope_compliance — from scope-vs-impact contradictions.
        scope_cds = [c for c in contradictions
                     if c.kind == ContradictionKind.SCOPE_VS_IMPACT]
        if scope_cds:
            consensus_list.append(EvidenceConsensus(
                run_id=run_id,
                topic="scope_compliance",
                summary=(
                    f"{len(scope_cds)} scope-vs-impact contradiction(s) detected"
                )[:500],
                status=ConsensusStatus.CONFLICTED,
                confidence=self.compute_confidence([
                    c.claim_evidence for c in scope_cds
                ] + [c.deterministic_evidence for c in scope_cds
                     if c.deterministic_evidence]),
                supporting_evidence=[c.claim_evidence for c in scope_cds],
                conflicting_evidence=[c.deterministic_evidence for c in scope_cds
                                      if c.deterministic_evidence],
                final_decision="scope_needs_review",
                contributing_agents=["planner", "graph"],
            ))

        # Persist new consensus records (dedup by topic).
        for c in consensus_list[:MAX_CONSENSUS_PER_RUN]:
            existing = [x for x in self._consensus.get(run_id, [])
                        if x.topic == c.topic]
            if not existing:
                self._consensus.setdefault(run_id, []).append(c)
                await self._persist_consensus(c)

        return consensus_list[:MAX_CONSENSUS_PER_RUN]

    # ── Engineering Notebook (§5) ────────────────────────────────

    async def build_notebook(
        self,
        run: Any,
        consensus: List[EvidenceConsensus],
        contradictions: List[ContradictionRecord],
        handoffs: Optional[List[Any]] = None,
        decisions: Optional[List[Any]] = None,
    ) -> EngineeringNotebook:
        """Assemble the shared engineering notebook for a run."""
        run_id = run.run_id
        collab = self._get_collaboration()
        if handoffs is None and collab is not None:
            try:
                handoffs = await collab.list_handoffs(run_id)
            except Exception:
                handoffs = []
        if decisions is None and collab is not None:
            try:
                decisions = await collab.list_decisions(run_id)
            except Exception:
                decisions = []
        handoffs = handoffs or []
        decisions = decisions or []

        resolved = [c for c in contradictions if c.resolution != "unresolved"]
        unresolved = [c for c in contradictions if c.resolution == "unresolved"]

        timeline: List[NotebookEntry] = []
        for d in decisions[:20]:
            timeline.append(NotebookEntry(
                run_id=run_id,
                entry_type=NotebookEntryType.DECISION,
                label=f"decision:{getattr(d, 'decision_type', 'unknown')}",
                detail=d.statement[:300],
                evidence_refs=d.evidence_refs[:5],
            ))
        for c in consensus[:20]:
            timeline.append(NotebookEntry(
                run_id=run_id,
                entry_type=NotebookEntryType.CONSENSUS,
                label=f"consensus:{c.topic}",
                detail=f"{c.status.value} — {c.final_decision[:200]}",
            ))
        for c in contradictions[:20]:
            timeline.append(NotebookEntry(
                run_id=run_id,
                entry_type=NotebookEntryType.CONTRADICTION,
                label=f"contradiction:{c.kind.value}",
                detail=c.description[:300],
            ))

        accepted = [{
            "decision_id": d.decision_id,
            "decision_type": getattr(d, "decision_type", "unknown"),
            "statement": d.statement[:300],
            "made_by": d.made_by,
        } for d in decisions][:50]

        notebook_id = getattr(run, "notebook_id", None)
        if not notebook_id:
            # run_id is unique (RUN-XXXXXXXX), so the notebook id is unique per
            # run. The previous NB-{run_id[:4]} form truncated to "RUN-" and
            # collided on uq_engineering_notebooks_notebook_id whenever two runs
            # produced the same timeline length.
            notebook_id = f"NB-{run_id}"
        rejected = [{
            "contradiction_id": c.contradiction_id,
            "kind": c.kind.value,
            "description": c.description[:300],
            "resolution": c.resolution,
        } for c in resolved][:50]

        notebook = EngineeringNotebook(
            notebook_id=notebook_id,
            run_id=run_id,
            task=(run.source.title or "")[:500],
            accepted_decisions=accepted[:50],
            rejected_decisions=rejected[:50],
            conflicts=unresolved[:MAX_CONTRADICTIONS_PER_RUN],
            resolved_conflicts=resolved[:MAX_CONTRADICTIONS_PER_RUN],
            consensus=consensus[:MAX_CONSENSUS_PER_RUN],
            timeline=timeline[:MAX_NOTEBOOK_ENTRIES],
        )
        self._notebooks[run_id] = notebook
        await self._persist_notebook(notebook)
        return notebook

    # ── One-call pipeline ────────────────────────────────────────

    async def analyze_run(self, run: Any) -> Dict[str, Any]:
        """Detect → consensus → notebook for a run. Never raises."""
        try:
            contradictions = await self.detect_contradictions(run)
            consensus = await self.build_consensus(run, contradictions)
            notebook = await self.build_notebook(run, consensus, contradictions)
            outcome = {
                "run_id": run.run_id,
                "consensus": consensus,
                "contradictions": contradictions,
                "notebook": notebook,
                "confidence": self.compute_confidence(
                    self._all_evidence(run)
                ),
            }
            # Phase 18: write reasoning artifacts into the knowledge graph.
            await self._sync_to_graph(run, consensus, contradictions, notebook)
            return outcome
        except Exception as exc:
            logger.debug("Reasoning analysis failed for %s: %s", run.run_id, exc)
            return {
                "run_id": run.run_id,
                "consensus": [],
                "contradictions": [],
                "notebook": None,
                "confidence": ConfidenceScore(),
            }

    def _all_evidence(self, run: Any) -> List[EvidenceRef]:
        refs: List[EvidenceRef] = []
        for bucket in self.collect_evidence(run).values():
            refs.extend(bucket)
        return refs[:50]

    # ── Persistence ──────────────────────────────────────────────

    async def _persist_consensus(self, consensus: EvidenceConsensus) -> None:
        async def _impl(session: AsyncSession) -> None:
            session.add(EvidenceConsensusModel(
                consensus_id=consensus.consensus_id,
                run_id=consensus.run_id,
                topic=consensus.topic,
                summary=consensus.summary,
                status=consensus.status.value,
                confidence_json=consensus.confidence.model_dump(),
                supporting_evidence=[e.model_dump() for e in consensus.supporting_evidence] or None,
                conflicting_evidence=[e.model_dump() for e in consensus.conflicting_evidence] or None,
                final_decision=consensus.final_decision,
                contributing_agents=consensus.contributing_agents or None,
            ))
            await session.commit()

        await self._with_session(_impl)

    async def _persist_contradiction(self, contradiction: ContradictionRecord) -> None:
        async def _impl(session: AsyncSession) -> None:
            session.add(ContradictionRecordModel(
                contradiction_id=contradiction.contradiction_id,
                run_id=contradiction.run_id,
                kind=contradiction.kind.value,
                description=contradiction.description,
                claim_evidence=contradiction.claim_evidence.model_dump(),
                deterministic_evidence=(
                    contradiction.deterministic_evidence.model_dump()
                    if contradiction.deterministic_evidence else None
                ),
                resolution=contradiction.resolution,
            ))
            await session.commit()

        await self._with_session(_impl)

    async def _persist_notebook(self, notebook: EngineeringNotebook) -> None:
        async def _impl(session: AsyncSession) -> None:
            stmt = select(EngineeringNotebookModel).where(
                EngineeringNotebookModel.run_id == notebook.run_id
            )
            model = (await session.execute(stmt)).scalar_one_or_none()
            now = _utcnow_for_db()
            if model is None:
                session.add(EngineeringNotebookModel(
                    notebook_id=notebook.notebook_id,
                    run_id=notebook.run_id,
                    task=notebook.task,
                    accepted_decisions=notebook.accepted_decisions or None,
                    rejected_decisions=notebook.rejected_decisions or None,
                    conflicts=[c.model_dump() for c in notebook.conflicts] or None,
                    resolved_conflicts=[c.model_dump() for c in notebook.resolved_conflicts] or None,
                    consensus=[c.model_dump() for c in notebook.consensus] or None,
                    timeline=[t.model_dump() for t in notebook.timeline] or None,
                    version=notebook.version,
                    updated_at=now,
                ))
            else:
                model.accepted_decisions = notebook.accepted_decisions or None
                model.rejected_decisions = notebook.rejected_decisions or None
                model.conflicts = [c.model_dump() for c in notebook.conflicts] or None
                model.resolved_conflicts = [c.model_dump() for c in notebook.resolved_conflicts] or None
                model.consensus = [c.model_dump() for c in notebook.consensus] or None
                model.timeline = [t.model_dump() for t in notebook.timeline] or None
                model.version = notebook.version
                model.updated_at = now
            await session.commit()

        await self._with_session(_impl)

    # ── Read / Recovery ──────────────────────────────────────────

    async def list_consensus(self, run_id: str, limit: int = 50) -> List[EvidenceConsensus]:
        memory = self._consensus.get(run_id, [])
        if memory:
            return memory[: min(limit, MAX_CONSENSUS_PER_RUN)]

        async def _impl(session: AsyncSession) -> List[EvidenceConsensus]:
            stmt = (
                select(EvidenceConsensusModel)
                .where(EvidenceConsensusModel.run_id == run_id)
                .order_by(EvidenceConsensusModel.created_at.asc())
                .limit(min(limit, MAX_CONSENSUS_PER_RUN))
            )
            result = await session.execute(stmt)
            return [_consensus_from_model(m) for m in result.scalars().all()]

        return await self._with_session(_impl, fallback=[])

    async def list_contradictions(
        self, run_id: str, limit: int = 50,
    ) -> List[ContradictionRecord]:
        memory = self._contradictions.get(run_id, [])
        if memory:
            return memory[: min(limit, MAX_CONTRADICTIONS_PER_RUN)]

        async def _impl(session: AsyncSession) -> List[ContradictionRecord]:
            stmt = (
                select(ContradictionRecordModel)
                .where(ContradictionRecordModel.run_id == run_id)
                .order_by(ContradictionRecordModel.created_at.asc())
                .limit(min(limit, MAX_CONTRADICTIONS_PER_RUN))
            )
            result = await session.execute(stmt)
            return [_contradiction_from_model(m) for m in result.scalars().all()]

        return await self._with_session(_impl, fallback=[])

    async def get_notebook(self, run_id: str) -> Optional[EngineeringNotebook]:
        memory = self._notebooks.get(run_id)
        if memory is not None:
            return memory

        async def _impl(session: AsyncSession) -> Optional[EngineeringNotebook]:
            stmt = select(EngineeringNotebookModel).where(
                EngineeringNotebookModel.run_id == run_id
            )
            model = (await session.execute(stmt)).scalar_one_or_none()
            if model is None:
                return None
            notebook = EngineeringNotebook(
                notebook_id=model.notebook_id,
                run_id=model.run_id,
                task=model.task or "",
                accepted_decisions=model.accepted_decisions or [],
                rejected_decisions=model.rejected_decisions or [],
                conflicts=[ContradictionRecord(**c) for c in (model.conflicts or [])],
                resolved_conflicts=[
                    ContradictionRecord(**c) for c in (model.resolved_conflicts or [])
                ],
                consensus=[EvidenceConsensus(**c) for c in (model.consensus or [])],
                timeline=[NotebookEntry(**t) for t in (model.timeline or [])],
                version=model.version,
            )
            if model.created_at is not None:
                try:
                    notebook.created_at = model.created_at.isoformat()
                except Exception:
                    pass
            if model.updated_at is not None:
                try:
                    notebook.updated_at = model.updated_at.isoformat()
                except Exception:
                    pass
            return notebook

        return await self._with_session(_impl, fallback=None)

    async def recover(self, run_id: str) -> None:
        """Rehydrate persisted reasoning state after a restart."""
        self._consensus[run_id] = await self.list_consensus(run_id)
        self._contradictions[run_id] = await self.list_contradictions(run_id)
        notebook = await self.get_notebook(run_id)
        if notebook is not None:
            self._notebooks[run_id] = notebook


# ── Helpers ─────────────────────────────────────────────────────

def _utcnow_for_db():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def _consensus_from_model(m: Any) -> EvidenceConsensus:
    created_at = None
    if m.created_at is not None:
        try:
            created_at = m.created_at.isoformat()
        except Exception:
            created_at = str(m.created_at)
    return EvidenceConsensus(
        consensus_id=m.consensus_id,
        run_id=m.run_id,
        topic=m.topic,
        summary=m.summary or "",
        status=ConsensusStatus(m.status),
        confidence=ConfidenceScore(**(m.confidence_json or {})),
        supporting_evidence=[
            EvidenceRef(**e) for e in (m.supporting_evidence or [])
        ],
        conflicting_evidence=[
            EvidenceRef(**e) for e in (m.conflicting_evidence or [])
        ],
        final_decision=m.final_decision or "",
        contributing_agents=m.contributing_agents or [],
        created_at=created_at or _utcnow_iso(),
    )


def _contradiction_from_model(m: Any) -> ContradictionRecord:
    created_at = None
    if m.created_at is not None:
        try:
            created_at = m.created_at.isoformat()
        except Exception:
            created_at = str(m.created_at)
    return ContradictionRecord(
        contradiction_id=m.contradiction_id,
        run_id=m.run_id,
        kind=ContradictionKind(m.kind),
        description=m.description,
        claim_evidence=EvidenceRef(**m.claim_evidence) if m.claim_evidence else EvidenceRef(type=EvidenceType.AGENT_CLAIM),
        deterministic_evidence=(
            EvidenceRef(**m.deterministic_evidence)
            if m.deterministic_evidence else None
        ),
        resolution=m.resolution,
        created_at=created_at or _utcnow_iso(),
    )
