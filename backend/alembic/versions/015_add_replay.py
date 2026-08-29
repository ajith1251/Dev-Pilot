"""015_add_replay

Phase 21 — Run Replay & Deterministic Reproduction.

Adds:
- replay_manifests  — durable Replay Manifest per completed run (bounded
  JSONB payload + normalized run/fingerprint columns for querying)
- replay_runs       — durable record of every replay execution (mode,
  verdict, bounded checks JSONB)

Schema:
replay_manifests:
- manifest_id            String(32) unique — RPL-XXXXXXXX
- run_id                 String(32) indexed — owning run
- source_run_status      String(24)
- repository_path        String(1024)
- repository_fingerprint String(64) indexed
- manifest_json          JSONB — full bounded manifest payload
- version                Integer default 1
- created_at             DateTime(tz)

replay_runs:
- replay_id              String(32) unique — REP-XXXXXXXX
- run_id                 String(32) indexed
- mode                   String(16) — exact | deterministic | compare
- verdict                String(16) indexed — match | drift | invalid | incomplete
- checks                 JSONB — bounded check records
- summary                String(500)
- created_at             DateTime(tz)

Revision ID: 015
Revises: 014
Create Date: 2026-08-14
"""

from typing import Optional

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "015"
down_revision = "014"
branch_labels: Optional[str] = None
depends_on: Optional[str] = None


def upgrade() -> None:
    op.create_table(
        "replay_manifests",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("manifest_id", sa.String(32), nullable=False),
        sa.Column("run_id", sa.String(32), nullable=False),
        sa.Column("source_run_status", sa.String(24), nullable=False, server_default=""),
        sa.Column("repository_path", sa.String(1024), nullable=False, server_default=""),
        sa.Column("repository_fingerprint", sa.String(64), nullable=False, server_default=""),
        sa.Column("manifest_json", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("manifest_id", name="uq_replay_manifests_manifest_id"),
    )
    op.create_index(
        "idx_replay_manifests_run_id", "replay_manifests", ["run_id"],
    )
    op.create_index(
        "idx_replay_manifests_run_created",
        "replay_manifests", ["run_id", "created_at"],
    )
    op.create_index(
        "idx_replay_manifests_fingerprint",
        "replay_manifests", ["repository_fingerprint"],
    )

    op.create_table(
        "replay_runs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("replay_id", sa.String(32), nullable=False),
        sa.Column("run_id", sa.String(32), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False, server_default="exact"),
        sa.Column("verdict", sa.String(16), nullable=False, server_default="incomplete"),
        sa.Column("checks", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("summary", sa.String(500), nullable=False, server_default=""),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("replay_id", name="uq_replay_runs_replay_id"),
    )
    op.create_index("idx_replay_runs_run_id", "replay_runs", ["run_id"])
    op.create_index(
        "idx_replay_runs_run_created", "replay_runs", ["run_id", "created_at"],
    )
    op.create_index("idx_replay_runs_verdict", "replay_runs", ["verdict"])


def downgrade() -> None:
    op.drop_index("idx_replay_runs_verdict", table_name="replay_runs")
    op.drop_index("idx_replay_runs_run_created", table_name="replay_runs")
    op.drop_index("idx_replay_runs_run_id", table_name="replay_runs")
    op.drop_table("replay_runs")

    op.drop_index("idx_replay_manifests_fingerprint", table_name="replay_manifests")
    op.drop_index("idx_replay_manifests_run_created", table_name="replay_manifests")
    op.drop_index("idx_replay_manifests_run_id", table_name="replay_manifests")
    op.drop_table("replay_manifests")
