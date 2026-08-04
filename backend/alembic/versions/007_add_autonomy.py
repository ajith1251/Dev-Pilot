"""Add Phase 16 autonomous execution tables

Revision ID: 007
Revises: 006
Create Date: 2026-08-01 12:00:00.000000

Persists durable autonomous execution state:
- execution_goals        — autonomous goals + criteria + budget + policy (JSONB)
- plan_versions          — immutable plan history (never overwritten)
- autonomous_decisions   — recorded controller decisions (no chain-of-thought)
- execution_checkpoints  — durable iteration checkpoints (crash-safe)
- human_escalations      — structured human input requests
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── execution_goals ──────────────────────────────────────────
    op.create_table(
        "execution_goals",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("goal_id", sa.String(32), nullable=False),
        sa.Column("task", sa.String(500), nullable=False),
        sa.Column("repository", sa.String(1024), nullable=True),
        sa.Column("state", sa.String(24), nullable=False, server_default="running"),
        sa.Column("goal_json", postgresql.JSONB(), nullable=True),
        sa.Column("budget_json", postgresql.JSONB(), nullable=True),
        sa.Column("policy_json", postgresql.JSONB(), nullable=True),
        sa.Column("scope_json", postgresql.JSONB(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("goal_id", name="uq_execution_goals_goal_id"),
    )
    op.create_index("idx_eg_state", "execution_goals", ["state"])
    op.create_index("idx_eg_created", "execution_goals", ["created_at"])

    # ── plan_versions ────────────────────────────────────────────
    op.create_table(
        "plan_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("goal_id", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("plan_summary", sa.String(500), nullable=False, server_default=""),
        sa.Column("plan_objective", sa.String(500), nullable=False, server_default=""),
        sa.Column("step_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("superseded_reason", sa.String(300), nullable=True),
        sa.Column("completed_steps", postgresql.JSONB(), nullable=True),
        sa.Column("remaining_criteria", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("goal_id", "version", name="uq_plan_versions_goal_version"),
    )
    op.create_index("idx_pv_goal_id", "plan_versions", ["goal_id"])

    # ── autonomous_decisions ─────────────────────────────────────
    op.create_table(
        "autonomous_decisions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("goal_id", sa.String(32), nullable=False),
        sa.Column("iteration", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("reason_code", sa.String(100), nullable=False, server_default=""),
        sa.Column("rationale", sa.String(300), nullable=False, server_default=""),
        sa.Column("evidence_refs", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_ad_goal_id", "autonomous_decisions", ["goal_id"])
    op.create_index("idx_ad_goal_iteration", "autonomous_decisions", ["goal_id", "iteration"])

    # ── execution_checkpoints ────────────────────────────────────
    op.create_table(
        "execution_checkpoints",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("goal_id", sa.String(32), nullable=False),
        sa.Column("iteration", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("state", sa.String(24), nullable=False, server_default="running"),
        sa.Column("action", sa.String(16), nullable=False, server_default="continue"),
        sa.Column("reason_code", sa.String(100), nullable=False, server_default=""),
        sa.Column("plan_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("budget_usage", postgresql.JSONB(), nullable=True),
        sa.Column("progress_json", postgresql.JSONB(), nullable=True),
        sa.Column("evidence_json", postgresql.JSONB(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("persisted_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_ecp_goal_id", "execution_checkpoints", ["goal_id"])
    op.create_index("idx_ecp_goal_iteration", "execution_checkpoints", ["goal_id", "iteration"])

    # ── human_escalations ────────────────────────────────────────
    op.create_table(
        "human_escalations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("escalation_id", sa.String(32), nullable=False),
        sa.Column("goal_id", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(32), nullable=False),
        sa.Column("what_happened", sa.String(300), nullable=False, server_default=""),
        sa.Column("attempted", sa.String(300), nullable=False, server_default=""),
        sa.Column("needed_input", sa.String(300), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("resolution", sa.String(300), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("escalation_id", name="uq_human_escalations_escalation_id"),
    )
    op.create_index("idx_he_goal_id", "human_escalations", ["goal_id"])
    op.create_index("idx_he_status", "human_escalations", ["status"])


def downgrade() -> None:
    op.drop_table("human_escalations")
    op.drop_table("execution_checkpoints")
    op.drop_table("autonomous_decisions")
    op.drop_table("plan_versions")
    op.drop_table("execution_goals")
