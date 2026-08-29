"""
Phase 11 — SQLAlchemy ORM Models for persistent DevPilot state.

Includes:
- runs table
- tasks table
- repositories table
- stage_results table
- run_events table
- artifacts table
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base for all DevPilot database models."""

    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_uuid() -> str:
    return str(uuid.uuid4())


# Phase 19A — default repository namespace for nodes/edges created without an
# explicit repository. Matches models.engineering_graph.DEFAULT_REPOSITORY_ID.
DEFAULT_REPOSITORY_ID_DB = "default"


# ── Runs Table ──────────────────────────────────────────────────


class RunModel(Base):
    """Persistent storage for DevPilotRun."""

    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        String(32), unique=True, nullable=False, index=True
    )
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    current_stage: Mapped[str] = mapped_column(String(64), nullable=False)

    # Source fields
    source_title: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    source_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_repository_path: Mapped[Optional[str]] = mapped_column(
        String(1024), nullable=True
    )
    source_issue_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    source_issue_url: Mapped[Optional[str]] = mapped_column(
        String(1024), nullable=True
    )

    # Context references
    task_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("tasks.id"), nullable=True
    )
    repository_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("repositories.id"), nullable=True
    )

    # Orchestration internals (serialized JSONB for bounded structured data)
    stage_results_data: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSONB, nullable=True
    )
    events_data: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    warnings_list: Mapped[Optional[List[str]]] = mapped_column(JSONB, nullable=True)
    failure_data: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    artifact_references: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSONB, nullable=True
    )
    # Phase 16/17: full run context round-trip (repository_profile, requirements,
    # plan, retrieved_context, patch/test/repair/review/gate outputs). Without it,
    # execute_run's store re-hydration drops the autonomy controller's
    # pre-populated context and the strict state machine rejects the transition.
    context_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSONB, nullable=True
    )

    # Cancellation
    cancellation_requested: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Version for optimistic concurrency
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    total_duration_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Relationships
    stage_results: Mapped[List["StageResultModel"]] = relationship(
        "StageResultModel",
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="StageResultModel.id",
    )
    events: Mapped[List["RunEventModel"]] = relationship(
        "RunEventModel",
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="RunEventModel.sequence",
    )

    def __repr__(self) -> str:
        return f"<RunModel {self.run_id} status={self.status}>"


# ── Tasks Table ─────────────────────────────────────────────────


class TaskModel(Base):
    """Persistent task identity."""

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    github_issue_number: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )
    github_issue_url: Mapped[Optional[str]] = mapped_column(
        String(1024), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    # Relationships
    runs: Mapped[List["RunModel"]] = relationship(
        "RunModel", backref="task", foreign_keys=[RunModel.task_id]
    )


# ── Repositories Table ──────────────────────────────────────────


class RepositoryModel(Base):
    """Persistent repository identity."""

    __tablename__ = "repositories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, default="local")
    repository_url: Mapped[Optional[str]] = mapped_column(
        String(1024), nullable=True
    )
    repository_owner: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    repository_name: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    local_reference: Mapped[Optional[str]] = mapped_column(
        String(1024), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    # Relationships
    runs: Mapped[List["RunModel"]] = relationship(
        "RunModel", backref="repository", foreign_keys=[RunModel.repository_id]
    )


# ── Stage Results Table ─────────────────────────────────────────


class StageResultModel(Base):
    """Persistent per-stage lifecycle record."""

    __tablename__ = "stage_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id_fk: Mapped[int] = mapped_column(
        Integer, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    stage: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(
        String(500), nullable=True
    )
    warnings: Mapped[Optional[List[str]]] = mapped_column(JSONB, nullable=True)
    metadata_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSONB, nullable=True
    )

    # Relationships
    run: Mapped["RunModel"] = relationship(
        "RunModel", back_populates="stage_results"
    )

    __table_args__ = (
        Index("idx_stage_results_run_stage", "run_id_fk", "stage"),
    )


# ── Run Events Table ────────────────────────────────────────────


class RunEventModel(Base):
    """Persistent orchestration event."""

    __tablename__ = "run_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(
        String(36), nullable=False, index=True
    )
    run_id_fk: Mapped[int] = mapped_column(
        Integer, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    stage: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    message: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    metadata_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSONB, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    # Relationships
    run: Mapped["RunModel"] = relationship(
        "RunModel", back_populates="events"
    )

    __table_args__ = (
        Index("idx_run_events_run_sequence", "run_id_fk", "sequence", unique=True),
        Index("idx_run_events_run_type", "run_id_fk", "event_type"),
    )


# ── Artifacts Table ─────────────────────────────────────────────


# ── Workspace Registry Table ────────────────────────────────────


class WorkspaceModel(Base):
    """Persistent workspace registry for coding and testing workspaces.

    Tracks workspaces across sessions so that TestingService and
    WorkspaceService can recover workspace paths after a backend restart.
    """

    __tablename__ = "workspace_registry"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    source_repository: Mapped[Optional[str]] = mapped_column(
        String(1024), nullable=True
    )
    root_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    run_id: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True, index=True
    )
    fingerprint: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True
    )
    writable: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    workspace_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="coding"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    def __repr__(self) -> str:
        return f"<WorkspaceModel {self.workspace_id} root={self.root_path}>"


