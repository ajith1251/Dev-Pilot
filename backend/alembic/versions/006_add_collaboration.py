"""Add Phase 15 multi-agent collaboration tables

Revision ID: 006
Revises: 005
Create Date: 2026-07-31 14:00:00.000000

Persists durable collaboration state:
- agent_handoffs      — structured handoffs between agents
- run_decisions       — lightweight engineering decision records
- evidence_conflicts  — detected contradictions between evidence sources
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── agent_handoffs ───────────────────────────────────────────
    op.create_table(
        "agent_handoffs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("handoff_id", sa.String(32), nullable=False),
        sa.Column("run_id", sa.String(32), nullable=False),
        sa.Column("from_agent", sa.String(32), nullable=False),
        sa.Column("to_agent", sa.String(32), nullable=False),
        sa.Column("stage", sa.String(64), nullable=False),
        sa.Column("summary", sa.String(500), nullable=False, server_default=""),
        sa.Column("decisions", postgresql.JSONB(), nullable=True),
        sa.Column("evidence_refs", postgresql.JSONB(), nullable=True),
        sa.Column("artifact_refs", postgresql.JSONB(), nullable=True),
        sa.Column("affected_symbols", postgresql.JSONB(), nullable=True),
        sa.Column("warnings", postgresql.JSONB(), nullable=True),
        sa.Column("open_questions", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="unverified"),
        sa.Column("validation", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("handoff_id", name="uq_agent_handoffs_handoff_id"),
    )
    op.create_index("idx_ah_run_id", "agent_handoffs", ["run_id"])
    op.create_index("idx_ah_run_to_agent", "agent_handoffs", ["run_id", "to_agent"])
    op.create_index("idx_ah_run_created", "agent_handoffs", ["run_id", "created_at"])

    # ── run_decisions ────────────────────────────────────────────
    op.create_table(
        "run_decisions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("decision_id", sa.String(32), nullable=False),
        sa.Column("run_id", sa.String(32), nullable=False),
        sa.Column("decision_type", sa.String(32), nullable=False),
        sa.Column("statement", sa.String(300), nullable=False),
        sa.Column("made_by", sa.String(32), nullable=False),
        sa.Column("evidence_refs", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("decision_id", name="uq_run_decisions_decision_id"),
    )
    op.create_index("idx_rd_run_id", "run_decisions", ["run_id"])
    op.create_index("idx_rd_run_created", "run_decisions", ["run_id", "created_at"])

    # ── evidence_conflicts ───────────────────────────────────────
    op.create_table(
        "evidence_conflicts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("conflict_id", sa.String(32), nullable=False),
        sa.Column("run_id", sa.String(32), nullable=False),
        sa.Column("description", sa.String(300), nullable=False),
        sa.Column("claim_evidence", postgresql.JSONB(), nullable=True),
        sa.Column("deterministic_evidence", postgresql.JSONB(), nullable=True),
        sa.Column("resolution", sa.String(32), nullable=False, server_default="unresolved"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("conflict_id", name="uq_evidence_conflicts_conflict_id"),
    )
    op.create_index("idx_ec_run_id", "evidence_conflicts", ["run_id"])
    op.create_index("idx_ec_run_created", "evidence_conflicts", ["run_id", "created_at"])


def downgrade() -> None:
    op.drop_table("evidence_conflicts")
    op.drop_table("run_decisions")
    op.drop_table("agent_handoffs")
