"""Add Phase 17 collaborative reasoning tables

Revision ID: 010
Revises: 009
Create Date: 2026-08-01 13:00:00.000000

Persists durable collaborative reasoning state:
- evidence_consensus       — consensus records per engineering topic
- contradiction_records    — detected contradictions (claim vs deterministic)
- engineering_notebooks    — shared engineering notebook per run
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── evidence_consensus ───────────────────────────────────────
    op.create_table(
        "evidence_consensus",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("consensus_id", sa.String(32), nullable=False),
        sa.Column("run_id", sa.String(32), nullable=False),
        sa.Column("topic", sa.String(100), nullable=False),
        sa.Column("summary", sa.String(500), nullable=False, server_default=""),
        sa.Column("status", sa.String(24), nullable=False, server_default="unknown"),
        sa.Column("confidence_json", postgresql.JSONB(), nullable=True),
        sa.Column("supporting_evidence", postgresql.JSONB(), nullable=True),
        sa.Column("conflicting_evidence", postgresql.JSONB(), nullable=True),
        sa.Column("final_decision", sa.String(300), nullable=False, server_default=""),
        sa.Column("contributing_agents", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("consensus_id", name="uq_evidence_consensus_consensus_id"),
    )
    # NOTE: index names are schema-unique in PostgreSQL — migration 006
    # already owns idx_ec_run_id on evidence_conflicts, so evidence_consensus
    # uses the distinct idx_ecs_* prefix (evidence consensus).
    op.create_index("idx_ecs_run_id", "evidence_consensus", ["run_id"])
    op.create_index("idx_ecs_run_topic", "evidence_consensus", ["run_id", "topic"])

    # ── contradiction_records ────────────────────────────────────
    op.create_table(
        "contradiction_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("contradiction_id", sa.String(32), nullable=False),
        sa.Column("run_id", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False, server_default="unknown"),
        sa.Column("description", sa.String(300), nullable=False),
        sa.Column("claim_evidence", postgresql.JSONB(), nullable=True),
        sa.Column("deterministic_evidence", postgresql.JSONB(), nullable=True),
        sa.Column("resolution", sa.String(24), nullable=False, server_default="unresolved"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("contradiction_id", name="uq_contradiction_records_contradiction_id"),
    )
    op.create_index("idx_cdr_run_id", "contradiction_records", ["run_id"])
    op.create_index("idx_cdr_run_kind", "contradiction_records", ["run_id", "kind"])

    # ── engineering_notebooks ────────────────────────────────────
    op.create_table(
        "engineering_notebooks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("notebook_id", sa.String(32), nullable=False),
        sa.Column("run_id", sa.String(32), nullable=False),
        sa.Column("task", sa.String(500), nullable=False, server_default=""),
        sa.Column("accepted_decisions", postgresql.JSONB(), nullable=True),
        sa.Column("rejected_decisions", postgresql.JSONB(), nullable=True),
        sa.Column("conflicts", postgresql.JSONB(), nullable=True),
        sa.Column("resolved_conflicts", postgresql.JSONB(), nullable=True),
        sa.Column("consensus", postgresql.JSONB(), nullable=True),
        sa.Column("timeline", postgresql.JSONB(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("notebook_id", name="uq_engineering_notebooks_notebook_id"),
    )
    op.create_index("idx_en_run_id", "engineering_notebooks", ["run_id"])


def downgrade() -> None:
    op.drop_table("engineering_notebooks")
    op.drop_table("contradiction_records")
    op.drop_table("evidence_consensus")