class ArtifactType:
    """Artifact type constants."""

    PLAN = "plan"
    RETRIEVAL_CONTEXT = "retrieval_context"
    PATCH = "patch"
    TEST_RESULT = "test_result"
    REPAIR_RESULT = "repair_result"
    REVIEW_REPORT = "review_report"
    QUALITY_GATE_RESULT = "quality_gate_result"


class ArtifactModel(Base):
    """Persistent artifact metadata."""

    __tablename__ = "artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    artifact_id: Mapped[str] = mapped_column(
        String(36), nullable=False, index=True
    )
    run_id_fk: Mapped[int] = mapped_column(
        Integer, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    artifact_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    stage: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    storage_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="jsonb"
    )
    content: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    content_hash: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True
    )
    size_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    metadata_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSONB, nullable=True
    )

    __table_args__ = (
        Index("idx_artifacts_run_type", "run_id_fk", "artifact_type"),
    )


# ── Code Intelligence Tables ────────────────────────────────────


class CodeSymbolModel(Base):
    """Persistent storage for code intelligence symbols.

    Maps to the 'code_symbols' table created in migration 003.
    """

    __tablename__ = "code_symbols"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol_id: Mapped[str] = mapped_column(
        String(255), nullable=False, index=True, comment="Deterministic symbol ID (file::qualified_name)"
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, comment="Short symbol name")
    qualified_name: Mapped[str] = mapped_column(
        String(500), nullable=False, comment="Fully qualified name"
    )
    kind: Mapped[str] = mapped_column(
        String(50), nullable=False, comment="Symbol kind: class, function, method, etc."
    )
    file_path: Mapped[str] = mapped_column(
        String(1024), nullable=False, comment="Repository-relative path"
    )
    language: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    signature: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    docstring: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    start_line: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    end_line: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    parent_symbol_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, comment="Parent symbol ID for hierarchy"
    )
    metadata_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    repository_id: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="Repository identifier"
    )
    index_id: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="Index batch identifier"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("idx_cs_symbol_id", "symbol_id"),
        Index("idx_cs_repository_id", "repository_id"),
        Index("idx_cs_index_id", "index_id"),
        Index("idx_cs_file_path", "file_path"),
        Index("idx_cs_kind", "kind"),
    )

    def __repr__(self) -> str:
        return f"<CodeSymbolModel {self.symbol_id} kind={self.kind}>"


class CodeRelationshipModel(Base):
    """Persistent storage for code intelligence relationships/edges.

    Maps to the 'code_relationships' table created in migration 003.
    """

    __tablename__ = "code_relationships"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_symbol_id: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="Source symbol ID"
    )
    target_symbol_id: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="Target symbol ID"
    )
    relationship: Mapped[str] = mapped_column(
        String(50), nullable=False, comment="Relationship type: calls, imports, inherits, etc."
    )
    confidence: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="medium",
        comment="exact, high, medium, unresolved"
    )
    source_lines: Mapped[Optional[List[int]]] = mapped_column(
        JSONB, nullable=True, comment="Evidence line numbers"
    )
    resolution_detail: Mapped[Optional[str]] = mapped_column(
        String(500), nullable=True, comment="How the relationship was resolved"
    )
    weight: Mapped[Optional[float]] = mapped_column(Float, nullable=True, server_default="1.0")
    metadata_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    repository_id: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="Repository identifier"
    )
    index_id: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="Index batch identifier"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("idx_cr_source", "source_symbol_id"),
        Index("idx_cr_target", "target_symbol_id"),
        Index("idx_cr_relationship", "relationship"),
        Index("idx_cr_repository_id", "repository_id"),
        Index("idx_cr_index_id", "index_id"),
    )

    def __repr__(self) -> str:
        return f"<CodeRelationshipModel {self.source_symbol_id} -> {self.target_symbol_id} [{self.relationship}]>"


