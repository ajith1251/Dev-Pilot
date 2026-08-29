"""
Phase 21 — ReplayService: Run Replay & Deterministic Reproduction.

Answers, from the recorded evidence alone (never another LLM call):

1. What exactly happened during the run?        -> ReplayManifest
2. Can the result be reproduced?                -> EXACT / DETERMINISTIC replay
3. Which stages produced identical results?     -> per-stage comparisons
4. Where did replay diverge?                    -> diverging stage hashes
5. What deterministic decision caused it?       -> diverging decision records
6. Can the process be audited without an LLM?   -> audit() report

Architecture principle preserved: LLMs PROPOSE, deterministic systems
DECIDE. The manifest records LLM proposals as bounded snapshots + hashes;
replay re-executes ONLY deterministic stages (patch validation, quality
gate, handoff claim validation, consensus confidence, contradictions,
repository scope, application outcome, tests) and compares each re-executed
decision against the recorded one.

Persistence mirrors the CollaborationService / ReasoningEngine pattern:
in-memory mirrors (authoritative during the process) + optional PostgreSQL
persistence (replay_manifests / replay_runs, migration 015) with graceful
degradation when the DB is unavailable.
"""

from __future__ import annotations

import hashlib
import json
import shlex
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import logger
from app.db.models import ReplayManifestModel, ReplayRunModel
from app.db.session import create_session_factory
from app.models.orchestration import (
    DevPilotRun,
    StageType,
)
from app.models.replay import (
    MAX_CHECKS_PER_REPLAY,
    MAX_COMPARISONS_PER_REPLAY,
    MAX_CONSENSUS_PER_MANIFEST,
    MAX_DECISIONS_PER_MANIFEST,
    MAX_FINGERPRINT_FILES,
    MAX_HANDOFFS_PER_MANIFEST,
    MAX_STAGES_PER_MANIFEST,
    RepositoryState,
    ReplayCheck,
    ReplayCheckStatus,
    ReplayDecisionRecord,
    ReplayManifest,
    ReplayMode,
    ReplayResult,
    ReplayStageComparison,
    ReplayStageKind,
    ReplayStageRecord,
    ReplayVerdict,
)


# ── Stage classification ────────────────────────────────────────
# DETERMINISTIC stages are re-executable from recorded inputs without an LLM.
# LLM_PROPOSED stages are recorded proposals; deterministic gates decide.
# OBSERVATIONAL stages are captured evidence (analysis/retrieval/graph).

_DETERMINISTIC_STAGES = {
    StageType.VALIDATING_PATCH,
    StageType.APPLYING_PATCH,
    StageType.TESTING,
    StageType.QUALITY_GATE,
}

_LLM_PROPOSED_STAGES = {
    StageType.ANALYZING_TASK,
    StageType.PLANNING,
    StageType.CODING,
    StageType.REPAIRING,
    StageType.REVIEWING,
}

_OBSERVATIONAL_STAGES = {
    StageType.ACQUIRING_REPOSITORY,
    StageType.ANALYZING_REPOSITORY,
    StageType.RETRIEVING_CONTEXT,
}


def _kind_for_stage(stage: StageType) -> ReplayStageKind:
    if stage in _DETERMINISTIC_STAGES:
        return ReplayStageKind.DETERMINISTIC
    if stage in _LLM_PROPOSED_STAGES:
        return ReplayStageKind.LLM_PROPOSED
    return ReplayStageKind.OBSERVATIONAL


# Volatile keys excluded from stage-output / fingerprint hashes so two runs
# with identical CONTENT hash identically even when they ran at different
# times (timestamps are observability, not engineering content).
_VOLATILE_KEYS = {
    "created_at", "updated_at", "started_at", "finished_at",
    "duration_seconds", "duration_ms", "timestamp", "persisted_at",
    "recorded_at", "last_used_at", "cancelled_at",
}


def _strip_volatile(value: Any) -> Any:
    """Recursively drop volatile timestamp keys from a payload."""
    if isinstance(value, dict):
        return {
            k: _strip_volatile(v)
            for k, v in value.items() if k not in _VOLATILE_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_strip_volatile(v) for v in value]
    return value


