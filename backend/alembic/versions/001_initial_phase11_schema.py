"""Initial Phase 11 schema — runs, tasks, repositories, stage_results, run_events, artifacts

Revision ID: 001
Revises:
Create Date: 2026-07-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Tasks table ─────────────────────────────────────────────
    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("github_issue_number", sa.Integer(), nullable=True),
        sa.Column("github_issue_url", sa.String(1024), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # ── Repositories table ──────────────────────────────────────
    op.create_table(
        "repositories",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False, server_default="local"),
        sa.Column("repository_url", sa.String(1024), nullable=True),
        sa.Column("repository_owner", sa.String(255), nullable=True),
        sa.Column("repository_name", sa.String(255), nullable=True),
        sa.Column("local_reference", sa.String(1024), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # ── Runs table ──────────────────────────────────────────────
    op.create_table(
        "runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(32), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("current_stage", sa.String(64), nullable=False),
        sa.Column("source_title", sa.String(500), nullable=False, server_default=""),
        sa.Column("source_description", sa.Text(), nullable=True),
        sa.Column("source_repository_path", sa.String(1024), nullable=True),
        sa.Column("source_issue_number", sa.Integer(), nullable=True),
        sa.Column("source_issue_url", sa.String(1024), nullable=True),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id"), nullable=True),
        sa.Column(
            "repository_id",
            sa.Integer(),
            sa.ForeignKey("repositories.id"),
            nullable=True,
        ),
        sa.Column("stage_results_data", JSONB(), nullable=True),
        sa.Column("events_data", JSONB(), nullable=True),
        sa.Column("warnings_list", JSONB(), nullable=True),
        sa.Column("failure_data", JSONB(), nullable=True),
        sa.Column("artifact_references", JSONB(), nullable=True),
        sa.Column(
            "cancellation_requested",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_duration_ms", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_runs_run_id", "runs", ["run_id"], unique=True)
    op.create_index("ix_runs_status", "runs", ["status"])

    # ── Stage Results table ─────────────────────────────────────
    op.create_table(
        "stage_results",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id_fk", sa.Integer(), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stage", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.String(500), nullable=True),
        sa.Column("warnings", JSONB(), nullable=True),
        sa.Column("metadata_json", JSONB(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_stage_results_run_stage", "stage_results", ["run_id_fk", "stage"])

    # ── Run Events table ────────────────────────────────────────
    op.create_table(
        "run_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.String(36), nullable=False),
        sa.Column("run_id_fk", sa.Integer(), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("stage", sa.String(64), nullable=True),
        sa.Column("message", sa.String(500), nullable=False, server_default=""),
        sa.Column("metadata_json", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_run_events_run_sequence", "run_events", ["run_id_fk", "sequence"], unique=True
    )
    op.create_index("idx_run_events_run_type", "run_events", ["run_id_fk", "event_type"])
    op.create_index("ix_run_events_event_id", "run_events", ["event_id"])

    # ── Artifacts table ─────────────────────────────────────────
    op.create_table(
        "artifacts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("artifact_id", sa.String(36), nullable=False),
        sa.Column("run_id_fk", sa.Integer(), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("artifact_type", sa.String(64), nullable=False),
        sa.Column("stage", sa.String(64), nullable=True),
        sa.Column("storage_type", sa.String(32), nullable=False, server_default="jsonb"),
        sa.Column("content", JSONB(), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("metadata_json", JSONB(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_artifacts_artifact_id", "artifacts", ["artifact_id"])
    op.create_index("ix_artifacts_artifact_type", "artifacts", ["artifact_type"])
    op.create_index("idx_artifacts_run_type", "artifacts", ["run_id_fk", "artifact_type"])


def downgrade() -> None:
    op.drop_table("artifacts")
    op.drop_table("run_events")
    op.drop_table("stage_results")
    op.drop_table("runs")
    op.drop_table("repositories")
    op.drop_table("tasks")