class RepositoryIndexModel(Base):
    """Persistent metadata about a completed repository index.

    Maps to the 'repository_indexes' table created in migration 003.
    """

    __tablename__ = "repository_indexes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    index_id: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, comment="Unique index batch identifier"
    )
    repository_id: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="Repository identifier (path name)"
    )
    repository_path: Mapped[str] = mapped_column(
        String(1024), nullable=False, comment="Absolute path to repository"
    )
    content_fingerprint: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, comment="Fingerprint of indexed content"
    )
    language_coverage: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSONB, nullable=True, comment="Languages and file counts"
    )
    symbol_count: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, server_default="0"
    )
    relationship_count: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, server_default="0"
    )
    file_count: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, server_default="0"
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="active",
        comment="active, stale, rebuilding"
    )
    version: Mapped[Optional[str]] = mapped_column(
        String(10), nullable=True, server_default="'12.0'", comment="Phase version"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("idx_ri_repository_id", "repository_id"),
        Index("idx_ri_status", "status"),
    )

    def __repr__(self) -> str:
        return f"<RepositoryIndexModel {self.index_id} repo={self.repository_id} status={self.status}>"


# ── Phase 13: Repository Memory Table ───────────────────────────


class RepositoryMemoryModel(Base):
    """Persistent storage for repository knowledge memory.

    Maps to the 'repository_memories' table created in migration 004.
    """

    __tablename__ = "repository_memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    memory_id: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True,
        comment="Unique memory identifier"
    )
    repository_id: Mapped[str] = mapped_column(
        String(255), nullable=False, index=True,
        comment="Repository this memory belongs to"
    )
    memory_type: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True,
        comment="architecture, convention, successful_change, failed_approach, etc."
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, index=True, server_default="provisional",
        comment="verified, provisional, stale, invalid"
    )
    content: Mapped[str] = mapped_column(
        Text, nullable=False, comment="The memory content / knowledge statement"
    )
    confidence: Mapped[float] = mapped_column(
        Float, nullable=False, server_default="0.0",
        comment="Confidence score 0.0-1.0"
    )

    # Associated symbols and files (JSONB for indexed access)
    symbol_names: Mapped[Optional[List[str]]] = mapped_column(
        JSONB, nullable=True, comment="Referenced symbol names"
    )
    file_paths: Mapped[Optional[List[str]]] = mapped_column(
        JSONB, nullable=True, comment="Referenced file paths"
    )

    # Evidence (JSONB)
    evidence: Mapped[Optional[List[Dict[str, Any]]]] = mapped_column(
        JSONB, nullable=True, comment="Evidence backing this memory"
    )

    # Source run
    source_run_id: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True, index=True, comment="Run ID that created this memory"
    )

    # Tags
    tags: Mapped[Optional[List[str]]] = mapped_column(
        JSONB, nullable=True, comment="Tags for categorization"
    )

    # Version
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1", comment="Version number"
    )
    related_commit: Mapped[Optional[str]] = mapped_column(
        String(40), nullable=True, comment="Related commit hash"
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    last_used_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("idx_rm_memory_id", "memory_id", unique=True),
        Index("idx_rm_repository_id", "repository_id"),
        Index("idx_rm_repository_type", "repository_id", "memory_type"),
        Index("idx_rm_memory_type", "memory_type"),
        Index("idx_rm_status", "status"),
        Index("idx_rm_updated_at", "updated_at"),
        Index("idx_rm_source_run_id", "source_run_id"),
    )

    def __repr__(self) -> str:
        return f"<RepositoryMemoryModel {self.memory_id} type={self.memory_type} status={self.status}>"


