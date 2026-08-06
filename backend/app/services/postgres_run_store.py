"""
PostgresRunStore — PostgreSQL-backed implementation of the RunStore Protocol.

Persists DevPilotRun, RunEvent, StageResult, and artifact data to PostgreSQL.
Supports optimistic concurrency, transactional state transitions, recovery,
and safe resume after backend restart.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import logger
from app.db.models import (
    ArtifactModel,
    CodeRelationshipModel,
    CodeSymbolModel,
    RepositoryIndexModel,
    RunEventModel,
    RunModel,
    StageResultModel,
    WorkspaceModel,
)
from app.db.session import create_session_factory
from app.models.base import new_id
from app.models.orchestration import (
    DevPilotRun,
    EventType,
    FailureCode,
    RunEvent,
    RunFailure,
    RunSource,
    RunSourceType,
    RunStatus,
    StageResult,
    StageStatus,
    StageType,
)
from app.services.run_store import RunStore, generate_run_id


class ConcurrentRunUpdateError(Exception):
    """Raised when a concurrent state modification is detected."""


class RunNotFoundError(Exception):
    """Raised when a run is not found."""


# ── Run context round-trip (Phase 16/17) ───────────────────────
# The autonomy controller pre-populates a run's context (repository_profile,
# requirements, plan, retrieved_context, and the patch/test/repair/review/gate
# outputs) before calling execute_run. execute_run re-hydrates the run from the
# store, so the durable store MUST round-trip that context or the strict state
# machine rejects the first real transition (ANALYZING_TASK -> PLANNING).

# Context fields serialized into context_json (not separate columns).
# Plain-string fields are excluded: they are either columns (e.g.
# source_repository_path) or reconstructed from the source on re-hydration.
#
# Phase 20A6: the multi-repository dashboard state (repository_path,
# auxiliary_repositories, repo_patches) is ALSO round-tripped so a backend
# restart can rebuild the repository-aware run-detail view identically.
_CONTEXT_FIELDS = (
    "repository_profile",
    "requirements",
    "plan",
    "retrieved_context",
    "patch_set",
    "patch_result",
    "test_result",
    "repair_result",
    "review_report",
    "quality_gate_result",
    "repository_path",
    "auxiliary_repositories",
    "repo_patches",
)


def _serialize_context_value(value: Any) -> Any:
    """Serialize one context value into a JSON-safe payload.

    Pydantic models dump via ``model_dump``; plain JSON-safe values (strings,
    lists of dicts, etc.) pass through unchanged. Non-serializable content
    returns None so the caller can skip it and keep the run durable.
    """
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            return dump(mode="json")
        except Exception:
            return None
    if isinstance(value, (list, tuple)):
        out: List[Any] = []
        for item in value:
            item_dump = getattr(item, "model_dump", None)
            if callable(item_dump):
                try:
                    out.append(item_dump(mode="json"))
                except Exception:
                    return None
            else:
                out.append(item)
        return out
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return None
    return value


def _serialize_context(run: "DevPilotRun") -> Optional[Dict[str, Any]]:
    """Serialize a run's context fields into a JSON-safe payload.

    Attribute-less stub objects (used by deterministic demos/tests to stand in
    for real stage outputs) are skipped — they cannot round-trip and are not
    part of the durable audit trail.
    """
    payload: Dict[str, Any] = {}
    for field in _CONTEXT_FIELDS:
        value = getattr(run, field, None)
        if value is None:
            continue
        serialized = _serialize_context_value(value)
        if serialized is None:
            continue  # non-serializable content — skip, keep the run durable
        payload[field] = serialized
    return payload or None


def _deserialize_context(data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Restore serialized context fields onto a DevPilotRun."""
    if not data:
        return {}
    from app.models.coding import PatchApplicationResult, PatchSet
    from app.models.issues import ImplementationPlan, StructuredRequirements
    from app.models.orchestration import RepositoryPatchResult
    from app.models.profile import RepositoryProfile
    from app.models.rag import RetrievedContext
    from app.models.repair import RepairResult
    from app.models.review import QualityGateResult, ReviewReport
    from app.models.testing import TestRunResult

    _MODELS = {
        "repository_profile": RepositoryProfile,
        "requirements": StructuredRequirements,
        "plan": ImplementationPlan,
        "retrieved_context": RetrievedContext,
        "patch_set": PatchSet,
        "patch_result": PatchApplicationResult,
        "test_result": TestRunResult,
        "repair_result": RepairResult,
        "review_report": ReviewReport,
        "quality_gate_result": QualityGateResult,
    }
    restored: Dict[str, Any] = {}
    for field, model in _MODELS.items():
        raw = data.get(field)
        if raw is None:
            continue
        try:
            restored[field] = model.model_validate(raw)
        except Exception:
            continue  # schema drift — skip rather than crash re-hydration

    # Phase 20A6 — plain / list-of-model dashboard state.
    if "repository_path" in data:
        restored["repository_path"] = data["repository_path"]
    if "auxiliary_repositories" in data:
        restored["auxiliary_repositories"] = data["auxiliary_repositories"]
    if "repo_patches" in data and isinstance(data["repo_patches"], list):
        restored["repo_patches"] = [
            RepositoryPatchResult.model_validate(raw)
            for raw in data["repo_patches"]
            if isinstance(raw, dict)
        ]
    return restored