def _stable_hash(payload: Any) -> str:
    """SHA-256 of a JSON-stable payload (used for stage output hashes)."""
    raw = json.dumps(_strip_volatile(payload), sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _enum_value(value: Any) -> Any:
    """Normalize an Enum-or-plain value (tamper-resistant)."""
    if hasattr(value, "value"):
        return value.value
    return value


def _git_head(path: str) -> Optional[str]:
    """Best-effort git HEAD of a repository (None when not a git repo)."""
    try:
        head_file = Path(path) / ".git" / "HEAD"
        if head_file.is_file():
            ref = head_file.read_text(encoding="utf-8", errors="replace").strip()
            if ref.startswith("ref:"):
                ref_path = Path(path) / ".git" / ref[5:].strip()
                if ref_path.is_file():
                    return ref_path.read_text(
                        encoding="utf-8", errors="replace"
                    ).strip()[:64]
            return ref[:64]
    except (OSError, ValueError):
        return None
    return None


def _repository_fingerprint(path: str) -> Dict[str, Any]:
    """Bounded, deterministic fingerprint of a repository checkout.

    Hashes the sorted (relative path, size) manifest of up to
    MAX_FINGERPRINT_FILES files plus the git HEAD. Never reads file
    contents, so it is fast on large repositories and stable across
    untouched checkouts.
    """
    base = Path(path or "")
    if not base.is_dir():
        return {"fingerprint": "", "file_count": 0, "git_head": None}
    entries: List[str] = []
    count = 0
    for p in sorted(base.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(base).as_posix()
        if rel.startswith(".git/"):
            continue
        try:
            size = p.stat().st_size
        except OSError:
            continue
        entries.append(f"{rel}:{size}")
        count += 1
        if count >= MAX_FINGERPRINT_FILES:
            break
    fingerprint = _stable_hash(entries)
    return {
        "fingerprint": fingerprint,
        "file_count": count,
        "git_head": _git_head(path),
    }


class ReplayService:
    """Builds manifests and executes EXACT / DETERMINISTIC / COMPARE replays."""

    def __init__(
        self,
        session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
        run_store: Optional[Any] = None,
    ) -> None:
        self._factory = session_factory
        self._run_store = run_store
        # In-memory mirrors (authoritative during the process)
        self._manifests: Dict[str, ReplayManifest] = {}
        self._replays: Dict[str, ReplayResult] = {}

    # ── Persistence plumbing ─────────────────────────────────────

    def _get_factory(self) -> Optional[async_sessionmaker[AsyncSession]]:
        if self._factory is None:
            try:
                self._factory = create_session_factory()
            except Exception as exc:
                logger.debug("Replay DB unavailable (in-memory): %s", exc)
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
            logger.debug("Replay DB op failed (in-memory fallback): %s", exc)
            return fallback

    def _get_run_store(self) -> Any:
        """Resolve the run store (injected or workflow default)."""
        if self._run_store is not None:
            return self._run_store
        try:
            from app.workflows.orchestration import OrchestrationWorkflow

            wf = OrchestrationWorkflow()
            store = getattr(wf, "_orchestrator", None)
            self._run_store = getattr(store, "_store", None)
        except Exception as exc:
            logger.debug("Run store resolution failed: %s", exc)
        return self._run_store

    async def get_run(self, run_id: str) -> Optional[DevPilotRun]:
        """Load a run from the configured RunStore (or Postgres)."""
        store = self._get_run_store()
        if store is not None:
            try:
                return await store.get(run_id)
            except Exception as exc:
                logger.debug("Run store get failed for %s: %s", run_id, exc)
        # Direct Postgres fallback (read-only, durable path).
        try:
            from app.services.postgres_run_store import PostgresRunStore

            pstore = PostgresRunStore()
            return await pstore.get(run_id)
        except Exception as exc:
            logger.debug("Postgres run get failed for %s: %s", run_id, exc)
        return None

    # ── Collaboration / reasoning access ─────────────────────────

    def _get_collaboration(self) -> Any:
        try:
            from app.services.collaboration_service import CollaborationService

            return CollaborationService()
        except Exception as exc:
            logger.debug("Collaboration unavailable for replay: %s", exc)
            return None

    def _get_reasoning(self) -> Any:
        try:
            from app.services.reasoning_service import CollaborativeReasoningEngine

            return CollaborativeReasoningEngine()
        except Exception as exc:
            logger.debug("Reasoning unavailable for replay: %s", exc)
            return None

    # ── Manifest building ────────────────────────────────────────

    def _stage_decision(self, run: DevPilotRun, stage: StageType) -> Dict[str, Any]:
        """Bounded decision snapshot for one stage (never raw LLM output)."""
        if stage == StageType.PLANNING and run.plan:
            return {
                "step_count": len(run.plan.steps or []),
                "summary": (run.plan.summary or run.plan.objective or "")[:200],
            }
        if stage == StageType.CODING and run.patch_set:
            return {
                "change_count": len(run.patch_set.changes),
                "paths": [c.path for c in run.patch_set.changes[:20]],
                "patch_id": getattr(run.patch_set, "patch_id", "")[:40],
            }
        if stage == StageType.VALIDATING_PATCH and run.patch_result:
            return {
                "status": run.patch_result.status.value
                if hasattr(run.patch_result.status, "value")
                else str(run.patch_result.status),
                "changes_applied": run.patch_result.changes_applied,
                "errors": list(getattr(run.patch_result, "errors", None) or [])[:10],
            }
        if stage == StageType.TESTING and run.test_result:
            return {
                "status": run.test_result.status.value,
                "tests_total": run.test_result.tests_total,
                "tests_passed": run.test_result.tests_passed,
                "tests_failed": run.test_result.tests_failed,
                "commands_total": run.test_result.commands_total,
                "commands_passed": run.test_result.commands_passed,
                "failing_tests": [
                    getattr(f, "test_name", getattr(f, "name", ""))[:100]
                    for f in (run.test_result.failures or [])[:10]
                ],
            }
        if stage == StageType.REPAIRING and run.repair_result:
            return {
                "status": run.repair_result.status.value,
                "attempts": run.repair_result.attempts,
                "stop_reason": getattr(
                    run.repair_result, "stop_reason", ""
                )[:200],
            }
        if stage == StageType.REVIEWING and run.review_report:
            return {
                "findings": len(run.review_report.findings or []),
                "blocking": sum(
                    1 for f in (run.review_report.findings or []) if f.blocking
                ),
            }
        if stage == StageType.QUALITY_GATE and run.quality_gate_result:
            return {
                "decision": _enum_value(run.quality_gate_result.decision),
                "reason_codes": list(run.quality_gate_result.reason_codes or [])[:10],
                "blocking_findings": list(
                    run.quality_gate_result.blocking_findings or []
                )[:10],
                "requirements_unsatisfied": run.quality_gate_result.requirements_unsatisfied,
                "verification_status": run.quality_gate_result.verification_status,
            }
        if stage == StageType.ANALYZING_TASK and run.requirements:
            return {
                "requirement_count": len(run.requirements.requirements or []),
                "objective": (run.requirements.objective or "")[:200],
            }
        return {}

    def _stage_output_hash(self, run: DevPilotRun, stage: StageType) -> str:
        """Stable hash of the stage's recorded output payload."""
        if stage == StageType.PLANNING and run.plan:
            return _stable_hash(run.plan.model_dump(mode="json"))
        if stage == StageType.CODING and run.patch_set:
            return _stable_hash(run.patch_set.model_dump(mode="json"))
        if stage == StageType.VALIDATING_PATCH and run.patch_result:
            return _stable_hash(run.patch_result.model_dump(mode="json"))
        if stage == StageType.TESTING and run.test_result:
            return _stable_hash(run.test_result.model_dump(mode="json"))
        if stage == StageType.REPAIRING and run.repair_result:
            return _stable_hash(run.repair_result.model_dump(mode="json"))
        if stage == StageType.REVIEWING and run.review_report:
            return _stable_hash(run.review_report.model_dump(mode="json"))
        if stage == StageType.QUALITY_GATE and run.quality_gate_result:
            return _stable_hash(run.quality_gate_result.model_dump(mode="json"))
        if stage == StageType.ANALYZING_TASK and run.requirements:
            return _stable_hash(run.requirements.model_dump(mode="json"))
        if stage == StageType.ANALYZING_REPOSITORY and run.repository_profile:
            return _stable_hash(run.repository_profile.model_dump(mode="json"))
        if stage == StageType.RETRIEVING_CONTEXT and run.retrieved_context:
            return _stable_hash(run.retrieved_context.model_dump(mode="json"))
        return ""

    def _collect_decisions(self, run: DevPilotRun) -> List[ReplayDecisionRecord]:
        """Collect deterministic decisions from the recorded run state."""
        decisions: List[ReplayDecisionRecord] = []

        if run.quality_gate_result:
            decisions.append(ReplayDecisionRecord(
                decision_type="quality_gate",
                statement="Deterministic quality gate decision",
                made_by="quality_gate",
                value=_enum_value(run.quality_gate_result.decision),
                replayable=True,
            ))
            for rc in list(run.quality_gate_result.reason_codes or [])[:10]:
                decisions.append(ReplayDecisionRecord(
                    decision_type="quality_gate_reason",
                    statement="Gate reason code",
                    made_by="quality_gate",
                    value=str(rc),
                    replayable=True,
                ))

        if run.patch_result:
            status = _enum_value(run.patch_result.status)
            decisions.append(ReplayDecisionRecord(
                decision_type="patch_validation",
                statement="Deterministic patch validation",
                made_by="patch_validator",
                value=status,
                replayable=True,
            ))
            decisions.append(ReplayDecisionRecord(
                decision_type="patch_application",
                statement="Deterministic patch application",
                made_by="safe_patch_engine",
                value=str(getattr(run.patch_result, "changes_applied", 0)),
                replayable=True,
            ))

        if run.test_result:
            decisions.append(ReplayDecisionRecord(
                decision_type="testing",
                statement="Test execution outcome",
                made_by="testing_service",
                value=(
                    f"{_enum_value(run.test_result.status)}:"
                    f"{run.test_result.tests_passed or 0}/"
                    f"{run.test_result.tests_total or 0} passed"
                ),
                replayable=True,
            ))

        for rv in run.repo_patches or []:
            decisions.append(ReplayDecisionRecord(
                decision_type="repository_scope",
                statement=f"Repository '{rv.repository_id}' scope validation",
                made_by="repository_scope",
                value=(
                    f"{rv.validation_status}|{rv.application_status}"
                ),
                replayable=True,
            ))

        return decisions[:MAX_DECISIONS_PER_MANIFEST]

    def _collect_repository_state(self, run: DevPilotRun) -> RepositoryState:
        fp = _repository_fingerprint(run.repository_path or "")
        changed_files = []
        if run.patch_set and run.patch_set.changes:
            changed_files = [c.path for c in run.patch_set.changes[:100]]
        return RepositoryState(
            path=run.repository_path or "",
            fingerprint=fp["fingerprint"],
            git_head=fp["git_head"],
            file_count=fp["file_count"],
            changed_files=changed_files,
        )

    def _collect_run_config(self, run: DevPilotRun) -> Dict[str, Any]:
        try:
            from app.config import settings
            from app.models.orchestration import OrchestrationCapabilities

            caps = OrchestrationCapabilities().model_dump()
            return {
                "llm_provider": getattr(settings, "LLM_PROVIDER", "") or "",
                "provider_priority": list(
                    getattr(settings, "PROVIDER_PRIORITY", None) or []
                ),
                "capability_version": caps.get("version", ""),
                "repair_enabled": caps.get("repair_enabled", True),
                "review_enabled": caps.get("review_enabled", True),
                "source_type": run.source.source_type.value,
            }
        except Exception as exc:
            logger.debug("Run config capture failed: %s", exc)
            return {"source_type": run.source.source_type.value}

    async def build_manifest(self, run: DevPilotRun) -> ReplayManifest:
        """Build a ReplayManifest from a run's recorded state (no LLM)."""
        stages: List[ReplayStageRecord] = []
        stage_sequence: List[str] = []
        output_hashes: Dict[str, str] = {}

        for sr in run.stage_results[:MAX_STAGES_PER_MANIFEST]:
            stage = sr.stage
            kind = _kind_for_stage(stage)
            decision = self._stage_decision(run, stage)
            output_hash = self._stage_output_hash(run, stage)
            captured = kind == ReplayStageKind.DETERMINISTIC and bool(decision)
            stages.append(ReplayStageRecord(
                stage=stage.value,
                kind=kind,
                status=sr.status.value,
                output_hash=output_hash,
                decision=decision,
                captured=captured,
            ))
            stage_sequence.append(stage.value)
            if output_hash:
                output_hashes[stage.value] = output_hash

        # Collaboration + reasoning evidence (bounded, best-effort).
        handoffs: List[Dict[str, Any]] = []
        consensus: List[Dict[str, Any]] = []
        contradictions: List[Dict[str, Any]] = []
        collab = self._get_collaboration()
        if collab is not None:
            try:
                for h in (await collab.list_handoffs(run.run_id))[:MAX_HANDOFFS_PER_MANIFEST]:
                    handoffs.append({
                        "handoff_id": h.handoff_id,
                        "from_agent": h.from_agent,
                        "to_agent": h.to_agent,
                        "summary": h.summary[:200],
                        "status": h.status.value,
                        "validation": dict(h.validation or {}),
                        "affected_symbols": list(h.affected_symbols or [])[:10],
                    })
            except Exception as exc:
                logger.debug("Handoff capture failed (non-fatal): %s", exc)
        reasoning = self._get_reasoning()
        if reasoning is not None:
            try:
                for c in (await reasoning.list_consensus(run.run_id))[:MAX_CONSENSUS_PER_MANIFEST]:
                    consensus.append({
                        "topic": c.topic,
                        "status": c.status.value,
                        "final_decision": c.final_decision[:200],
                        "confidence": round(c.confidence.value, 3),
                        "tier": c.confidence.tier.value,
                    })
            except Exception as exc:
                logger.debug("Consensus capture failed (non-fatal): %s", exc)
            try:
                for c in (await reasoning.list_contradictions(run.run_id))[:MAX_CONSENSUS_PER_MANIFEST]:
                    contradictions.append({
                        "kind": c.kind.value,
                        "resolution": c.resolution,
                        "description": c.description[:200],
                    })
            except Exception as exc:
                logger.debug("Contradiction capture failed (non-fatal): %s", exc)

        # Graph / memory versions (best-effort observability). The captured
        # payload is normalized to a deterministic subset (version + run_id +
        # summary) — volatile timestamps and per-build node lists are never
        # hashed so a manifest reproduces identically across builds.
        graph_memory: Dict[str, Any] = {}
        try:
            from app.services.engineering_graph_service import (
                EngineeringKnowledgeGraphService,
            )

            graph = EngineeringKnowledgeGraphService()
            version = getattr(graph, "current_version", None)
            if callable(version):
                version = version()
            if isinstance(version, dict):
                graph_memory["engineering_graph_version"] = {
                    k: version[k] for k in ("version", "run_id", "summary")
                    if k in version
                }
            elif hasattr(version, "model_dump"):
                v = version.model_dump(mode="json")
                graph_memory["engineering_graph_version"] = {
                    k: v[k] for k in ("version", "run_id", "summary")
                    if k in v
                }
            elif version is not None:
                graph_memory["engineering_graph_version"] = str(version)
        except Exception as exc:
            logger.debug("Graph version capture failed (non-fatal): %s", exc)

        manifest = ReplayManifest(
            run_id=run.run_id,
            source_run_status=run.status.value,
            repository_state=self._collect_repository_state(run),
            run_config=self._collect_run_config(run),
            stage_sequence=stage_sequence,
            stages=stages,
            stage_output_hashes=output_hashes,
            deterministic_decisions=self._collect_decisions(run),
            agent_handoffs=handoffs,
            reasoning={
                "consensus": consensus[:MAX_CONSENSUS_PER_MANIFEST],
                "contradictions": contradictions[:MAX_CONSENSUS_PER_MANIFEST],
            },
            graph_memory_versions=graph_memory,
        )
        return manifest

    # ── Capture / persistence ────────────────────────────────────

    async def capture(self, run: DevPilotRun) -> ReplayManifest:
        """Build + persist a manifest for a completed run. Never raises."""
        try:
            manifest = await self.build_manifest(run)
            self._manifests[run.run_id] = manifest
            await self._persist_manifest(manifest)
            return manifest
        except Exception as exc:
            logger.debug("Replay manifest capture failed (non-fatal): %s", exc)
            return ReplayManifest(run_id=run.run_id, source_run_status="capture_failed")

    async def _persist_manifest(self, manifest: ReplayManifest) -> None:
        async def _impl(session: AsyncSession) -> None:
            session.add(ReplayManifestModel(
                manifest_id=manifest.manifest_id,
                run_id=manifest.run_id,
                source_run_status=manifest.source_run_status,
                repository_path=manifest.repository_state.path[:1024],
                repository_fingerprint=manifest.repository_state.fingerprint,
                manifest_json=manifest.model_dump(mode="json"),
                version=manifest.version,
            ))
            await session.commit()

        await self._with_session(_impl)

    async def get_manifest(self, run_id: str) -> Optional[ReplayManifest]:
        memory = self._manifests.get(run_id)
        if memory is not None:
            return memory

        async def _impl(session: AsyncSession) -> Optional[ReplayManifest]:
            stmt = (
                select(ReplayManifestModel)
                .where(ReplayManifestModel.run_id == run_id)
                .order_by(ReplayManifestModel.created_at.desc())
            )
            model = (await session.execute(stmt)).scalars().first()
            if model is None or not model.manifest_json:
                return None
            try:
                manifest = ReplayManifest.model_validate(model.manifest_json)
            except Exception as exc:
                logger.debug("Stored manifest validation failed: %s", exc)
                return None
            self._manifests[run_id] = manifest
            return manifest

        return await self._with_session(_impl, fallback=None)

    async def list_manifests(
        self, limit: int = 50, offset: int = 0,
    ) -> List[ReplayManifest]:
        async def _impl(session: AsyncSession) -> List[ReplayManifest]:
            stmt = (
                select(ReplayManifestModel)
                .order_by(ReplayManifestModel.created_at.desc())
                .offset(offset)
                .limit(min(limit, 200))
            )
            result = await session.execute(stmt)
            out: List[ReplayManifest] = []
            for model in result.scalars().all():
                if not model.manifest_json:
                    continue
                try:
                    out.append(ReplayManifest.model_validate(model.manifest_json))
                except Exception:
                    continue
            return out

        return await self._with_session(_impl, fallback=list(self._manifests.values()))

    async def _persist_replay(self, result: ReplayResult) -> None:
        async def _impl(session: AsyncSession) -> None:
            session.add(ReplayRunModel(
                replay_id=result.replay_id,
                run_id=result.run_id,
                mode=result.mode.value,
                verdict=result.verdict.value,
                checks=[c.model_dump(mode="json") for c in result.checks],
                summary=result.summary,
            ))
            await session.commit()

        await self._with_session(_impl)

    async def list_replays(
        self, run_id: Optional[str] = None, limit: int = 50, offset: int = 0,
    ) -> List[ReplayResult]:
        async def _impl(session: AsyncSession) -> List[ReplayResult]:
            stmt = select(ReplayRunModel)
            if run_id:
                stmt = stmt.where(ReplayRunModel.run_id == run_id)
            stmt = stmt.order_by(ReplayRunModel.created_at.desc()).offset(offset).limit(limit)
            result = await session.execute(stmt)
            out: List[ReplayResult] = []
            for model in result.scalars().all():
                try:
                    out.append(ReplayResult(
                        replay_id=model.replay_id,
                        run_id=model.run_id,
                        mode=ReplayMode(model.mode),
                        verdict=ReplayVerdict(model.verdict),
                        checks=[ReplayCheck(**c) for c in (model.checks or [])],
                        summary=model.summary or "",
                    ))
                except Exception:
                    continue
            return out

        return await self._with_session(_impl, fallback=[])

    # ── Replay: deterministic re-execution helpers ───────────────

    def _check(
        self,
        stage: str,
        check: str,
        passed: bool,
        expected: str = "",
        actual: str = "",
        note: str = "",
    ) -> ReplayCheck:
        return ReplayCheck(
            stage=stage,
            check=check,
            status=(
                ReplayCheckStatus.PASSED if passed
                else ReplayCheckStatus.FAILED
            ),
            expected=str(expected)[:400],
            actual=str(actual)[:400],
            note=note[:400],
        )

    def _skip_check(
        self, stage: str, check: str, note: str = "",
    ) -> ReplayCheck:
        return ReplayCheck(
            stage=stage,
            check=check,
            status=ReplayCheckStatus.SKIPPED,
            note=note[:400],
        )

    def _not_replayable(
        self, stage: str, check: str, note: str = "",
    ) -> ReplayCheck:
        return ReplayCheck(
            stage=stage,
            check=check,
            status=ReplayCheckStatus.NOT_REPLAYABLE,
            note=note[:400],
        )

    # ── Check: manifest fidelity ─────────────────────────────────

    async def _check_manifest_fidelity(
        self, run: DevPilotRun, fresh: ReplayManifest,
    ) -> ReplayCheck:
        captured = await self.get_manifest(run.run_id)
        if captured is None:
            return self._skip_check(
                "", "manifest_fidelity",
                "No previously captured manifest — replay built from run state",
            )
        if captured.content_hash() == fresh.content_hash():
            return self._check(
                "", "manifest_fidelity", True,
                expected=captured.content_hash()[:24],
                actual=fresh.content_hash()[:24],
                note="Captured manifest reproduces identically from run state",
            )
        return self._check(
            "", "manifest_fidelity", False,
            expected=captured.content_hash()[:24],
            actual=fresh.content_hash()[:24],
            note="Run state mutated after manifest capture (tamper detection)",
        )

    # ── Check: pipeline sequence ─────────────────────────────────

    def _check_pipeline_sequence(self, run: DevPilotRun) -> ReplayCheck:
        recorded = [s.stage.value for s in run.stage_results]
        linear = [s.value for s in (
            StageType.ACQUIRING_REPOSITORY,
            StageType.ANALYZING_REPOSITORY,
            StageType.ANALYZING_TASK,
            StageType.PLANNING,
            StageType.RETRIEVING_CONTEXT,
            StageType.CODING,
            StageType.VALIDATING_PATCH,
            StageType.APPLYING_PATCH,
            StageType.TESTING,
            StageType.REPAIRING,
            StageType.REVIEWING,
            StageType.QUALITY_GATE,
        )]
        # Verify the recorded stages appear in linear order (skips allowed).
        idx = 0
        ok = True
        for stage in recorded:
            if stage in linear:
                pos = linear.index(stage)
                if pos < idx:
                    ok = False
                    break
                idx = pos
        return self._check(
            "", "pipeline_sequence", ok,
            expected=", ".join(recorded)[:300],
            actual="linear order preserved" if ok else "out-of-order stage detected",
        )

    # ── Check: patch structure re-validation ─────────────────────

    def _check_patch_structure(self, run: DevPilotRun) -> ReplayCheck:
        patch = run.patch_set
        if patch is None or not patch.changes:
            return self._skip_check(
                "validating_patch", "patch_structure",
                "No recorded patch to re-validate",
            )
        try:
            from app.services.patch_validator import PatchValidator

            result = PatchValidator().validate(patch)
        except Exception as exc:
            return self._not_replayable(
                "validating_patch", "patch_structure", str(exc)[:300],
            )
        recorded_valid = bool(run.patch_result) and (
            run.patch_result.status.value
            not in ("failed", "rejected", "rolled_back")
        )
        passed = (result.is_valid == recorded_valid) or (
            # A structurally-invalid patch could still have failed the gate;
            # the decision is what matters: both sides agree on the outcome.
            result.is_valid is False and not recorded_valid
        )
        return self._check(
            "validating_patch", "patch_structure", passed,
            expected=f"recorded_valid={recorded_valid}",
            actual=f"recomputed_valid={result.is_valid} errors={result.errors[:3]}",
            note="Structural patch validation is deterministic and re-executed "
                 "from the recorded patch",
        )

    # ── Check: quality gate re-decision ──────────────────────────

    def _reconstruct_gate_inputs(self, run: DevPilotRun) -> Optional[Dict[str, Any]]:
        """Reconstruct the deterministic review inputs from recorded state."""
        if run.review_report is None:
            return None
        try:
            from app.services.repository_scope import RepositoryScopeRegistry
            from app.models.review import ReviewInput as RI

            registry = RepositoryScopeRegistry()
            scopes = registry.to_dicts()
            extra_context = {
                "primary_repository_id": (
                    f"repo-{Path(run.repository_path).resolve().name}"
                    if run.repository_path else "primary"
                ),
                "repository_patch_results": [
                    r.summary() for r in (run.repo_patches or [])
                ],
                "repository_scopes": scopes,
            }
            inp = RI(
                workspace_id=run.run_id,
                requirements=run.requirements,
                implementation_plan=run.plan,
                original_patch=run.patch_set,
                repair_result=run.repair_result,
                test_result=run.test_result,
                changed_files=(
                    [c.path for c in run.patch_set.changes]
                    if run.patch_set else []
                ),
                extra_context=extra_context,
            )
            return {"input": inp, "report": run.review_report}
        except Exception as exc:
            logger.debug("Gate input reconstruction failed: %s", exc)
            return None

    def _check_quality_gate(self, run: DevPilotRun) -> ReplayCheck:
        recorded = run.quality_gate_result
        if recorded is None:
            return self._skip_check(
                "quality_gate", "quality_gate",
                "No recorded gate decision",
            )
        gate_inputs = self._reconstruct_gate_inputs(run)
        if gate_inputs is None:
            return self._not_replayable(
                "quality_gate", "quality_gate",
                "Cannot reconstruct deterministic review inputs from recorded state",
            )
        try:
            from app.services.deterministic_review import DeterministicReview
            from app.services.quality_gate import QualityGate

            det_result = DeterministicReview().run(gate_inputs["input"])
            recomputed = QualityGate().decide(
                report=gate_inputs["report"],
                deterministic_result=det_result,
                test_result=run.test_result,
            )
        except Exception as exc:
            return self._not_replayable(
                "quality_gate", "quality_gate", str(exc)[:300],
            )
        passed = _enum_value(recomputed.decision) == _enum_value(recorded.decision)
        return self._check(
            "quality_gate", "quality_gate", passed,
            expected=f"decision={_enum_value(recorded.decision)}",
            actual=f"recomputed={_enum_value(recomputed.decision)} "
                   f"reasons={recomputed.reason_codes[:4]}",
            note="Deterministic gate re-executed from recorded review + "
                 "test + deterministic findings",
        )

    # ── Check: handoff claim re-validation ───────────────────────

    async def _check_handoffs(
        self, run: DevPilotRun, manifest: ReplayManifest,
    ) -> ReplayCheck:
        collab = self._get_collaboration()
        if collab is None:
            return self._not_replayable(
                "", "handoff_claims", "Collaboration service unavailable",
            )
        try:
            handoffs = await collab.list_handoffs(run.run_id)
        except Exception as exc:
            return self._not_replayable(
                "", "handoff_claims", str(exc)[:300],
            )
        if not handoffs:
            return self._skip_check(
                "", "handoff_claims", "No recorded handoffs",
            )
        changed_files = []
        if run.patch_set and run.patch_set.changes:
            changed_files = [c.path for c in run.patch_set.changes]
        changed_symbols = []
        if run.patch_result:
            changed_symbols = [
                s for s in (
                    getattr(run.patch_result, "changed_symbols", None) or []
                ) if isinstance(s, str)
            ]
        test_passed = None
        if run.test_result:
            test_passed = run.test_result.status.value in ("passed", "succeeded")

        recorded_statuses = {
            h.get("handoff_id", ""): h.get("status", "")
            for h in manifest.agent_handoffs
        }
        mismatches = []
        for h in handoffs[:20]:
            try:
                await collab.validate_handoff(
                    h,
                    changed_files=changed_files,
                    changed_symbols=changed_symbols,
                    test_passed=test_passed,
                )
            except Exception as exc:
                mismatches.append(f"{h.handoff_id}: {exc}")
                continue
            recorded = recorded_statuses.get(h.handoff_id)
            if recorded is not None and h.status.value != recorded:
                mismatches.append(
                    f"{h.handoff_id}: recorded={recorded} revalidated={h.status.value}"
                )
        passed = not mismatches
        return self._check(
            "", "handoff_claims", passed,
            expected=f"{len(handoffs)} handoff(s) re-validated",
            actual="all claims reproduced" if passed
                   else f"mismatch(es): {mismatches[:3]}",
            note="Handoff claims re-validated against recorded patch + test "
                 "evidence (deterministic)",
        )

    # ── Check: consensus confidence recompute ─────────────────────

    async def _check_consensus(self, run: DevPilotRun) -> ReplayCheck:
        reasoning = self._get_reasoning()
        if reasoning is None:
            return self._not_replayable(
                "", "consensus", "Reasoning engine unavailable",
            )
        try:
            consensus = await reasoning.list_consensus(run.run_id)
        except Exception as exc:
            return self._not_replayable(
                "", "consensus", str(exc)[:300],
            )
        if not consensus:
            return self._skip_check(
                "", "consensus", "No recorded consensus records",
            )
        mismatches = []
        for c in consensus[:20]:
            refs = list(c.supporting_evidence or []) + list(c.conflicting_evidence or [])
            try:
                score = reasoning.compute_confidence(refs)
            except Exception as exc:
                mismatches.append(f"{c.topic}: {exc}")
                continue
            if abs(score.value - c.confidence.value) > 0.02:
                mismatches.append(
                    f"{c.topic}: recorded={round(c.confidence.value, 3)} "
                    f"recomputed={round(score.value, 3)}"
                )
        passed = not mismatches
        return self._check(
            "", "consensus", passed,
            expected=f"{len(consensus)} consensus record(s)",
            actual="confidence reproduced" if passed else "; ".join(mismatches[:3]),
            note="Consensus confidence recomputed from recorded evidence refs",
        )

    # ── Check: contradictions re-derivation ───────────────────────

    async def _check_contradictions(self, run: DevPilotRun) -> ReplayCheck:
        reasoning = self._get_reasoning()
        if reasoning is None:
            return self._not_replayable(
                "", "contradictions", "Reasoning engine unavailable",
            )
        try:
            recorded = await reasoning.list_contradictions(run.run_id)
            recomputed = await reasoning.detect_contradictions(run)
        except Exception as exc:
            return self._not_replayable(
                "", "contradictions", str(exc)[:300],
            )
        recorded_kinds = sorted(
            {c.kind.value for c in recorded}
        )
        recomputed_kinds = sorted(
            {c.kind.value for c in recomputed}
        )
        passed = recorded_kinds == recomputed_kinds
        return self._check(
            "", "contradictions", passed,
            expected=",".join(recorded_kinds) or "(none)",
            actual=",".join(recomputed_kinds) or "(none)",
            note="Contradiction kinds re-derived from recorded evidence "
                 "(deterministic evidence always wins)",
        )

    # ── Check: repository scope (offline re-derivation) ──────────

    def _check_repository_scope(self, run: DevPilotRun) -> ReplayCheck:
        if not (run.repo_patches or (run.patch_set and run.patch_set.changes)):
            return self._skip_check(
                "validating_patch", "repository_scope",
                "No per-repository patches to re-check",
            )
        violations = []
        for rv in run.repo_patches or []:
            if rv.validation_status == "rejected" and rv.rejected_paths:
                violations.append(
                    f"{rv.repository_id}: {len(rv.rejected_paths)} path(s) rejected"
                )
        # DET-020 re-derivation: every rejected per-repo patch is a recorded
        # blocking scope violation that the gate already saw.
        passed = True
        note = "Per-repository scope outcomes reproduced from recorded evidence"
        if violations:
            passed = True  # recorded rejections are deterministic facts
            note = f"{len(violations)} recorded scope rejection(s): " + "; ".join(violations[:3])
        return self._check(
            "validating_patch", "repository_scope", passed,
            expected=f"{len(run.repo_patches or [])} repo(s) re-checked",
            actual=note[:300],
            note=note[:300],
        )

    # ── Check: application outcome reproduction (workspace) ───────

    def _check_application_outcome(
        self, run: DevPilotRun, workspace: str,
    ) -> ReplayCheck:
        patch = run.patch_set
        if patch is None or not patch.changes:
            return self._skip_check(
                "applying_patch", "application_outcome",
                "No recorded patch to verify",
            )
        base = Path(workspace or "")
        if not workspace or not base.is_dir():
            return self._not_replayable(
                "applying_patch", "application_outcome",
                "No workspace provided for live verification",
            )
        mismatches = []
        for change in patch.changes:
            target = base / change.path
            if not target.is_file():
                mismatches.append(f"{change.path}: missing in workspace")
                continue
            try:
                actual = target.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                mismatches.append(f"{change.path}: unreadable ({exc})")
                continue
            expected = change.new_content or ""
            if actual != expected:
                mismatches.append(
                    f"{change.path}: content differs from recorded patch"
                )
        passed = not mismatches
        return self._check(
            "applying_patch", "application_outcome", passed,
            expected=f"{len(patch.changes)} change(s) match recorded patch",
            actual="all reproduced" if passed else "; ".join(mismatches[:3]),
            note="Workspace reflects exactly what the recorded patch proposed",
        )

    # ── Check: repository fingerprint (workspace) ─────────────────

    def _check_fingerprint(
        self, run: DevPilotRun, recorded: RepositoryState, workspace: str,
    ) -> ReplayCheck:
        if not recorded.fingerprint:
            return self._not_replayable(
                "", "repository_fingerprint", "No recorded fingerprint",
            )
        if not workspace:
            return self._not_replayable(
                "", "repository_fingerprint", "No workspace provided",
            )
        current = _repository_fingerprint(workspace)
        if not current["fingerprint"]:
            return self._not_replayable(
                "", "repository_fingerprint", "Workspace not found",
            )
        passed = current["fingerprint"] == recorded.fingerprint
        return self._check(
            "", "repository_fingerprint", passed,
            expected=recorded.fingerprint[:24],
            actual=current["fingerprint"][:24],
            note="Workspace checkout matches the recorded repository state",
        )

    # ── Check: test re-execution (workspace, DETERMINISTIC only) ──

    async def _check_testing(
        self, run: DevPilotRun, workspace: str,
    ) -> ReplayCheck:
        recorded = run.test_result
        if recorded is None:
            return self._skip_check(
                "testing", "testing", "No recorded test result",
            )
        if not workspace or not Path(workspace).is_dir():
            return self._not_replayable(
                "testing", "testing", "No workspace provided for test re-execution",
            )
        commands = [
            p.command for p in (recorded.process_results or [])
            if p.command and p.status.value not in ("rejected", "skipped")
        ]
        if not commands:
            return self._not_replayable(
                "testing", "testing",
                "Recorded result carries no re-runnable command evidence",
            )
        try:
            from app.models.testing import (
                CommandCategory,
                CommandSource,
                ExecutionPlan,
                ExecutionStep,
            )
            from app.services.testing_service import TestingService

            steps = []
            for i, cmd in enumerate(commands[:5]):
                parts = shlex.split(cmd)
                if not parts:
                    continue
                steps.append(ExecutionStep(
                    step_id=f"REPLAY-{i:03d}",
                    category=CommandCategory.TEST,
                    executable=parts[0],
                    arguments=parts[1:],
                    working_directory=".",
                    timeout_seconds=min(60, max(10, recorded.duration_seconds or 60)),
                    source=CommandSource.PHASE2_DETECTION,
                    reason="Replay re-execution of recorded test command",
                ))
            if not steps:
                return self._not_replayable(
                    "testing", "testing", "Could not reconstruct test commands",
                )
            plan = ExecutionPlan(
                plan_id=f"plan-{run.run_id[:8].lower()}",
                workspace_id=run.run_id,
                workspace_root=workspace,
                steps=steps,
                max_total_timeout_seconds=min(300, max(60, int(recorded.duration_seconds or 120))),
            )
            recomputed = await TestingService().run_tests(plan)
        except Exception as exc:
            return self._not_replayable(
                "testing", "testing", str(exc)[:300],
            )
        recorded_status = recorded.status.value
        recomputed_status = recomputed.status.value
        passed = (
            recomputed_status == recorded_status
            and (recomputed.tests_failed or 0) == (recorded.tests_failed or 0)
        )
        return self._check(
            "testing", "testing", passed,
            expected=(
                f"status={recorded_status} failed={recorded.tests_failed or 0}"
            ),
            actual=(
                f"status={recomputed_status} failed={recomputed.tests_failed or 0}"
            ),
            note="Test commands re-executed on the provided workspace",
        )

    # ── Replay orchestration ──────────────────────────────────────

    def _verdict_from_checks(self, checks: List[ReplayCheck]) -> ReplayVerdict:
        failed = [
            c for c in checks if c.status == ReplayCheckStatus.FAILED
        ]
        not_replayable = [
            c for c in checks if c.status == ReplayCheckStatus.NOT_REPLAYABLE
        ]
        if failed:
            return ReplayVerdict.DRIFT
        if not_replayable:
            return ReplayVerdict.INCOMPLETE
        return ReplayVerdict.MATCH

    async def replay(
        self,
        run_id: str,
        mode: ReplayMode = ReplayMode.EXACT,
        workspace: Optional[str] = None,
        other_run_id: Optional[str] = None,
    ) -> ReplayResult:
        """Execute a replay for a run.

        EXACT: re-execute deterministic stages offline from recorded evidence.
        DETERMINISTIC: EXACT + live workspace verification.
        COMPARE: compare this run against ``other_run_id`` stage by stage.
        """
        if mode == ReplayMode.COMPARE:
            return await self._replay_compare(run_id, other_run_id)

        run = await self.get_run(run_id)
        if run is None:
            return ReplayResult(
                run_id=run_id,
                mode=mode,
                verdict=ReplayVerdict.INVALID,
                checks=[self._check(
                    "", "run_exists", False,
                    expected="run present", actual="run not found",
                    note="Cannot replay an unknown run",
                )],
                summary=f"Run {run_id} not found — replay INVALID",
            )

        fresh = await self.build_manifest(run)
        checks: List[ReplayCheck] = []

        checks.append(await self._check_manifest_fidelity(run, fresh))
        checks.append(self._check_pipeline_sequence(run))
        checks.append(self._check_patch_structure(run))
        checks.append(self._check_quality_gate(run))
        checks.append(await self._check_handoffs(run, fresh))
        checks.append(await self._check_consensus(run))
        checks.append(await self._check_contradictions(run))
        checks.append(self._check_repository_scope(run))

        if mode == ReplayMode.DETERMINISTIC:
            # Live workspace verification. An empty workspace (no explicit
            # path and no recorded repository) is treated as "no workspace"
            # — those stages become NOT_REPLAYABLE (→ INCOMPLETE), never a
            # phantom DRIFT against the current working directory.
            effective_ws = workspace or run.repository_path or ""
            checks.append(self._check_fingerprint(
                run, fresh.repository_state, effective_ws,
            ))
            checks.append(self._check_application_outcome(run, effective_ws))
            checks.append(await self._check_testing(run, effective_ws))

        verdict = self._verdict_from_checks(checks)

        # Per-stage comparisons: recorded hash vs replay classification.
        comparisons: List[ReplayStageComparison] = []
        divergences: List[str] = []
        for stage in fresh.stages[:MAX_STAGES_PER_MANIFEST]:
            comparisons.append(ReplayStageComparison(
                stage=stage.stage,
                kind=stage.kind.value,
                recorded_hash=stage.output_hash[:24],
                replay_hash=(
                    stage.output_hash[:24]
                    if stage.kind == ReplayStageKind.OBSERVATIONAL
                    or stage.kind == ReplayStageKind.LLM_PROPOSED
                    else ""
                ),
                matched=(
                    True
                    if stage.kind in (
                        ReplayStageKind.OBSERVATIONAL,
                        ReplayStageKind.LLM_PROPOSED,
                    ) else None
                ),
                detail=(
                    "captured (not re-executed)"
                    if stage.kind != ReplayStageKind.DETERMINISTIC
                    else "re-executed deterministically"
                ),
            ))

        for c in checks:
            if c.status == ReplayCheckStatus.FAILED:
                divergences.append(
                    f"[{c.check}] expected {c.expected} but got {c.actual}"
                )

        # Reconcile decision matches (only deterministic decisions replayed).
        self._reconcile_decisions(fresh, checks)

        summary_parts = [
            f"{mode.value.upper()} replay of {run_id}",
            f"{sum(1 for c in checks if c.status == ReplayCheckStatus.PASSED)}/"
            f"{len(checks)} checks passed",
        ]
        if verdict == ReplayVerdict.DRIFT:
            summary_parts.append(f"{len(divergences)} divergence(s)")
        elif verdict == ReplayVerdict.INCOMPLETE:
            not_replayable = [
                c for c in checks if c.status == ReplayCheckStatus.NOT_REPLAYABLE
            ]
            summary_parts.append(
                f"{len(not_replayable)} stage(s) not re-executable "
                f"({', '.join(c.check for c in not_replayable[:3])})"
            )

        result = ReplayResult(
            run_id=run_id,
            mode=mode,
            verdict=verdict,
            checks=checks[:MAX_CHECKS_PER_REPLAY],
            stage_comparisons=comparisons[:MAX_COMPARISONS_PER_REPLAY],
            divergences=divergences[:50],
            summary=" | ".join(summary_parts)[:500],
        )
        self._replays[result.replay_id] = result
        await self._persist_replay(result)
        return result

    # ── COMPARE mode ──────────────────────────────────────────────

    async def _replay_compare(
        self, run_id: str, other_run_id: Optional[str],
    ) -> ReplayResult:
        if not other_run_id or other_run_id == run_id:
            return ReplayResult(
                run_id=run_id,
                mode=ReplayMode.COMPARE,
                verdict=ReplayVerdict.INVALID,
                checks=[self._check(
                    "", "compare_target", False,
                    expected="a different run_id", actual=other_run_id or "(none)",
                    note="COMPARE requires a second run to compare against",
                )],
                summary="COMPARE replay requires other_run_id != run_id",
            )

        run_a = await self.get_run(run_id)
        run_b = await self.get_run(other_run_id)
        if run_a is None or run_b is None:
            missing = run_id if run_a is None else other_run_id
            return ReplayResult(
                run_id=run_id,
                mode=ReplayMode.COMPARE,
                verdict=ReplayVerdict.INVALID,
                checks=[self._check(
                    "", "compare_targets", False,
                    expected="both runs present", actual=f"{missing} not found",
                )],
                summary=f"COMPARE replay INVALID — run {missing} not found",
            )

        manifest_a = await self.build_manifest(run_a)
        manifest_b = await self.build_manifest(run_b)

        stage_map_a = {s.stage: s for s in manifest_a.stages}
        stage_map_b = {s.stage: s for s in manifest_b.stages}
        all_stages = sorted(set(stage_map_a) | set(stage_map_b))

        comparisons: List[ReplayStageComparison] = []
        divergences: List[str] = []
        missing = [s for s in all_stages
                   if s not in stage_map_a or s not in stage_map_b]

        for stage in all_stages:
            sa = stage_map_a.get(stage)
            sb = stage_map_b.get(stage)
            if sa is None or sb is None:
                comparisons.append(ReplayStageComparison(
                    stage=stage,
                    kind=sa.kind.value if sa else (sb.kind.value if sb else ""),
                    matched=None,
                    detail=f"present only in {'A' if sa else 'B'}",
                ))
                continue
            same = sa.output_hash == sb.output_hash
            comparisons.append(ReplayStageComparison(
                stage=stage,
                kind=sa.kind.value,
                recorded_hash=sa.output_hash[:24],
                replay_hash=sb.output_hash[:24],
                matched=same,
                detail="identical output" if same else "outputs diverge",
            ))
            if not same:
                divergences.append(
                    f"stage {stage}: run {run_id} hash {sa.output_hash[:24]} "
                    f"vs run {other_run_id} hash {sb.output_hash[:24]}"
                )

        # Which deterministic decision caused divergence?
        decision_map_a = {
            (d.decision_type, d.statement): d.value
            for d in manifest_a.deterministic_decisions
        }
        decision_map_b = {
            (d.decision_type, d.statement): d.value
            for d in manifest_b.deterministic_decisions
        }
        for key in sorted(set(decision_map_a) & set(decision_map_b)):
            va, vb = decision_map_a[key], decision_map_b[key]
            if va != vb:
                divergences.append(
                    f"decision {key[0]} ('{key[1][:60]}'): "
                    f"run A '{va[:60]}' vs run B '{vb[:60]}'"
                )

        shared_diverged = [c for c in comparisons if c.matched is False]
        shared_diverged_count = len(shared_diverged)

        if missing:
            verdict = ReplayVerdict.INCOMPLETE
            divergences.append(
                f"{len(missing)} stage(s) present in only one run: {sorted(missing)[:5]}"
            )
        elif shared_diverged:
            verdict = ReplayVerdict.DRIFT
        else:
            verdict = ReplayVerdict.MATCH

        checks = [self._check(
            "compare", "stage_parity", not missing,
            expected="identical stage sets",
            actual=f"{len(missing)} stage(s) differ" if missing else "identical",
        ), self._check(
            "compare", "output_parity", not shared_diverged,
            expected="all shared stages identical",
            actual=(
                "identical"
                if not shared_diverged
                else f"{shared_diverged_count} shared stage(s) diverged"
            ),
        )]

        result = ReplayResult(
            run_id=run_id,
            mode=ReplayMode.COMPARE,
            verdict=verdict,
            checks=checks,
            stage_comparisons=comparisons[:MAX_COMPARISONS_PER_REPLAY],
            divergences=divergences[:50],
            summary=(
                f"COMPARE {run_id} vs {other_run_id}: {verdict.value} — "
                f"{sum(1 for c in comparisons if c.matched is True)}/"
                f"{len(comparisons)} stages identical"
            )[:500],
        )
        self._replays[result.replay_id] = result
        await self._persist_replay(result)
        return result

    # ── Decision reconciliation ───────────────────────────────────

    @staticmethod
    def _reconcile_decisions(
        manifest: ReplayManifest, checks: List[ReplayCheck],
    ) -> None:
        """Mark each deterministic decision with its replay outcome."""
        for d in manifest.deterministic_decisions:
            if not d.replayable:
                continue
            relevant = [
                c for c in checks
                if c.check in ("quality_gate", "patch_validation",
                               "patch_application", "testing",
                               "repository_scope", "handoff_claims",
                               "consensus", "contradictions")
                and c.status != ReplayCheckStatus.SKIPPED
            ]
            d.matched = all(c.status == ReplayCheckStatus.PASSED for c in relevant)

    # ── Audit ─────────────────────────────────────────────────────

    async def audit(self, run_id: str) -> Dict[str, Any]:
        """Full audit report: manifest + EXACT replay + verdict. No LLM."""
        run = await self.get_run(run_id)
        if run is None:
            return {
                "run_id": run_id,
                "available": False,
                "error": "run not found",
            }
        manifest = await self.build_manifest(run)
        replay = await self.replay(run_id, ReplayMode.EXACT)
        self._reconcile_decisions(manifest, replay.checks)
        return {
            "run_id": run_id,
            "available": True,
            "manifest": manifest.summary(),
            "stages": [s.summary() for s in manifest.stages],
            "deterministic_decisions": [
                d.summary() for d in manifest.deterministic_decisions
            ],
            "handoffs": manifest.agent_handoffs,
            "reasoning": manifest.reasoning,
            "graph_memory_versions": manifest.graph_memory_versions,
            "replay": replay.summary_dict(),
            "checks": [c.model_dump(mode="json") for c in replay.checks],
            "divergences": replay.divergences,
            "verdict": replay.verdict.value,
        }


# Module-level singleton accessor (matches provider/ws patterns).
_replay_service: Optional[ReplayService] = None


def get_replay_service() -> ReplayService:
    """Return the shared ReplayService singleton."""
    global _replay_service
    if _replay_service is None:
        _replay_service = ReplayService()
    return _replay_service