# ── Phase 15: Multi-Agent Collaboration Tables ──────────────────


class AgentHandoffModel(Base):
    """Persistent structured handoff between two agents.

    Maps to the 'agent_handoffs' table created in migration 006.
    """

    __tablename__ = "agent_handoffs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    handoff_id: Mapped[str] = mapped_column(
        String(32), unique=True, nullable=False, index=True
    )
    run_id: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True
    )
    from_agent: Mapped[str] = mapped_column(String(32), nullable=False)
    to_agent: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    stage: Mapped[str] = mapped_column(String(64), nullable=False)
    summary: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    decisions: Mapped[Optional[List[str]]] = mapped_column(JSONB, nullable=True)
    evidence_refs: Mapped[Optional[List[Dict[str, Any]]]] = mapped_column(
        JSONB, nullable=True
    )
    artifact_refs: Mapped[Optional[List[str]]] = mapped_column(JSONB, nullable=True)
    affected_symbols: Mapped[Optional[List[str]]] = mapped_column(JSONB, nullable=True)
    warnings: Mapped[Optional[List[str]]] = mapped_column(JSONB, nullable=True)
    open_questions: Mapped[Optional[List[str]]] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="unverified"
    )
    validation: Mapped[Optional[Dict[str, str]]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    __table_args__ = (
        Index("idx_ah_run_id", "run_id"),
        Index("idx_ah_run_to_agent", "run_id", "to_agent"),
        Index("idx_ah_run_created", "run_id", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<AgentHandoffModel {self.handoff_id} {self.from_agent}->{self.to_agent}>"


class RunDecisionModel(Base):
    """Persistent engineering decision record.

    Maps to the 'run_decisions' table created in migration 006.
    """

    __tablename__ = "run_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    decision_id: Mapped[str] = mapped_column(
        String(32), unique=True, nullable=False, index=True
    )
    run_id: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True
    )
    decision_type: Mapped[str] = mapped_column(String(32), nullable=False)
    statement: Mapped[str] = mapped_column(String(300), nullable=False)
    made_by: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence_refs: Mapped[Optional[List[Dict[str, Any]]]] = mapped_column(
        JSONB, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    __table_args__ = (
        Index("idx_rd_run_id", "run_id"),
        Index("idx_rd_run_created", "run_id", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<RunDecisionModel {self.decision_id} {self.decision_type}>"


class EvidenceConflictModel(Base):
    """Persistent record of a detected evidence conflict.

    Maps to the 'evidence_conflicts' table created in migration 006.
    """

    __tablename__ = "evidence_conflicts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conflict_id: Mapped[str] = mapped_column(
        String(32), unique=True, nullable=False, index=True
    )
    run_id: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True
    )
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    claim_evidence: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    deterministic_evidence: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSONB, nullable=True
    )
    resolution: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="unresolved"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    __table_args__ = (
        Index("idx_ec_run_id", "run_id"),
        Index("idx_ec_run_created", "run_id", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<EvidenceConflictModel {self.conflict_id} resolution={self.resolution}>"


# ── Phase 17: Collaborative Reasoning Tables ────────────────────


class EvidenceConsensusModel(Base):
    """Persistent consensus record over one engineering topic.

    Maps to the 'evidence_consensus' table created in migration 010.
    """

    __tablename__ = "evidence_consensus"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    consensus_id: Mapped[str] = mapped_column(String(32), nullable=False)
    run_id: Mapped[str] = mapped_column(String(32), nullable=False)
    topic: Mapped[str] = mapped_column(String(100), nullable=False)
    summary: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default="unknown"
    )
    confidence_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    supporting_evidence: Mapped[Optional[List[Dict[str, Any]]]] = mapped_column(
        JSONB, nullable=True
    )
    conflicting_evidence: Mapped[Optional[List[Dict[str, Any]]]] = mapped_column(
        JSONB, nullable=True
    )
    final_decision: Mapped[str] = mapped_column(
        String(300), nullable=False, default=""
    )
    contributing_agents: Mapped[Optional[List[str]]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    __table_args__ = (
        # Named constraints/indexes match migration 010 exactly (no
        # model↔migration drift). idx_ecs_* prefix avoids the schema-unique
        # name collision with evidence_conflicts (006 owns idx_ec_run_id).
        UniqueConstraint("consensus_id", name="uq_evidence_consensus_consensus_id"),
        Index("idx_ecs_run_id", "run_id"),
        Index("idx_ecs_run_topic", "run_id", "topic"),
    )

    def __repr__(self) -> str:
        return f"<EvidenceConsensusModel {self.consensus_id} {self.topic} {self.status}>"


class ContradictionRecordModel(Base):
    """Persistent contradiction between evidence sources.

    Maps to the 'contradiction_records' table created in migration 010.
    Deterministic evidence always wins; never allow unsupported LLM claims
    to override deterministic evidence.
    """

    __tablename__ = "contradiction_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contradiction_id: Mapped[str] = mapped_column(String(32), nullable=False)
    run_id: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="unknown"
    )
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    claim_evidence: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    deterministic_evidence: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSONB, nullable=True
    )
    resolution: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default="unresolved"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("contradiction_id", name="uq_contradiction_records_contradiction_id"),
        Index("idx_cdr_run_id", "run_id"),
        Index("idx_cdr_run_kind", "run_id", "kind"),
    )

    def __repr__(self) -> str:
        return f"<ContradictionRecordModel {self.contradiction_id} {self.kind} {self.resolution}>"