# ── Timezone helpers ────────────────────────────────────────────


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(iso_str: Optional[str]) -> Optional[datetime]:
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _format_dt(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.isoformat()


# ── Serialization helpers ──────────────────────────────────────


def _serialize_stage_result(sr: StageResult) -> Dict[str, Any]:
    return {
        "stage": sr.stage.value,
        "status": sr.status.value,
        "started_at": sr.started_at,
        "finished_at": sr.finished_at,
        "duration_ms": sr.duration_ms,
        "error": sr.error,
        "warnings": sr.warnings,
        "metadata": sr.metadata,
    }


def _deserialize_stage_result(data: Dict[str, Any]) -> StageResult:
    return StageResult(
        stage=StageType(data.get("stage", "initializing")),
        status=StageStatus(data.get("status", "pending")),
        started_at=data.get("started_at"),
        finished_at=data.get("finished_at"),
        duration_ms=data.get("duration_ms"),
        error=data.get("error"),
        warnings=data.get("warnings", []),
        metadata=data.get("metadata", {}),
    )


def _serialize_event(e: RunEvent) -> Dict[str, Any]:
    return {
        "event_id": e.event_id,
        "run_id": e.run_id,
        "timestamp": e.timestamp,
        "event_type": e.event_type.value,
        "stage": e.stage.value if e.stage else None,
        "message": e.message,
        "metadata": e.metadata,
    }


def _deserialize_event(data: Dict[str, Any]) -> RunEvent:
    return RunEvent(
        event_id=data.get("event_id", ""),
        run_id=data.get("run_id", ""),
        timestamp=data.get("timestamp", ""),
        event_type=EventType(data.get("event_type", "run_created")),
        stage=StageType(data["stage"]) if data.get("stage") else None,
        message=data.get("message", ""),
        metadata=data.get("metadata", {}),
    )


def _deserialize_run(model: RunModel) -> DevPilotRun:
    """Convert a RunModel ORM instance to a DevPilotRun Pydantic model."""
    source_type = RunSourceType(model.source_type)

    source = RunSource(
        source_type=source_type,
        title=model.source_title or "",
        description=model.source_description or "",
        repository_path=model.source_repository_path,
        issue_number=model.source_issue_number,
        issue_url=model.source_issue_url,
    )

    stage_results = []
    if model.stage_results_data:
        for sr_data in model.stage_results_data:
            stage_results.append(_deserialize_stage_result(sr_data))

    events = []
    if model.events_data:
        for e_data in model.events_data:
            events.append(_deserialize_event(e_data))

    failure = None
    if model.failure_data:
        failure = RunFailure(
            stage=StageType(model.failure_data.get("stage", "initializing")),
            code=FailureCode(model.failure_data.get("code", "unknown")),
            message=model.failure_data.get("message", ""),
            recoverable=model.failure_data.get("recoverable", False),
            details=model.failure_data.get("details", {}),
        )

    run = DevPilotRun(
        run_id=model.run_id,
        source=source,
        status=RunStatus(model.status),
        current_stage=StageType(model.current_stage),
        created_at=_format_dt(model.created_at) or _utcnow().isoformat(),
        started_at=_format_dt(model.started_at),
        finished_at=_format_dt(model.finished_at),
        stage_results=stage_results,
        events=events,
        warnings=model.warnings_list or [],
        failure=failure,
        cancellation_requested=model.cancellation_requested or False,
        cancelled_at=_format_dt(model.cancelled_at),
        total_duration_ms=model.total_duration_ms,
    )
    # Restore the context round-trip so execute_run's re-hydration keeps the
    # autonomy controller's pre-populated context (repository_profile,
    # requirements, plan, retrieved_context, stage outputs).
    for field, value in _deserialize_context(model.context_json).items():
        setattr(run, field, value)
    return run


# ── PostgresRunStore ───────────────────────────────────────────


class PostgresRunStore:
    """PostgreSQL-backed run storage.

    Implements the RunStore Protocol with additional Phase 11 methods
    for events, stage results, artifacts, recovery, and resume.

    Thread-safe via database transactions. Uses optimistic concurrency
    via the version field.
    """

    def __init__(self, database_url: Optional[str] = None) -> None:
        self._database_url = database_url
        self._session_factory: Optional[async_sessionmaker[AsyncSession]] = None
        # Owned engine when created from an explicit URL (tracked so it can be
        # disposed; avoids leaking a pool per store).
        self._owned_engine: Optional[Any] = None

    def _get_session_factory(self) -> async_sessionmaker[AsyncSession]:
        if self._session_factory is None:
            if self._database_url:
                # Honor the explicit URL (contract tests / demo point at a
                # test-named DB). Previously this parameter was silently
                # ignored and the store always connected to
                # settings.DATABASE_URL, which meant a live-PG full-suite run
                # against a separated dev/test setup hit the dev schema
                # (missing context_json etc.).
                from app.db.database import create_async_engine

                engine = create_async_engine(self._database_url)
                if engine is None:
                    raise RuntimeError(
                        f"Failed to create engine for {self._database_url}"
                    )
                self._owned_engine = engine
                self._session_factory = create_session_factory(engine=engine)
            else:
                self._session_factory = create_session_factory()
        return self._session_factory

    async def dispose(self) -> None:
        """Dispose an owned engine (no-op when using the shared engine)."""
        if self._owned_engine is not None:
            await self._owned_engine.dispose()
            self._owned_engine = None
            self._session_factory = None

    async def _with_session(self, callback):
        """Execute a callback within a session context."""
        factory = self._get_session_factory()
        async with factory() as session:
            return await callback(session)

    # ── RunStore Protocol Methods ───────────────────────────────

    async def create(self, run: DevPilotRun) -> DevPilotRun:
        """Store a new run in PostgreSQL."""

        async def _impl(session: AsyncSession):
            now = _utcnow()
            run_id = run.run_id or generate_run_id()

            stage_results_data = [_serialize_stage_result(sr) for sr in run.stage_results] or None
            events_data = [_serialize_event(e) for e in run.events] or None

            failure_data = None
            if run.failure:
                failure_data = {
                    "stage": run.failure.stage.value,
                    "code": run.failure.code.value,
                    "message": run.failure.message,
                    "recoverable": run.failure.recoverable,
                    "details": run.failure.details,
                }

            created_at = _parse_dt(run.created_at) or now

            model = RunModel(
                run_id=run_id,
                source_type=run.source.source_type.value,
                status=run.status.value,
                current_stage=run.current_stage.value,
                source_title=run.source.title or "",
                source_description=run.source.description,
                source_repository_path=run.source.repository_path,
                source_issue_number=run.source.issue_number,
                source_issue_url=run.source.issue_url,
                stage_results_data=stage_results_data,
                events_data=events_data,
                warnings_list=run.warnings or [],
                failure_data=failure_data,
                context_json=_serialize_context(run),
                cancellation_requested=run.cancellation_requested,
                cancelled_at=_parse_dt(run.cancelled_at),
                version=1,
                created_at=created_at,
                started_at=_parse_dt(run.started_at),
                updated_at=now,
                finished_at=_parse_dt(run.finished_at),
                total_duration_ms=run.total_duration_ms,
            )

            session.add(model)
            await session.flush()

            for seq, event in enumerate(run.events, 1):
                event_model = RunEventModel(
                    event_id=event.event_id,
                    run_id_fk=model.id,
                    sequence=seq,
                    event_type=event.event_type.value,
                    stage=event.stage.value if event.stage else None,
                    message=event.message[:500],
                    metadata_json=event.metadata or None,
                    created_at=_parse_dt(event.timestamp) or now,
                )
                session.add(event_model)

            for sr in run.stage_results:
                sr_model = StageResultModel(
                    run_id_fk=model.id,
                    stage=sr.stage.value,
                    status=sr.status.value,
                    started_at=_parse_dt(sr.started_at),
                    finished_at=_parse_dt(sr.finished_at),
                    duration_ms=sr.duration_ms,
                    error_message=sr.error[:500] if sr.error else None,
                    warnings=sr.warnings or None,
                    metadata_json=sr.metadata or None,
                )
                session.add(sr_model)

            await session.commit()
            run.run_id = run_id
            return run

        return await self._with_session(_impl)

    async def get(self, run_id: str) -> Optional[DevPilotRun]:
        """Retrieve a run by ID from PostgreSQL."""

        async def _impl(session: AsyncSession):
            stmt = select(RunModel).where(RunModel.run_id == run_id)
            result = await session.execute(stmt)
            model = result.scalar_one_or_none()
            if model is None:
                return None
            return _deserialize_run(model)

        return await self._with_session(_impl)

    async def update(self, run: DevPilotRun) -> DevPilotRun:
        """Update an existing run in PostgreSQL with optimistic concurrency."""

        async def _impl(session: AsyncSession):
            now = _utcnow()

            stmt = select(RunModel).where(RunModel.run_id == run.run_id)
            result = await session.execute(stmt)
            model = result.scalar_one_or_none()
            if model is None:
                raise RunNotFoundError(f"Run {run.run_id} not found")

            expected_version = model.version

            stage_results_data = [_serialize_stage_result(sr) for sr in run.stage_results] or None
            events_data = [_serialize_event(e) for e in run.events] or None

            failure_data = None
            if run.failure:
                failure_data = {
                    "stage": run.failure.stage.value,
                    "code": run.failure.code.value,
                    "message": run.failure.message,
                    "recoverable": run.failure.recoverable,
                    "details": run.failure.details,
                }

            model.source_type = run.source.source_type.value
            model.status = run.status.value
            model.current_stage = run.current_stage.value
            model.source_title = run.source.title or ""
            model.source_description = run.source.description
            model.source_repository_path = run.source.repository_path
            model.source_issue_number = run.source.issue_number
            model.source_issue_url = run.source.issue_url
            model.stage_results_data = stage_results_data
            model.events_data = events_data
            model.warnings_list = run.warnings or []
            model.failure_data = failure_data
            model.context_json = _serialize_context(run)
            model.cancellation_requested = run.cancellation_requested
            model.cancelled_at = _parse_dt(run.cancelled_at)
            model.started_at = _parse_dt(run.started_at)
            model.updated_at = now
            model.finished_at = _parse_dt(run.finished_at)
            model.total_duration_ms = run.total_duration_ms

            # Optimistic concurrency: increment version atomically
            stmt_update = (
                update(RunModel)
                .where(
                    RunModel.run_id == run.run_id,
                    RunModel.version == expected_version,
                )
                .values(version=expected_version + 1)
            )
            upd_result = await session.execute(stmt_update)
            if upd_result.rowcount == 0:
                raise ConcurrentRunUpdateError(
                    f"Run {run.run_id} was modified by another process. "
                    f"Expected version {expected_version}"
                )

            # Re-sync event rows
            await session.execute(
                delete(RunEventModel).where(RunEventModel.run_id_fk == model.id)
            )
            for seq, event in enumerate(run.events, 1):
                event_model = RunEventModel(
                    event_id=event.event_id,
                    run_id_fk=model.id,
                    sequence=seq,
                    event_type=event.event_type.value,
                    stage=event.stage.value if event.stage else None,
                    message=event.message[:500],
                    metadata_json=event.metadata or None,
                    created_at=_parse_dt(event.timestamp) or now,
                )
                session.add(event_model)

            # Re-sync stage result rows
            await session.execute(
                delete(StageResultModel).where(StageResultModel.run_id_fk == model.id)
            )
            for sr in run.stage_results:
                sr_model = StageResultModel(
                    run_id_fk=model.id,
                    stage=sr.stage.value,
                    status=sr.status.value,
                    started_at=_parse_dt(sr.started_at),
                    finished_at=_parse_dt(sr.finished_at),
                    duration_ms=sr.duration_ms,
                    error_message=sr.error[:500] if sr.error else None,
                    warnings=sr.warnings or None,
                    metadata_json=sr.metadata or None,
                )
                session.add(sr_model)

            await session.commit()
            return run

        return await self._with_session(_impl)

    async def list(
        self,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        sort_by: str = "newest",
        created_after: Optional[str] = None,
        created_before: Optional[str] = None,
    ) -> List[DevPilotRun]:
        """List runs with optional filtering, sorting, and date range."""

        async def _impl(session: AsyncSession):
            stmt = select(RunModel)
            if status:
                stmt = stmt.where(RunModel.status == status)
            # Half-open interval: created_after inclusive (>=), created_before
            # exclusive (<) — consistent with InMemoryRunStore and the API
            # contract (TestSeededTotalCount boundary assertions).
            if created_after:
                stmt = stmt.where(RunModel.created_at >= _parse_dt(created_after))
            if created_before:
                stmt = stmt.where(RunModel.created_at < _parse_dt(created_before))
            if sort_by == "oldest":
                stmt = stmt.order_by(RunModel.created_at.asc())
            elif sort_by == "duration":
                stmt = stmt.order_by(RunModel.total_duration_ms.desc().nullslast())
            else:
                stmt = stmt.order_by(RunModel.created_at.desc())
            stmt = stmt.offset(offset).limit(min(limit, 200))

            result = await session.execute(stmt)
            models = result.scalars().all()
            return [_deserialize_run(m) for m in models]

        return await self._with_session(_impl)

    async def delete(self, run_id: str) -> bool:
        """Remove a run from PostgreSQL."""

        async def _impl(session: AsyncSession):
            stmt = select(RunModel).where(RunModel.run_id == run_id)
            result = await session.execute(stmt)
            model = result.scalar_one_or_none()
            if model is None:
                return False
            await session.delete(model)
            await session.commit()
            return True

        return await self._with_session(_impl)

    async def request_cancel(self, run_id: str) -> bool:
        """Request cancellation of a run."""

        async def _impl(session: AsyncSession):
            stmt = select(RunModel).where(RunModel.run_id == run_id)
            result = await session.execute(stmt)
            model = result.scalar_one_or_none()
            if model is None:
                return False

            terminal_statuses = {"approved", "rejected", "needs_human_review", "failed", "cancelled"}
            if model.status in terminal_statuses:
                return False

            model.cancellation_requested = True
            model.updated_at = _utcnow()
            await session.commit()
            return True

        return await self._with_session(_impl)

    # ── Phase 11: Event Methods ─────────────────────────────────

    async def append_event(self, run_id: str, event: RunEvent) -> RunEvent:
        """Append an event to a run and persist it."""

        async def _impl(session: AsyncSession):
            stmt = select(RunModel).where(RunModel.run_id == run_id)
            result = await session.execute(stmt)
            model = result.scalar_one_or_none()
            if model is None:
                raise RunNotFoundError(f"Run {run_id} not found")

            seq_stmt = select(
                func.coalesce(func.max(RunEventModel.sequence), 0)
            ).where(RunEventModel.run_id_fk == model.id)
            seq_result = await session.execute(seq_stmt)
            next_seq = (seq_result.scalar() or 0) + 1

            event_model = RunEventModel(
                event_id=event.event_id,
                run_id_fk=model.id,
                sequence=next_seq,
                event_type=event.event_type.value,
                stage=event.stage.value if event.stage else None,
                message=event.message[:500],
                metadata_json=event.metadata or None,
                created_at=_parse_dt(event.timestamp) or _utcnow(),
            )
            session.add(event_model)
            model.updated_at = _utcnow()
            await session.commit()
            return event

        return await self._with_session(_impl)

    async def get_events(
        self,
        run_id: str,
        limit: int = 200,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Get sanitized events for a run, ordered by sequence."""

        async def _impl(session: AsyncSession):
            stmt = (
                select(RunEventModel)
                .join(RunModel)
                .where(RunModel.run_id == run_id)
                .order_by(RunEventModel.sequence)
                .offset(offset)
                .limit(limit)
            )
            result = await session.execute(stmt)
            event_models = result.scalars().all()

            return [
                {
                    "event_id": em.event_id,
                    "event_type": em.event_type,
                    "stage": em.stage,
                    "message": em.message[:200],
                    "timestamp": _format_dt(em.created_at),
                    "sequence": em.sequence,
                }
                for em in event_models
            ]

        return await self._with_session(_impl)

    # ── Phase 11: Stage Result Methods ──────────────────────────

    async def save_stage_result(self, run_id: str, stage_result: StageResult) -> StageResult:
        """Persist a stage result."""

        async def _impl(session: AsyncSession):
            stmt = select(RunModel).where(RunModel.run_id == run_id)
            result = await session.execute(stmt)
            model = result.scalar_one_or_none()
            if model is None:
                raise RunNotFoundError(f"Run {run_id} not found")

            sr_model = StageResultModel(
                run_id_fk=model.id,
                stage=stage_result.stage.value,
                status=stage_result.status.value,
                started_at=_parse_dt(stage_result.started_at),
                finished_at=_parse_dt(stage_result.finished_at),
                duration_ms=stage_result.duration_ms,
                error_message=stage_result.error[:500] if stage_result.error else None,
                warnings=stage_result.warnings or None,
                metadata_json=stage_result.metadata or None,
            )
            session.add(sr_model)
            model.updated_at = _utcnow()
            await session.commit()
            return stage_result

        return await self._with_session(_impl)

    async def get_stage_results(self, run_id: str) -> List[Dict[str, Any]]:
        """Get stage results for a run."""

        async def _impl(session: AsyncSession):
            stmt = (
                select(StageResultModel)
                .join(RunModel)
                .where(RunModel.run_id == run_id)
                .order_by(StageResultModel.id)
            )
            result = await session.execute(stmt)
            sr_models = result.scalars().all()

            return [
                {
                    "stage": sm.stage,
                    "status": sm.status,
                    "started_at": _format_dt(sm.started_at),
                    "finished_at": _format_dt(sm.finished_at),
                    "duration_ms": sm.duration_ms,
                    "error": sm.error_message,
                    "warnings": sm.warnings,
                }
                for sm in sr_models
            ]

        return await self._with_session(_impl)

    # ── Phase 11: Artifact Methods ──────────────────────────────

    async def save_artifact(
        self,
        run_id: str,
        artifact_type: str,
        content: Optional[Dict[str, Any]] = None,
        stage: Optional[str] = None,
        metadata_json: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Save an artifact for a run."""

        async def _impl(session: AsyncSession):
            stmt = select(RunModel).where(RunModel.run_id == run_id)
            result = await session.execute(stmt)
            model = result.scalar_one_or_none()
            if model is None:
                raise RunNotFoundError(f"Run {run_id} not found")

            artifact_id = str(uuid.uuid4())
            content_json = (
                json.loads(content.model_dump_json(exclude_none=True))
                if hasattr(content, "model_dump_json")
                else content
            )

            art_model = ArtifactModel(
                artifact_id=artifact_id,
                run_id_fk=model.id,
                artifact_type=artifact_type,
                stage=stage,
                storage_type="jsonb",
                content=content_json,
                content_hash=None,
                size_bytes=len(json.dumps(content_json)) if content_json else 0,
                metadata_json=metadata_json,
            )
            session.add(art_model)
            await session.commit()

            return {
                "artifact_id": artifact_id,
                "artifact_type": artifact_type,
                "stage": stage,
                "storage_type": "jsonb",
                "created_at": _format_dt(_utcnow()),
            }

        return await self._with_session(_impl)

    async def get_artifacts(
        self,
        run_id: str,
        artifact_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get artifacts for a run."""

        async def _impl(session: AsyncSession):
            stmt = select(ArtifactModel).join(RunModel).where(RunModel.run_id == run_id)
            if artifact_type:
                stmt = stmt.where(ArtifactModel.artifact_type == artifact_type)
            stmt = stmt.order_by(ArtifactModel.created_at.desc())

            result = await session.execute(stmt)
            art_models = result.scalars().all()

            return [
                {
                    "artifact_id": am.artifact_id,
                    "artifact_type": am.artifact_type,
                    "stage": am.stage,
                    "storage_type": am.storage_type,
                    "content": am.content,
                    "size_bytes": am.size_bytes,
                    "created_at": _format_dt(am.created_at),
                }
                for am in art_models
            ]

        return await self._with_session(_impl)

    # ── Phase 11: Recovery Methods ──────────────────────────────

    async def find_recoverable_runs(self) -> List[DevPilotRun]:
        """Find non-terminal runs that might need recovery after restart."""

        async def _impl(session: AsyncSession):
            stmt = (
                select(RunModel)
                .where(RunModel.status.in_(["pending", "running"]))
                .order_by(RunModel.created_at.desc())
            )
            result = await session.execute(stmt)
            models = result.scalars().all()
            return [_deserialize_run(m) for m in models]

        return await self._with_session(_impl)

    async def mark_stale_runs(self, max_age_minutes: int = 60) -> int:
        """Mark recoverable runs older than max_age_minutes as FAILED."""

        async def _impl(session: AsyncSession):
            count = 0
            stmt = select(RunModel).where(RunModel.status.in_(["pending", "running"]))
            result = await session.execute(stmt)
            models = result.scalars().all()

            for model in models:
                age_seconds = 0
                if model.updated_at:
                    updated = (
                        model.updated_at.replace(tzinfo=timezone.utc)
                        if model.updated_at.tzinfo is None
                        else model.updated_at
                    )
                    age_seconds = (_utcnow() - updated).total_seconds()

                if age_seconds > max_age_minutes * 60:
                    model.status = "failed"
                    model.failure_data = {
                        "stage": model.current_stage,
                        "code": "cancelled",
                        "message": "Run marked as stale after backend restart",
                        "recoverable": False,
                    }
                    count += 1

            if count > 0:
                await session.commit()
            return count

        return await self._with_session(_impl)

    async def count_runs(self, status: Optional[str] = None, created_after: Optional[str] = None, created_before: Optional[str] = None) -> int:
        """Count runs with optional status filter and date range."""

        async def _impl(session: AsyncSession):
            stmt = select(func.count(RunModel.id))
            if status:
                stmt = stmt.where(RunModel.status == status)
            # Half-open interval: created_after inclusive (>=), created_before
            # exclusive (<) — consistent with InMemoryRunStore and the API
            # contract (TestSeededTotalCount boundary assertions).
            if created_after:
                stmt = stmt.where(RunModel.created_at >= _parse_dt(created_after))
            if created_before:
                stmt = stmt.where(RunModel.created_at < _parse_dt(created_before))
            result = await session.execute(stmt)
            return result.scalar() or 0

        return await self._with_session(_impl)

    async def count_runs_by_status(self) -> Dict[str, int]:
        """Return aggregate counts for each run status in a single query.

        Uses a single GROUP BY query instead of N individual queries.
        """

        async def _impl(session: AsyncSession):
            stmt = (
                select(RunModel.status, func.count(RunModel.id))
                .group_by(RunModel.status)
            )
            result = await session.execute(stmt)
            rows = result.all()

            counts: Dict[str, int] = {
                "total": 0,
                "pending": 0, "running": 0, "approved": 0,
                "rejected": 0, "needs_human_review": 0, "failed": 0, "cancelled": 0,
            }
            for status, cnt in rows:
                counts["total"] += cnt
                if status in counts:
                    counts[status] = cnt
            return counts

        return await self._with_session(_impl)

    # ── Phase 12: Workspace Registry Methods ─────────────────────

    async def save_workspace(
        self,
        workspace_id: str,
        root_path: str,
        source_repository: Optional[str] = None,
        run_id: Optional[str] = None,
        fingerprint: Optional[str] = None,
        writable: bool = True,
        workspace_type: str = "coding",
    ) -> Dict[str, Any]:
        """Persist a workspace registration.

        Creates a new record or updates an existing one (upsert by workspace_id).

        Args:
            workspace_id: Unique workspace identifier.
            root_path: Absolute path to the workspace root.
            source_repository: Optional source repository path.
            run_id: Optional associated run ID.
            fingerprint: Optional source fingerprint.
            writable: Whether the workspace is writable.
            workspace_type: Type of workspace ("coding" or "testing").

        Returns:
            Dict with workspace metadata.
        """

        async def _impl(session: AsyncSession):
            now = _utcnow()

            # Check if workspace already exists (upsert)
            stmt = select(WorkspaceModel).where(
                WorkspaceModel.workspace_id == workspace_id
            )
            result = await session.execute(stmt)
            model = result.scalar_one_or_none()

            if model:
                # Update existing
                model.root_path = root_path
                model.source_repository = source_repository
                model.run_id = run_id
                model.fingerprint = fingerprint
                model.writable = writable
                model.workspace_type = workspace_type
                model.updated_at = now
            else:
                # Create new
                model = WorkspaceModel(
                    workspace_id=workspace_id,
                    source_repository=source_repository,
                    root_path=root_path,
                    run_id=run_id,
                    fingerprint=fingerprint,
                    writable=writable,
                    workspace_type=workspace_type,
                    created_at=now,
                    updated_at=now,
                )
                session.add(model)

            await session.commit()

            return {
                "workspace_id": model.workspace_id,
                "root_path": model.root_path,
                "source_repository": model.source_repository,
                "run_id": model.run_id,
                "fingerprint": model.fingerprint,
                "writable": model.writable,
                "workspace_type": model.workspace_type,
                "created_at": _format_dt(model.created_at),
                "updated_at": _format_dt(model.updated_at),
            }

        return await self._with_session(_impl)

    async def get_workspace(self, workspace_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a workspace registration by ID."""

        async def _impl(session: AsyncSession):
            stmt = select(WorkspaceModel).where(
                WorkspaceModel.workspace_id == workspace_id
            )
            result = await session.execute(stmt)
            model = result.scalar_one_or_none()
            if model is None:
                return None
            return {
                "workspace_id": model.workspace_id,
                "root_path": model.root_path,
                "source_repository": model.source_repository,
                "run_id": model.run_id,
                "fingerprint": model.fingerprint,
                "writable": model.writable,
                "workspace_type": model.workspace_type,
                "created_at": _format_dt(model.created_at),
                "updated_at": _format_dt(model.updated_at),
            }

        return await self._with_session(_impl)

    async def list_workspaces(
        self,
        run_id: Optional[str] = None,
        workspace_type: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """List workspace registrations, optionally filtered."""

        async def _impl(session: AsyncSession):
            stmt = select(WorkspaceModel)
            if run_id:
                stmt = stmt.where(WorkspaceModel.run_id == run_id)
            if workspace_type:
                stmt = stmt.where(
                    WorkspaceModel.workspace_type == workspace_type
                )
            stmt = stmt.order_by(WorkspaceModel.created_at.desc())
            stmt = stmt.offset(offset).limit(limit)

            result = await session.execute(stmt)
            models = result.scalars().all()

            return [
                {
                    "workspace_id": m.workspace_id,
                    "root_path": m.root_path,
                    "source_repository": m.source_repository,
                    "run_id": m.run_id,
                    "fingerprint": m.fingerprint,
                    "writable": m.writable,
                    "workspace_type": m.workspace_type,
                    "created_at": _format_dt(m.created_at),
                    "updated_at": _format_dt(m.updated_at),
                }
                for m in models
            ]

        return await self._with_session(_impl)

    async def delete_workspace(self, workspace_id: str) -> bool:
        """Remove a workspace registration."""

        async def _impl(session: AsyncSession):
            stmt = select(WorkspaceModel).where(
                WorkspaceModel.workspace_id == workspace_id
            )
            result = await session.execute(stmt)
            model = result.scalar_one_or_none()
            if model is None:
                return False
            await session.delete(model)
            await session.commit()
            return True

        return await self._with_session(_impl)

    async def count_workspaces(
        self,
        run_id: Optional[str] = None,
        workspace_type: Optional[str] = None,
    ) -> int:
        """Count workspace registrations, optionally filtered."""

        async def _impl(session: AsyncSession):
            stmt = select(func.count(WorkspaceModel.id))
            if run_id:
                stmt = stmt.where(WorkspaceModel.run_id == run_id)
            if workspace_type:
                stmt = stmt.where(
                    WorkspaceModel.workspace_type == workspace_type
                )
            result = await session.execute(stmt)
            return result.scalar() or 0

        return await self._with_session(_impl)

    async def list_with_total_and_stats(
        self,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        sort_by: str = "newest",
        created_after: Optional[str] = None,
        created_before: Optional[str] = None,
    ) -> tuple[List[DevPilotRun], int, Dict[str, int]]:
        """List runs + total count (respecting filters) + unfiltered stats — all in **one** session.

        Returns:
            (runs_list, total_matching_count, stats_by_status)
        """

        async def _impl(session: AsyncSession) -> tuple[List[DevPilotRun], int, Dict[str, int]]:
            # ── 1. Build the base filter ──
            base = select(RunModel)
            if status:
                base = base.where(RunModel.status == status)
            # Half-open interval: created_after inclusive (>=), created_before
            # exclusive (<) — consistent with InMemoryRunStore and the API
            # contract (TestSeededTotalCount boundary assertions).
            if created_after:
                base = base.where(RunModel.created_at >= _parse_dt(created_after))
            if created_before:
                base = base.where(RunModel.created_at < _parse_dt(created_before))

            # ── 2. List query (paginated + sorted) ──
            list_stmt = base
            if sort_by == "oldest":
                list_stmt = list_stmt.order_by(RunModel.created_at.asc())
            elif sort_by == "duration":
                list_stmt = list_stmt.order_by(RunModel.total_duration_ms.desc().nullslast())
            else:
                list_stmt = list_stmt.order_by(RunModel.created_at.desc())
            list_stmt = list_stmt.offset(offset).limit(min(limit, 200))

            list_result = await session.execute(list_stmt)
            runs = [_deserialize_run(m) for m in list_result.scalars().all()]

            # ── 3. Total count (same filters as list) ──
            count_stmt = select(func.count()).select_from(base.subquery())
            total = (await session.execute(count_stmt)).scalar() or 0

            # ── 4. Unfiltered stats ──
            stats_stmt = select(RunModel.status, func.count(RunModel.id)).group_by(RunModel.status)
            stats_rows = (await session.execute(stats_stmt)).all()
            stats: Dict[str, int] = {
                "total": 0, "pending": 0, "running": 0, "approved": 0,
                "rejected": 0, "needs_human_review": 0, "failed": 0, "cancelled": 0,
            }
            for s, cnt in stats_rows:
                stats["total"] += cnt
                if s in stats:
                    stats[s] = cnt

            return runs, total, stats

        return await self._with_session(_impl)

    # ── Phase 12: Code Intelligence Graph Persistence ───────────

    async def save_graph(
        self,
        graph: "SemanticRepositoryGraph",
        repository_id: str,
        index_id: str,
        repository_path: str,
        content_fingerprint: Optional[str] = None,
        language_coverage: Optional[Dict[str, Any]] = None,
        file_count: int = 0,
    ) -> Dict[str, Any]:
        """Persist a SemanticRepositoryGraph to PostgreSQL.

        Saves all nodes to code_symbols table, edges to code_relationships
        table, and index metadata to repository_indexes table.

        This is an upsert operation — if the index_id already exists,
        symbols and relationships are replaced atomically.

        Args:
            graph: The semantic graph to persist.
            repository_id: Repository identifier.
            index_id: Unique index batch ID.
            repository_path: Absolute path to the repository.
            content_fingerprint: Optional content fingerprint.
            language_coverage: Optional dict of language -> file count.
            file_count: Total indexed file count.

        Returns:
            Dict with symbol_count, relationship_count, and index metadata.
        """
        # Avoid circular import by doing a late import
        from app.code_intelligence.semantic_graph import (
            SemanticRepositoryGraph,
        )

        if not isinstance(graph, SemanticRepositoryGraph):
            raise TypeError(f"Expected SemanticRepositoryGraph, got {type(graph).__name__}")

        graph_dict = graph.to_dict()
        nodes = graph_dict.get("nodes", [])
        edges = graph_dict.get("edges", [])

        async def _impl(session: AsyncSession):
            now = _utcnow()

            # ── 1. Upsert index metadata ──
            stmt = select(RepositoryIndexModel).where(
                RepositoryIndexModel.index_id == index_id
            )
            result = await session.execute(stmt)
            index_model = result.scalar_one_or_none()

            if index_model:
                # Update existing
                index_model.repository_id = repository_id
                index_model.repository_path = repository_path
                index_model.content_fingerprint = content_fingerprint
                index_model.language_coverage = language_coverage or {}
                index_model.symbol_count = len(nodes)
                index_model.relationship_count = len(edges)
                index_model.file_count = file_count
                index_model.status = "active"
                index_model.updated_at = now
            else:
                # Create new
                index_model = RepositoryIndexModel(
                    index_id=index_id,
                    repository_id=repository_id,
                    repository_path=repository_path,
                    content_fingerprint=content_fingerprint,
                    language_coverage=language_coverage or {},
                    symbol_count=len(nodes),
                    relationship_count=len(edges),
                    file_count=file_count,
                    status="active",
                    version="12.0",
                    created_at=now,
                    updated_at=now,
                )
                session.add(index_model)

            # ── 2. Delete existing symbols/edges for this index ──
            await session.execute(
                delete(CodeSymbolModel).where(
                    CodeSymbolModel.index_id == index_id
                )
            )
            await session.execute(
                delete(CodeRelationshipModel).where(
                    CodeRelationshipModel.index_id == index_id
                )
            )

            # ── 3. Bulk insert symbols ──
            symbol_rows = []
            for node_data in nodes:
                symbol_rows.append({
                    "symbol_id": node_data["id"],
                    "name": node_data["name"],
                    "qualified_name": node_data.get("qualified_name", node_data["name"]),
                    "kind": node_data.get("kind", "unknown"),
                    "file_path": node_data.get("file_path", ""),
                    "language": node_data.get("language"),
                    "signature": node_data.get("signature"),
                    "docstring": node_data.get("docstring"),
                    "start_line": node_data.get("start_line"),
                    "end_line": node_data.get("end_line"),
                    "parent_symbol_id": node_data.get("parent_id"),
                    "metadata_json": node_data.get("metadata", {}),
                    "repository_id": repository_id,
                    "index_id": index_id,
                })

            if symbol_rows:
                # Batch insert in chunks to avoid oversized queries
                chunk_size = 500
                for i in range(0, len(symbol_rows), chunk_size):
                    chunk = symbol_rows[i:i + chunk_size]
                    await session.execute(
                        insert(CodeSymbolModel).values(chunk)
                    )

            # ── 4. Bulk insert relationships ──
            edge_rows = []
            for edge_data in edges:
                edge_rows.append({
                    "source_symbol_id": edge_data["source_id"],
                    "target_symbol_id": edge_data["target_id"],
                    "relationship": edge_data.get("relationship", "depends_on"),
                    "confidence": edge_data.get("confidence", "medium"),
                    "source_lines": edge_data.get("source_lines"),
                    "resolution_detail": edge_data.get("resolution_detail"),
                    "weight": edge_data.get("weight", 1.0),
                    "metadata_json": edge_data.get("metadata", {}),
                    "repository_id": repository_id,
                    "index_id": index_id,
                })

            if edge_rows:
                chunk_size = 500
                for i in range(0, len(edge_rows), chunk_size):
                    chunk = edge_rows[i:i + chunk_size]
                    await session.execute(
                        insert(CodeRelationshipModel).values(chunk)
                    )

            await session.commit()

            return {
                "index_id": index_id,
                "repository_id": repository_id,
                "symbol_count": len(symbol_rows),
                "relationship_count": len(edge_rows),
                "status": "active",
            }

        return await self._with_session(_impl)

    async def load_graph(
        self,
        index_id: Optional[str] = None,
        repository_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Load a SemanticRepositoryGraph from PostgreSQL.

        Loads symbols from code_symbols and relationships from
        code_relationships, then reconstructs the graph.

        Args:
            index_id: Specific index batch to load.
            repository_id: Most recent active index for this repo.
                Ignored if index_id is provided.

        Returns:
            Dict with 'graph' (SemanticRepositoryGraph),
            'index' (index metadata), or None if not found.
        """
        from app.code_intelligence.semantic_graph import (
            ConfidenceLevel,
            GraphNode,
            RelationshipType,
            SemanticRepositoryGraph,
        )

        async def _impl(session: AsyncSession):
            # ── 1. Resolve which index to load ──
            if index_id:
                stmt = select(RepositoryIndexModel).where(
                    RepositoryIndexModel.index_id == index_id,
                    RepositoryIndexModel.status == "active",
                )
            elif repository_id:
                # Get most recent active index for this repository
                stmt = (
                    select(RepositoryIndexModel)
                    .where(
                        RepositoryIndexModel.repository_id == repository_id,
                        RepositoryIndexModel.status == "active",
                    )
                    .order_by(RepositoryIndexModel.created_at.desc())
                    .limit(1)
                )
            else:
                return None

            result = await session.execute(stmt)
            index_model = result.scalar_one_or_none()
            if index_model is None:
                return None

            resolved_index_id = index_model.index_id

            # ── 2. Load symbols ──
            sym_stmt = select(CodeSymbolModel).where(
                CodeSymbolModel.index_id == resolved_index_id
            )
            sym_result = await session.execute(sym_stmt)
            symbol_models = sym_result.scalars().all()

            # ── 3. Load relationships ──
            rel_stmt = select(CodeRelationshipModel).where(
                CodeRelationshipModel.index_id == resolved_index_id
            )
            rel_result = await session.execute(rel_stmt)
            relationship_models = rel_result.scalars().all()

            # ── 4. Reconstruct graph ──
            graph = SemanticRepositoryGraph()

            for sm in symbol_models:
                node = GraphNode(
                    id=sm.symbol_id,
                    name=sm.name,
                    qualified_name=sm.qualified_name,
                    kind=sm.kind,
                    file_path=sm.file_path,
                    language=sm.language or "",
                    start_line=sm.start_line or 0,
                    end_line=sm.end_line or 0,
                    parent_id=sm.parent_symbol_id,
                    signature=sm.signature,
                    docstring=sm.docstring,
                    metadata=sm.metadata_json or {},
                )
                graph.add_node(node)

            for rm in relationship_models:
                try:
                    graph.add_edge(
                        source_id=rm.source_symbol_id,
                        target_id=rm.target_symbol_id,
                        relationship=RelationshipType(rm.relationship),
                        confidence=ConfidenceLevel(rm.confidence),
                        source_lines=rm.source_lines,
                        resolution_detail=rm.resolution_detail,
                        weight=rm.weight or 1.0,
                        metadata=rm.metadata_json or {},
                    )
                except (ValueError, KeyError) as exc:
                    logger.warning(
                        "Skipping edge %s -> %s [%s]: %s",
                        rm.source_symbol_id, rm.target_symbol_id,
                        rm.relationship, exc,
                    )

            return {
                "graph": graph,
                "index": {
                    "index_id": index_model.index_id,
                    "repository_id": index_model.repository_id,
                    "repository_path": index_model.repository_path,
                    "content_fingerprint": index_model.content_fingerprint,
                    "language_coverage": index_model.language_coverage,
                    "symbol_count": index_model.symbol_count,
                    "relationship_count": index_model.relationship_count,
                    "file_count": index_model.file_count,
                    "status": index_model.status,
                    "version": index_model.version,
                    "created_at": _format_dt(index_model.created_at),
                    "updated_at": _format_dt(index_model.updated_at),
                },
            }

        return await self._with_session(_impl)

    async def delete_graph(self, index_id: str) -> bool:
        """Remove a graph index and its symbols/relationships."""

        async def _impl(session: AsyncSession):
            stmt = select(RepositoryIndexModel).where(
                RepositoryIndexModel.index_id == index_id
            )
            result = await session.execute(stmt)
            index_model = result.scalar_one_or_none()
            if index_model is None:
                return False

            await session.delete(index_model)
            await session.execute(
                delete(CodeSymbolModel).where(
                    CodeSymbolModel.index_id == index_id
                )
            )
            await session.execute(
                delete(CodeRelationshipModel).where(
                    CodeRelationshipModel.index_id == index_id
                )
            )
            await session.commit()
            return True

        return await self._with_session(_impl)

    async def list_graph_indexes(
        self,
        repository_id: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """List graph index metadata."""

        async def _impl(session: AsyncSession):
            stmt = select(RepositoryIndexModel)
            if repository_id:
                stmt = stmt.where(
                    RepositoryIndexModel.repository_id == repository_id
                )
            stmt = stmt.order_by(
                RepositoryIndexModel.created_at.desc()
            ).offset(offset).limit(limit)

            result = await session.execute(stmt)
            models = result.scalars().all()

            return [
                {
                    "index_id": m.index_id,
                    "repository_id": m.repository_id,
                    "repository_path": m.repository_path,
                    "content_fingerprint": m.content_fingerprint,
                    "symbol_count": m.symbol_count,
                    "relationship_count": m.relationship_count,
                    "file_count": m.file_count,
                    "status": m.status,
                    "version": m.version,
                    "created_at": _format_dt(m.created_at),
                    "updated_at": _format_dt(m.updated_at),
                }
                for m in models
            ]

        return await self._with_session(_impl)
