"""
Phase 15 — CollaborationService.

Owns the Shared Run Intelligence layer: structured handoffs, decision
records, evidence conflict detection, and shared run summaries.

Separation of concerns (per Phase 15 spec):
    CollaborationService = what agents produced/shared
    ContextEngine        = what evidence an agent receives

Agents share *engineering evidence* — never chain-of-thought. Every
agent claim is validated deterministically where possible; deterministic
evidence always outranks LLM claims.

Persistence: mirrors to PostgreSQL (agent_handoffs / run_decisions /
evidence_conflicts, migration 006) when available, and keeps an
in-memory copy so the system degrades gracefully when the DB is down.
Recovery: `recover(run_id)` rehydrates persisted state after restart.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import logger
from app.db.models import (
    AgentHandoffModel,
    EvidenceConflictModel,
    RunDecisionModel,
)
from app.db.session import create_session_factory
from app.models.collaboration import (
    MAX_CONFLICTS_PER_RUN,
    MAX_DECISIONS_PER_RUN,
    MAX_EVIDENCE_PER_HANDOFF,
    MAX_HANDOFFS_PER_RUN,
    MAX_HANDOFFS_SELECTED,
    AgentHandoff,
    ConflictResolution,
    EvidenceConflict,
    EvidenceRef,
    EvidenceType,
    HandoffStatus,
    RunDecision,
    SharedRunContext,
)


# ── Secret redaction (Security: §31) ────────────────────────────
# Handoff content is untrusted. Strip obvious secret-like tokens
# before durable storage / frontend exposure.

_SECRET_PATTERNS: List[re.Pattern] = [
    re.compile(r"(?i)(api[_-]?key|secret|token|password|passwd|credential)\s*[:=]\s*['\"]?[A-Za-z0-9_\-\.]{8,}"),
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    re.compile(r"ghp_[A-Za-z0-9]{16,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
]


def redact_secrets(text: str) -> str:
    """Redact obvious secret-like tokens from untrusted text."""
    if not text:
        return text
    out = text
    for pattern in _SECRET_PATTERNS:
        out = pattern.sub("[REDACTED]", out)
    return out


# ── Evidence authority hierarchy (§12) ───────────────────────────
# Higher = more authoritative. Deterministic evidence outranks claims.

_EVIDENCE_AUTHORITY = {
    EvidenceType.PATCH: 100,
    EvidenceType.TEST_RESULT: 95,
    EvidenceType.QUALITY_GATE: 90,
    EvidenceType.FAILURE: 88,
    EvidenceType.SOURCE_CODE: 85,
    EvidenceType.GRAPH_RELATIONSHIP: 80,
    EvidenceType.REVIEW_FINDING: 60,
    EvidenceType.REPAIR: 55,
    EvidenceType.PLAN: 50,
    EvidenceType.RETRIEVAL: 40,
    EvidenceType.HISTORICAL_MEMORY: 30,
    EvidenceType.AGENT_CLAIM: 10,
}


class CollaborationService:
    """Structured multi-agent collaboration store + validation."""

    def __init__(
        self,
        session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
        memory_service: Optional[Any] = None,
    ) -> None:
        self._factory = session_factory
        self._memory_service = memory_service
        # In-memory mirrors (always authoritative during the run process)
        self._handoffs: Dict[str, List[AgentHandoff]] = {}
        self._decisions: Dict[str, List[RunDecision]] = {}
        self._conflicts: Dict[str, List[EvidenceConflict]] = {}

    # ── Persistence plumbing ─────────────────────────────────────

    def _get_factory(self) -> Optional[async_sessionmaker[AsyncSession]]:
        if self._factory is None:
            try:
                self._factory = create_session_factory()
            except Exception as exc:
                logger.debug("Collaboration DB unavailable (in-memory): %s", exc)
                self._factory = None
        return self._factory

    async def _with_session(self, callback, fallback: Any = None) -> Any:
        """Run callback(session) when DB available; else return fallback."""
        factory = self._get_factory()
        if factory is None:
            return fallback
        try:
            async with factory() as session:
                return await callback(session)
        except Exception as exc:
            logger.debug("Collaboration DB op failed (in-memory fallback): %s", exc)
            return fallback

    # ── SharedRunContext ─────────────────────────────────────────

    async def build_shared_run_context(self, run: Any) -> SharedRunContext:
        """Assemble the authoritative per-run collaboration summary.

        Bounded: handoffs/decisions/conflicts are capped by constants.
        """
        ctx = SharedRunContext(
            run_id=run.run_id,
            task=(run.source.title or "")[:500],
        )

        if run.repository_path:
            ctx.plan_ref = getattr(run.plan, "summary", None) or getattr(run.plan, "objective", None)
            ctx.requirements_ref = getattr(run.requirements, "objective", None) or ""

        # Changed files / symbols (deterministic, from the actual patch)
        changed_files: List[str] = []
        changed_symbols: List[str] = []
        if run.patch_set and run.patch_set.changes:
            changed_files = [c.path for c in run.patch_set.changes]
        if run.patch_result:
            symbols = getattr(run.patch_result, "changed_symbols", None) or []
            changed_symbols = [s for s in symbols if isinstance(s, str)][:20]
        ctx.changed_files = changed_files[:50]
        ctx.changed_symbols = changed_symbols

        # Deterministic evidence refs
        if run.test_result:
            status = run.test_result.status.value
            ctx.test_evidence.append(EvidenceRef(
                type=EvidenceType.TEST_RESULT,
                reference=status,
                detail=f"Test run status: {status}",
                confidence=1.0,
            ))
        if run.repair_result:
            ctx.repair_evidence.append(EvidenceRef(
                type=EvidenceType.REPAIR,
                reference=run.repair_result.status.value,
                detail=run.repair_result.summary[:200],
                confidence=0.9,
            ))
        if run.review_report:
            ctx.review_evidence.append(EvidenceRef(
                type=EvidenceType.REVIEW_FINDING,
                reference=run.review_report.verdict.value
                if hasattr(run.review_report.verdict, "value") else str(run.review_report.verdict),
                detail=f"{len(run.review_report.findings or [])} finding(s)",
                confidence=0.8,
            ))
        if run.quality_gate_result:
            ctx.review_evidence.append(EvidenceRef(
                type=EvidenceType.QUALITY_GATE,
                reference=run.quality_gate_result.decision.value,
                detail="Deterministic quality gate decision",
                confidence=1.0,
            ))

        # Collaboration records (bounded)
        ctx.agent_handoffs = (await self.list_handoffs(run.run_id))[:MAX_HANDOFFS_PER_RUN]
        ctx.decisions = (await self.list_decisions(run.run_id))[:MAX_DECISIONS_PER_RUN]
        ctx.conflicts = (await self.list_conflicts(run.run_id))[:MAX_CONFLICTS_PER_RUN]
        ctx.warnings = list(run.warnings or [])[:10]

        ctx.version = max(1, len(ctx.agent_handoffs) + len(ctx.decisions) + len(ctx.conflicts))
        return ctx

    # ── Handoffs ─────────────────────────────────────────────────

    async def create_handoff(
        self,
        run_id: str,
        from_agent: str,
        to_agent: str,
        stage: str,
        summary: str = "",
        decisions: Optional[List[str]] = None,
        evidence_refs: Optional[List[EvidenceRef]] = None,
        artifact_refs: Optional[List[str]] = None,
        affected_symbols: Optional[List[str]] = None,
        warnings: Optional[List[str]] = None,
        open_questions: Optional[List[str]] = None,
    ) -> Optional[AgentHandoff]:
        """Create a structured handoff. Bounded + redacted. Returns None on overflow."""
        existing = await self.list_handoffs(run_id)
        if len(existing) >= MAX_HANDOFFS_PER_RUN:
            logger.warning("Handoff limit reached for run %s — skipping", run_id)
            return None

        handoff = AgentHandoff(
            run_id=run_id,
            from_agent=from_agent,
            to_agent=to_agent,
            stage=stage,
            summary=redact_secrets(summary or "")[:500],
            decisions=[redact_secrets(d)[:300] for d in (decisions or [])][:8],
            evidence_refs=(evidence_refs or [])[:MAX_EVIDENCE_PER_HANDOFF],
            artifact_refs=(artifact_refs or [])[:8],
            affected_symbols=(affected_symbols or [])[:20],
            warnings=[redact_secrets(w)[:300] for w in (warnings or [])][:5],
            open_questions=[redact_secrets(q)[:300] for q in (open_questions or [])][:5],
        )

        # In-memory mirror (authoritative during this process)
        self._handoffs.setdefault(run_id, []).append(handoff)
        # Durable mirror (best effort)
        await self._persist_handoff(handoff)
        return handoff

    async def _persist_handoff(self, handoff: AgentHandoff) -> None:
        async def _impl(session: AsyncSession) -> None:
            model = AgentHandoffModel(
                handoff_id=handoff.handoff_id,
                run_id=handoff.run_id,
                from_agent=handoff.from_agent,
                to_agent=handoff.to_agent,
                stage=handoff.stage,
                summary=handoff.summary,
                decisions=handoff.decisions or None,
                evidence_refs=[e.model_dump() for e in handoff.evidence_refs] or None,
                artifact_refs=handoff.artifact_refs or None,
                affected_symbols=handoff.affected_symbols or None,
                warnings=handoff.warnings or None,
                open_questions=handoff.open_questions or None,
                status=handoff.status.value,
                validation=handoff.validation or None,
            )
            session.add(model)
            await session.commit()

        await self._with_session(_impl)

    async def get_handoff(self, run_id: str, handoff_id: str) -> Optional[AgentHandoff]:
        for h in self._handoffs.get(run_id, []):
            if h.handoff_id == handoff_id:
                return h

        async def _impl(session: AsyncSession) -> Optional[AgentHandoff]:
            stmt = select(AgentHandoffModel).where(
                AgentHandoffModel.run_id == run_id,
                AgentHandoffModel.handoff_id == handoff_id,
            )
            result = await session.execute(stmt)
            model = result.scalar_one_or_none()
            if model is None:
                return None
            handoff = AgentHandoff(
                handoff_id=model.handoff_id,
                run_id=model.run_id,
                from_agent=model.from_agent,
                to_agent=model.to_agent,
                stage=model.stage,
                summary=model.summary or "",
                decisions=model.decisions or [],
                evidence_refs=[
                    EvidenceRef(**e) for e in (model.evidence_refs or [])
                ],
                artifact_refs=model.artifact_refs or [],
                affected_symbols=model.affected_symbols or [],
                warnings=model.warnings or [],
                open_questions=model.open_questions or [],
                status=HandoffStatus(model.status),
                validation=model.validation or {},
            )
            self._handoffs.setdefault(run_id, []).append(handoff)
            return handoff

        return await self._with_session(_impl)

    async def list_handoffs(
        self,
        run_id: str,
        limit: int = 50,
        offset: int = 0,
        to_agent: Optional[str] = None,
    ) -> List[AgentHandoff]:
        """List handoffs for a run (bounded, oldest first)."""
        memory = self._handoffs.get(run_id, [])
        if memory:
            ordered = sorted(memory, key=lambda h: h.created_at)
            if to_agent:
                ordered = [h for h in ordered if h.to_agent == to_agent]
            return ordered[offset : offset + min(limit, MAX_HANDOFFS_PER_RUN)]

        async def _impl(session: AsyncSession) -> List[AgentHandoff]:
            stmt = (
                select(AgentHandoffModel)
                .where(AgentHandoffModel.run_id == run_id)
                .order_by(AgentHandoffModel.created_at.asc())
                .offset(offset)
                .limit(min(limit, MAX_HANDOFFS_PER_RUN))
            )
            if to_agent:
                stmt = stmt.where(AgentHandoffModel.to_agent == to_agent)
            result = await session.execute(stmt)
            return [
                AgentHandoff(
                    handoff_id=m.handoff_id,
                    run_id=m.run_id,
                    from_agent=m.from_agent,
                    to_agent=m.to_agent,
                    stage=m.stage,
                    summary=m.summary or "",
                    decisions=m.decisions or [],
                    evidence_refs=[EvidenceRef(**e) for e in (m.evidence_refs or [])],
                    artifact_refs=m.artifact_refs or [],
                    affected_symbols=m.affected_symbols or [],
                    warnings=m.warnings or [],
                    open_questions=m.open_questions or [],
                    status=HandoffStatus(m.status),
                    validation=m.validation or {},
                )
                for m in result.scalars().all()
            ]

        return await self._with_session(_impl, fallback=[])

    async def retrieve_relevant_handoffs(
        self,
        run_id: str,
        agent_type: str,
        limit: int = MAX_HANDOFFS_SELECTED,
    ) -> List[AgentHandoff]:
        """Select handoffs relevant to an agent, bounded.

        Preference: handoffs addressed to this agent, then the most
        recent handoffs overall (repair/retest loops stay visible).
        """
        all_handoffs = await self.list_handoffs(run_id, limit=MAX_HANDOFFS_PER_RUN)
        if not all_handoffs:
            return []

        direct = [h for h in all_handoffs if h.to_agent == agent_type]
        selected = direct[:limit]
        if len(selected) < limit:
            # Fill with most recent handoffs (bounded)
            recent = [h for h in reversed(all_handoffs) if h.handoff_id not in {s.handoff_id for s in selected}]
            selected.extend(recent[: limit - len(selected)])
        return selected[:limit]

    async def validate_handoff(
        self,
        handoff: AgentHandoff,
        changed_files: Optional[List[str]] = None,
        changed_symbols: Optional[List[str]] = None,
        test_passed: Optional[bool] = None,
        test_failures: Optional[List[Any]] = None,
    ) -> AgentHandoff:
        """Deterministically validate handoff claims (§10).

        - affected symbols vs actual patch files/symbols → validated/unverified
        - test claims vs actual test result → validated/rejected
        Deterministic evidence never loses to an agent claim.
        """
        validation: Dict[str, str] = {}
        changed_files = changed_files or []
        changed_symbols = changed_symbols or []

        # Symbol claims — match against actual patch files/symbols.
        # The symbol may be a qualified name (auth_service.py::AuthService),
        # a file path, or a bare symbol name; match its file component.
        for sym in handoff.affected_symbols:
            file_part = sym.split("::")[0]
            symbol_matched = (
                sym in changed_symbols
                or sym in changed_files
                or file_part in changed_files
                or any(file_part in f for f in changed_files if f)
                or any(Path(f).stem in sym for f in changed_files if f)
            )
            validation[f"symbol:{sym}"] = "validated" if symbol_matched else "unverified"

        # Test claims — only validate when the claim actually asserts an
        # outcome; a mention of 'test' without pass/fail is unverified.
        claim_text = f"{handoff.summary} {' '.join(handoff.decisions)}".lower()
        claim_says_pass = any(w in claim_text for w in (
            "tests passed", "all tests pass", "tests green", "tests succeeded",
            "test run passed", "passed", "succeeded", "success",
        ))
        claim_says_fail = any(w in claim_text for w in (
            "tests failed", "test failure", "failing", "failed", "not passing",
        ))
        actual_pass = bool(test_passed) if test_passed is not None else None
        if actual_pass is not None:
            if claim_says_pass and not actual_pass:
                validation["test:claim"] = "rejected"
            elif claim_says_pass and actual_pass:
                validation["test:claim"] = "validated"
            elif claim_says_fail and actual_pass:
                validation["test:claim"] = "rejected"
            elif claim_says_fail and not actual_pass:
                validation["test:claim"] = "validated"
            else:
                validation["test:claim"] = "unverified"

        handoff.validation = validation
        if "rejected" in validation.values():
            handoff.status = HandoffStatus.REJECTED
        elif "validated" in validation.values():
            handoff.status = HandoffStatus.PARTIAL
        else:
            handoff.status = HandoffStatus.UNVERIFIED

        # Persist updated status
        await self._persist_handoff_status(handoff)
        return handoff

    async def _persist_handoff_status(self, handoff: AgentHandoff) -> None:
        async def _impl(session: AsyncSession) -> None:
            stmt = select(AgentHandoffModel).where(
                AgentHandoffModel.handoff_id == handoff.handoff_id
            )
            result = await session.execute(stmt)
            model = result.scalar_one_or_none()
            if model is None:
                return
            model.status = handoff.status.value
            model.validation = handoff.validation or None
            await session.commit()

        await self._with_session(_impl)

    # ── Decisions ────────────────────────────────────────────────

    async def record_decision(
        self,
        run_id: str,
        decision_type: str,
        statement: str,
        made_by: str,
        evidence_refs: Optional[List[EvidenceRef]] = None,
    ) -> Optional[RunDecision]:
        """Record a lightweight engineering decision. Bounded."""
        existing = await self.list_decisions(run_id)
        if len(existing) >= MAX_DECISIONS_PER_RUN:
            logger.warning("Decision limit reached for run %s — skipping", run_id)
            return None

        from app.models.collaboration import DecisionType

        decision = RunDecision(
            run_id=run_id,
            decision_type=DecisionType(decision_type),
            statement=redact_secrets(statement or "")[:300],
            made_by=made_by,
            evidence_refs=(evidence_refs or [])[:8],
        )
        self._decisions.setdefault(run_id, []).append(decision)

        async def _impl(session: AsyncSession) -> None:
            session.add(RunDecisionModel(
                decision_id=decision.decision_id,
                run_id=run_id,
                decision_type=decision.decision_type.value,
                statement=decision.statement,
                made_by=made_by,
                evidence_refs=[e.model_dump() for e in decision.evidence_refs] or None,
            ))
            await session.commit()

        await self._with_session(_impl)
        return decision

    async def list_decisions(
        self, run_id: str, limit: int = 100, offset: int = 0
    ) -> List[RunDecision]:
        memory = self._decisions.get(run_id, [])
        if memory:
            ordered = sorted(memory, key=lambda d: d.created_at)
            return ordered[offset : offset + min(limit, MAX_DECISIONS_PER_RUN)]

        from app.models.collaboration import DecisionType

        async def _impl(session: AsyncSession) -> List[RunDecision]:
            stmt = (
                select(RunDecisionModel)
                .where(RunDecisionModel.run_id == run_id)
                .order_by(RunDecisionModel.created_at.asc())
                .offset(offset)
                .limit(min(limit, MAX_DECISIONS_PER_RUN))
            )
            result = await session.execute(stmt)
            return [
                RunDecision(
                    decision_id=m.decision_id,
                    run_id=m.run_id,
                    decision_type=DecisionType(m.decision_type),
                    statement=m.statement,
                    made_by=m.made_by,
                    evidence_refs=[EvidenceRef(**e) for e in (m.evidence_refs or [])],
                )
                for m in result.scalars().all()
            ]

        return await self._with_session(_impl, fallback=[])

    # ── Conflicts ────────────────────────────────────────────────

    async def detect_conflicts(
        self,
        run_id: str,
        handoff: AgentHandoff,
        test_passed: Optional[bool] = None,
    ) -> List[EvidenceConflict]:
        """Detect conflicts between agent claims and deterministic evidence.

        Deterministic evidence (test result) always outranks the claim.
        """
        conflicts: List[EvidenceConflict] = []
        claim_evidence = EvidenceRef(
            type=EvidenceType.AGENT_CLAIM,
            reference=handoff.handoff_id,
            detail=handoff.summary[:200],
            confidence=0.4,
        )

        claim_text = f"{handoff.summary} {' '.join(handoff.decisions)}".lower()
        claims_pass = any(w in claim_text for w in (
            "tests passed", "tests green", "tests succeeded", "test run passed",
            "passed", "succeeded", "success",
        ))
        if claims_pass and test_passed is False:
            deterministic_evidence = EvidenceRef(
                type=EvidenceType.TEST_RESULT,
                reference="failed",
                detail="Actual test result reported failures",
                confidence=1.0,
            )
            conflict = EvidenceConflict(
                run_id=run_id,
                description=(
                    f"{handoff.from_agent} claimed success but test evidence "
                    f"reports failure (handoff {handoff.handoff_id})"
                )[:300],
                claim_evidence=claim_evidence,
                deterministic_evidence=deterministic_evidence,
                resolution=ConflictResolution.DETERMINISTIC_WINS,
            )
            conflicts.append(conflict)
            # Downgrade the claim — deterministic evidence wins
            handoff.status = HandoffStatus.REJECTED
            handoff.validation["test:conflict"] = "rejected"

        if conflicts:
            # Persist the downgraded status (durable, survives restart)
            await self._persist_handoff_status(handoff)

        for conflict in conflicts:
            self._conflicts.setdefault(run_id, []).append(conflict)
            async def _impl(session: AsyncSession, c=conflict) -> None:
                session.add(EvidenceConflictModel(
                    conflict_id=c.conflict_id,
                    run_id=run_id,
                    description=c.description,
                    claim_evidence=c.claim_evidence.model_dump(),
                    deterministic_evidence=(
                        c.deterministic_evidence.model_dump()
                        if c.deterministic_evidence else None
                    ),
                    resolution=c.resolution.value,
                ))
                await session.commit()

            await self._with_session(_impl)

        return conflicts

    async def list_conflicts(
        self, run_id: str, limit: int = 50, offset: int = 0
    ) -> List[EvidenceConflict]:
        memory = self._conflicts.get(run_id, [])
        if memory:
            ordered = sorted(memory, key=lambda c: c.created_at)
            return ordered[offset : offset + min(limit, MAX_CONFLICTS_PER_RUN)]

        async def _impl(session: AsyncSession) -> List[EvidenceConflict]:
            stmt = (
                select(EvidenceConflictModel)
                .where(EvidenceConflictModel.run_id == run_id)
                .order_by(EvidenceConflictModel.created_at.asc())
                .offset(offset)
                .limit(min(limit, MAX_CONFLICTS_PER_RUN))
            )
            result = await session.execute(stmt)
            return [
                EvidenceConflict(
                    conflict_id=m.conflict_id,
                    run_id=m.run_id,
                    description=m.description,
                    claim_evidence=EvidenceRef(**m.claim_evidence) if m.claim_evidence else EvidenceRef(type=EvidenceType.AGENT_CLAIM),
                    deterministic_evidence=(
                        EvidenceRef(**m.deterministic_evidence)
                        if m.deterministic_evidence else None
                    ),
                    resolution=ConflictResolution(m.resolution),
                )
                for m in result.scalars().all()
            ]

        return await self._with_session(_impl, fallback=[])

    # ── Recovery / Resume (§15) ──────────────────────────────────

    async def recover(self, run_id: str) -> None:
        """Rehydrate persisted collaboration state after a restart."""
        factory = self._get_factory()
        if factory is None:
            return

        try:
            async with factory() as session:
                # Handoffs
                h_stmt = (
                    select(AgentHandoffModel)
                    .where(AgentHandoffModel.run_id == run_id)
                    .order_by(AgentHandoffModel.created_at.asc())
                )
                h_rows = (await session.execute(h_stmt)).scalars().all()
                self._handoffs[run_id] = [
                    AgentHandoff(
                        handoff_id=m.handoff_id,
                        run_id=m.run_id,
                        from_agent=m.from_agent,
                        to_agent=m.to_agent,
                        stage=m.stage,
                        summary=m.summary or "",
                        decisions=m.decisions or [],
                        evidence_refs=[EvidenceRef(**e) for e in (m.evidence_refs or [])],
                        artifact_refs=m.artifact_refs or [],
                        affected_symbols=m.affected_symbols or [],
                        warnings=m.warnings or [],
                        open_questions=m.open_questions or [],
                        status=HandoffStatus(m.status),
                        validation=m.validation or {},
                    )
                    for m in h_rows
                ]

                # Decisions
                from app.models.collaboration import DecisionType

                d_stmt = (
                    select(RunDecisionModel)
                    .where(RunDecisionModel.run_id == run_id)
                    .order_by(RunDecisionModel.created_at.asc())
                )
                d_rows = (await session.execute(d_stmt)).scalars().all()
                self._decisions[run_id] = [
                    RunDecision(
                        decision_id=m.decision_id,
                        run_id=m.run_id,
                        decision_type=DecisionType(m.decision_type),
                        statement=m.statement,
                        made_by=m.made_by,
                        evidence_refs=[EvidenceRef(**e) for e in (m.evidence_refs or [])],
                    )
                    for m in d_rows
                ]

                # Conflicts
                c_stmt = (
                    select(EvidenceConflictModel)
                    .where(EvidenceConflictModel.run_id == run_id)
                    .order_by(EvidenceConflictModel.created_at.asc())
                )
                c_rows = (await session.execute(c_stmt)).scalars().all()
                self._conflicts[run_id] = [
                    EvidenceConflict(
                        conflict_id=m.conflict_id,
                        run_id=m.run_id,
                        description=m.description,
                        claim_evidence=EvidenceRef(**m.claim_evidence) if m.claim_evidence else EvidenceRef(type=EvidenceType.AGENT_CLAIM),
                        deterministic_evidence=(
                            EvidenceRef(**m.deterministic_evidence)
                            if m.deterministic_evidence else None
                        ),
                        resolution=ConflictResolution(m.resolution),
                    )
                    for m in c_rows
                ]
        except Exception as exc:
            logger.debug("Collaboration recovery failed for %s: %s", run_id, exc)

    # ── Memory Promotion (§19) ───────────────────────────────────

    async def promote_memory(self, run: Any) -> int:
        """Promote verified collaboration knowledge to RepositoryMemory.

        Only at terminal run completion; only verified/approved knowledge.
        Never promote arbitrary agent claims or raw LLM output.
        """
        promoted = 0
        if run.status.value not in ("approved", "rejected"):
            return 0

        repo_id = None
        if run.repository_path:
            repo_id = run.repository_path.rstrip("/\\").split("/")[-1].split("\\")[-1]

        if not repo_id:
            return 0

        if self._memory_service is None:
            try:
                from app.services.repository_memory_service import RepositoryMemoryService
                self._memory_service = RepositoryMemoryService()
            except Exception:
                return 0

        from app.models.memory import (
            MemoryEvidence,
            MemoryStatus,
            MemoryType,
            RepositoryMemory,
        )

        changed_files = []
        if run.patch_set and run.patch_set.changes:
            changed_files = [c.path for c in run.patch_set.changes][:10]

        changed_symbols = [
            s for s in (getattr(run.patch_result, "changed_symbols", None) or [])
            if isinstance(s, str)
        ][:10]

        handoffs = await self.list_handoffs(run.run_id)

        # 1. Successful change memory (approved run with patch)
        if run.status.value == "approved" and changed_files:
            content = (
                f"Approved change in run {run.run_id} touched "
                f"{len(changed_files)} file(s): {', '.join(changed_files[:5])}"
            )
            try:
                await self._memory_service.create_memory(
                    RepositoryMemory(
                        memory_id=f"mem_{run.run_id[:8].lower()}_change",
                        repository_id=repo_id,
                        memory_type=MemoryType.SUCCESSFUL_CHANGE,
                        status=MemoryStatus.VERIFIED,
                        content=content,
                        confidence=0.85,
                        file_paths=changed_files,
                        symbol_names=changed_symbols,
                        evidence=[MemoryEvidence(
                            source_type="quality_gate",
                            source_id=run.run_id,
                            description="Approved by deterministic quality gate",
                        )],
                        source_run_id=run.run_id,
                        tags=["approved", "collaboration"],
                    )
                )
                promoted += 1
            except Exception as exc:
                logger.debug("Memory promotion (change) skipped: %s", exc)

        # 2. Known failed approach (from rejected handoff claims)
        for handoff in handoffs[:5]:
            if handoff.status == HandoffStatus.REJECTED and handoff.affected_symbols:
                try:
                    await self._memory_service.create_memory(
                        RepositoryMemory(
                            memory_id=f"mem_{run.run_id[:8].lower()}_{handoff.handoff_id[:6].lower()}",
                            repository_id=repo_id,
                            memory_type=MemoryType.FAILED_APPROACH,
                            status=MemoryStatus.PROVISIONAL,
                            content=(
                                f"Attempt on {', '.join(handoff.affected_symbols[:3])} "
                                f"was contradicted by test evidence in run {run.run_id}"
                            )[:500],
                            confidence=0.6,
                            symbol_names=handoff.affected_symbols[:10],
                            evidence=[MemoryEvidence(
                                source_type="test_result",
                                source_id=run.run_id,
                                description=handoff.summary[:200],
                            )],
                            source_run_id=run.run_id,
                            tags=["failed_approach", "collaboration"],
                        )
                    )
                    promoted += 1
                except Exception as exc:
                    logger.debug("Memory promotion (failed) skipped: %s", exc)

        return promoted

    # ── Metrics (§22) ────────────────────────────────────────────

    async def get_collaboration_metrics(self, run_id: str) -> Dict[str, Any]:
        handoffs = await self.list_handoffs(run_id)
        decisions = await self.list_decisions(run_id)
        conflicts = await self.list_conflicts(run_id)
        return {
            "run_id": run_id,
            "handoffs_total": len(handoffs),
            "handoffs_by_to_agent": _count_by(handoffs, lambda h: h.to_agent),
            "handoffs_validated": sum(1 for h in handoffs if h.status != HandoffStatus.UNVERIFIED),
            "decisions": len(decisions),
            "conflicts_detected": len(conflicts),
            "conflicts_resolved": sum(1 for c in conflicts if c.resolution != ConflictResolution.UNRESOLVED),
            "evidence_items": sum(len(h.evidence_refs) for h in handoffs),
        }


def _count_by(items: List[Any], key_fn) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for item in items:
        k = key_fn(item)
        out[k] = out.get(k, 0) + 1
    return out