class EngineeringNotebookModel(Base):
    """Persistent shared engineering notebook for a run.

    Maps to the 'engineering_notebooks' table created in migration 010.
    JSONB payloads are bounded by the reasoning model caps.
    """

    __tablename__ = "engineering_notebooks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    notebook_id: Mapped[str] = mapped_column(String(32), nullable=False)
    run_id: Mapped[str] = mapped_column(String(32), nullable=False)
    task: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    accepted_decisions: Mapped[Optional[List[Dict[str, Any]]]] = mapped_column(
        JSONB, nullable=True
    )
    rejected_decisions: Mapped[Optional[List[Dict[str, Any]]]] = mapped_column(
        JSONB, nullable=True
    )
    conflicts: Mapped[Optional[List[Dict[str, Any]]]] = mapped_column(JSONB, nullable=True)
    resolved_conflicts: Mapped[Optional[List[Dict[str, Any]]]] = mapped_column(
        JSONB, nullable=True
    )
    consensus: Mapped[Optional[List[Dict[str, Any]]]] = mapped_column(JSONB, nullable=True)
    timeline: Mapped[Optional[List[Dict[str, Any]]]] = mapped_column(JSONB, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("notebook_id", name="uq_engineering_notebooks_notebook_id"),
        Index("idx_en_run_id", "run_id"),
    )

    def __repr__(self) -> str:
        return f"<EngineeringNotebookModel {self.notebook_id} run={self.run_id}>"


# ── Phase 16: Autonomous Execution Tables ───────────────────────


class ExecutionGoalModel(Base):
    """Persistent autonomous execution goal.

    Maps to the 'execution_goals' table created in migration 007.
    Normalized relational fields + bounded JSONB metadata.
    """

    __tablename__ = "execution_goals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    goal_id: Mapped[str] = mapped_column(
        String(32), unique=True, nullable=False, index=True
    )
    task: Mapped[str] = mapped_column(String(500), nullable=False)
    repository: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    state: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default="running"
    )
    goal_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    budget_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    policy_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    scope_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1",
        comment="Optimistic-concurrency version"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    __table_args__ = (
        Index("idx_eg_state", "state"),
        Index("idx_eg_created", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<ExecutionGoalModel {self.goal_id} state={self.state}>"


class PlanVersionModel(Base):
    """Immutable persisted plan version history.

    Maps to the 'plan_versions' table created in migration 007.
    Previous versions are never overwritten.
    """

    __tablename__ = "plan_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    goal_id: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_summary: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    plan_objective: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    step_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="active"
    )
    superseded_reason: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    completed_steps: Mapped[Optional[List[str]]] = mapped_column(JSONB, nullable=True)
    remaining_criteria: Mapped[Optional[List[str]]] = mapped_column(JSONB, nullable=True)
    test_set: Mapped[Optional[List[str]]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    __table_args__ = (
        Index("idx_pv_goal_version", "goal_id", "version", unique=True),
    )

    def __repr__(self) -> str:
        return f"<PlanVersionModel {self.goal_id} v{self.version} status={self.status}>"


class AutonomousDecisionModel(Base):
    """Persistent autonomous controller decision.

    Maps to the 'autonomous_decisions' table created in migration 007.
    Stores action + reason code + bounded rationale — never chain-of-thought.
    """

    __tablename__ = "autonomous_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    goal_id: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True
    )
    iteration: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    rationale: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    evidence_refs: Mapped[Optional[List[Dict[str, Any]]]] = mapped_column(
        JSONB, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    __table_args__ = (
        Index("idx_ad_goal_iteration", "goal_id", "iteration"),
    )

    def __repr__(self) -> str:
        return f"<AutonomousDecisionModel {self.goal_id} i{self.iteration} {self.action}>"


class ExecutionCheckpointModel(Base):
    """Persistent autonomous checkpoint.

    Maps to the 'execution_checkpoints' table created in migration 007.
    A crash must not erase autonomous progress.
    """

    __tablename__ = "execution_checkpoints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    goal_id: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True
    )
    iteration: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="running")
    action: Mapped[str] = mapped_column(String(16), nullable=False, default="continue")
    reason_code: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    budget_usage: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    progress_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    evidence_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1"
    )
    persisted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    __table_args__ = (
        Index("idx_ecp_goal_iteration", "goal_id", "iteration"),
    )

    def __repr__(self) -> str:
        return f"<ExecutionCheckpointModel {self.goal_id} i{self.iteration} {self.action}>"


# ── Phase 18: Engineering Knowledge Graph Tables ────────────────


class EKNodeModel(Base):
    """Persistent engineering knowledge graph node.

    Maps to the 'ekg_nodes' table created in migration 011.
    Stores the graph entity with bounded JSONB payload + provenance.
    """

    __tablename__ = "ekg_nodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    node_id: Mapped[str] = mapped_column(
        String(40), nullable=False, comment="Unique EK node identifier"
    )
    node_type: Mapped[str] = mapped_column(
        String(50), nullable=False, comment="EKNodeType value"
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    qualified_name: Mapped[str] = mapped_column(
        String(500), nullable=False, default=""
    )
    kind: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    source_ref: Mapped[str] = mapped_column(
        String(200), nullable=False, default="",
        comment="Stable reference in the source store",
    )
    source_type: Mapped[str] = mapped_column(
        String(50), nullable=False, default="",
        comment="Source store: run | consensus | notebook | memory | ...",
    )
    payload: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    provenance: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="active"
    )
    graph_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1"
    )
    # Phase 19A — repository namespace (default 'default' so existing rows
    # and single-repo deployments are unaffected).
    repository_id: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=DEFAULT_REPOSITORY_ID_DB,
        comment="Repository namespace this node belongs to",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("node_id", name="uq_ekg_nodes_node_id"),
        UniqueConstraint(
            "node_id", "repository_id", name="uq_ekg_nodes_node_repo"
        ),
        Index("idx_ekg_node_type", "node_type"),
        Index("idx_ekg_node_repository", "repository_id"),
        Index("idx_ekg_node_source", "source_type", "source_ref"),
        Index("idx_ekg_node_name", "name"),
        Index("idx_ekg_node_version", "graph_version"),
    )

    def __repr__(self) -> str:
        return f"<EKNodeModel {self.node_id} {self.node_type} {self.name}>"


class EKEdgeModel(Base):
    """Persistent engineering knowledge graph edge.

    Maps to the 'ekg_edges' table created in migration 011.
    """

    __tablename__ = "ekg_edges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    edge_id: Mapped[str] = mapped_column(
        String(40), nullable=False, comment="Unique EK edge identifier"
    )
    source_id: Mapped[str] = mapped_column(
        String(40), nullable=False, comment="Source EK node_id"
    )
    target_id: Mapped[str] = mapped_column(
        String(40), nullable=False, comment="Target EK node_id"
    )
    relationship: Mapped[str] = mapped_column(
        String(40), nullable=False, comment="EKRelationshipType value"
    )
    weight: Mapped[float] = mapped_column(
        Float, nullable=False, server_default="1.0"
    )
    # NOTE: attribute is metadata_json, NOT metadata — SQLAlchemy reserves
    # `metadata` on declarative models (maps to the ekg_edges.metadata_json
    # JSONB column created in migration 011).
    metadata_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSONB, nullable=True
    )
    provenance: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    graph_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1"
    )
    # Phase 19A — repository namespace (in-repo edges inherit the source
    # node's namespace). Cross-repository edges live in their own table.
    repository_id: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=DEFAULT_REPOSITORY_ID_DB,
        comment="Repository namespace this edge belongs to",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("edge_id", name="uq_ekg_edges_edge_id"),
        UniqueConstraint(
            "edge_id", "repository_id", name="uq_ekg_edges_edge_repo"
        ),
        Index("idx_ekg_edge_source", "source_id"),
        Index("idx_ekg_edge_target", "target_id"),
        Index("idx_ekg_edge_repository", "repository_id"),
        Index("idx_ekg_edge_source_target_rel", "source_id", "target_id", "relationship"),
        Index("idx_ekg_edge_rel", "relationship"),
        Index("idx_ekg_edge_version", "graph_version"),
    )

    def __repr__(self) -> str:
        return f"<EKEdgeModel {self.edge_id} {self.source_id}->{self.target_id} [{self.relationship}]>"


class GraphVersionModel(Base):
    """Persistent engineering knowledge graph version record.

    Maps to the 'ekg_versions' table created in migration 011.
    """

    __tablename__ = "ekg_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="Monotonic graph version"
    )
    run_id: Mapped[str] = mapped_column(
        String(32), nullable=False, default="", index=True
    )
    summary: Mapped[str] = mapped_column(
        String(500), nullable=False, default=""
    )
    updated_nodes: Mapped[Optional[List[str]]] = mapped_column(JSONB, nullable=True)
    updated_edges: Mapped[Optional[List[str]]] = mapped_column(JSONB, nullable=True)
    superseded_node_ids: Mapped[Optional[List[str]]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    __table_args__ = (
        Index("idx_ekg_versions_version", "version", unique=True),
        Index("idx_ekg_versions_run", "run_id"),
    )

    def __repr__(self) -> str:
        return f"<GraphVersionModel v{self.version} run={self.run_id}>"


class HumanEscalationModel(Base):
    """Persistent structured human escalation.

    Maps to the 'human_escalations' table created in migration 007.
    """

    __tablename__ = "human_escalations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    escalation_id: Mapped[str] = mapped_column(
        String(32), unique=True, nullable=False, index=True
    )
    goal_id: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True
    )
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    what_happened: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    attempted: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    needed_input: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="open"
    )
    resolution: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("idx_he_goal_id", "goal_id"),
        Index("idx_he_status", "status"),
    )

    def __repr__(self) -> str:
        return f"<HumanEscalationModel {self.escalation_id} {self.reason} {self.status}>"


# ── Phase 19A: Organization Knowledge Graph Tables ───────────────


class EKOrganizationModel(Base):
    """Persistent metadata for an organization knowledge graph.

    A single DevPilot instance typically operates one organization
    ('default'); the schema is normalized so a future multi-tenant
    deployment can scope namespaces to an organization.
    """

    __tablename__ = "ekg_organizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True,
        comment="Organization identifier",
    )
    name: Mapped[str] = mapped_column(
        String(200), nullable=False, server_default="Organization Knowledge Graph"
    )
    metadata_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    def __repr__(self) -> str:
        return f"<EKOrganizationModel {self.organization_id} {self.name}>"


class EKRepositoryNamespaceModel(Base):
    """Persistent repository namespace registry (Phase 19A).

    Maps to the 'ekg_repository_namespaces' table created in migration
    013. Each registered repository owns its namespace; the org-level
    service uses this to enforce isolation.
    """

    __tablename__ = "ekg_repository_namespaces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repository_id: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True,
        comment="Stable repository identifier (namespace)",
    )
    namespace_id: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="",
        comment="Canonical namespace id (defaults to repository_id)",
    )
    organization_id: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="default",
        comment="Owning organization",
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    path: Mapped[str] = mapped_column(
        String(1024), nullable=False, server_default="", comment="Filesystem path"
    )
    source_type: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="local",
        comment="local | github | org | shared",
    )
    metadata_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "repository_id", name="uq_ekg_repo_ns_repository_id"
        ),
        Index("idx_ekg_repo_ns_organization", "organization_id"),
        Index("idx_ekg_repo_ns_source_type", "source_type"),
    )

    def __repr__(self) -> str:
        return (
            f"<EKRepositoryNamespaceModel {self.repository_id} "
            f"org={self.organization_id}>"
        )


class EKCrossRepositoryEdgeModel(Base):
    """Persistent cross-repository edge (Phase 19A).

    Maps to the 'ekg_cross_repository_edges' table created in migration
    013. These are the ONLY bridges that let retrieval cross a repository
    boundary — explicit, deterministic links recorded by the
    OrganizationKnowledgeGraphService.link_repositories() API.
    """

    __tablename__ = "ekg_cross_repository_edges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    edge_id: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True,
        comment="Unique edge identifier",
    )
    source_repository_id: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True,
        comment="Source repository namespace",
    )
    target_repository_id: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True,
        comment="Target repository namespace",
    )
    relationship: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="EKRelationshipType value"
    )
    weight: Mapped[float] = mapped_column(Float, nullable=False, server_default="1.0")
    metadata_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    provenance: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    graph_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("edge_id", name="uq_ekg_cross_edges_edge_id"),
        Index(
            "idx_ekg_cross_edges_source_target_rel",
            "source_repository_id", "target_repository_id", "relationship",
        ),
        Index("idx_ekg_cross_edges_rel", "relationship"),
        Index("idx_ekg_cross_edges_repo", "source_repository_id", "target_repository_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<EKCrossRepositoryEdgeModel {self.edge_id} "
            f"{self.source_repository_id}->{self.target_repository_id} "
            f"[{self.relationship}]>"
        )


# ── Phase 21: Run Replay & Deterministic Reproduction Tables ────


class ReplayManifestModel(Base):
    """Persistent Replay Manifest for a completed run.

    Maps to the 'replay_manifests' table created in migration 015.
    The full manifest payload is stored as JSONB (bounded by the replay
    model caps); the normalized columns enable list/query/fingerprint.
    """

    __tablename__ = "replay_manifests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    manifest_id: Mapped[str] = mapped_column(
        String(32), unique=True, nullable=False, index=True,
        comment="Unique manifest identifier (RPL-XXXXXXXX)",
    )
    run_id: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True, comment="Owning run"
    )
    source_run_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=""
    )
    repository_path: Mapped[str] = mapped_column(
        String(1024), nullable=False, default=""
    )
    repository_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False, default=""
    )
    manifest_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSONB, nullable=True
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("manifest_id", name="uq_replay_manifests_manifest_id"),
        Index("idx_replay_manifests_run_id", "run_id"),
        Index("idx_replay_manifests_run_created", "run_id", "created_at"),
        Index("idx_replay_manifests_fingerprint", "repository_fingerprint"),
    )

    def __repr__(self) -> str:
        return f"<ReplayManifestModel {self.manifest_id} run={self.run_id}>"


class ReplayRunModel(Base):
    """Persistent record of one replay execution.

    Maps to the 'replay_runs' table created in migration 015.
    Bounded checks JSONB; verdict/mode normalized for querying.
    """

    __tablename__ = "replay_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    replay_id: Mapped[str] = mapped_column(
        String(32), unique=True, nullable=False, index=True,
        comment="Unique replay identifier (REP-XXXXXXXX)",
    )
    run_id: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True, comment="Replayed run"
    )
    mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="exact"
    )
    verdict: Mapped[str] = mapped_column(
        String(16), nullable=False, default="incomplete", index=True
    )
    checks: Mapped[Optional[List[Dict[str, Any]]]] = mapped_column(
        JSONB, nullable=True
    )
    summary: Mapped[str] = mapped_column(
        String(500), nullable=False, default=""
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("replay_id", name="uq_replay_runs_replay_id"),
        Index("idx_replay_runs_run_id", "run_id"),
        Index("idx_replay_runs_run_created", "run_id", "created_at"),
        Index("idx_replay_runs_verdict", "verdict"),
    )

    def __repr__(self) -> str:
        return f"<ReplayRunModel {self.replay_id} run={self.run_id} {self.verdict}>"
