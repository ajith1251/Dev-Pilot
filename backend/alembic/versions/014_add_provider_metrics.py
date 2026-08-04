"""014_add_provider_metrics

Phase 19B — Multi-Provider Failover & Reliability Platform.

Adds:
- provider_metric_snapshots — point-in-time per-provider router metric
  snapshots so routing health is observable across restarts.

Schema:
- provider        String(32)  provider name (gemini/openai/anthropic/...)
- status          String(16)  healthy/degraded/unhealthy/unknown
- circuit_state   String(16)  closed/open/half_open
- total_requests / successful_requests / failed_requests  Integer
- retries / failovers                                      Integer
- avg_latency_ms  Float (nullable)
- success_rate    Float (nullable)
- recorded_at     DateTime(tz) indexed — snapshot time

Revision ID: 014
Revises: 013
Create Date: 2026-08-03
"""

from typing import Optional

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "014"
down_revision = "013"
branch_labels: Optional[str] = None
depends_on: Optional[str] = None


def upgrade() -> None:
    op.create_table(
        "provider_metric_snapshots",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("circuit_state", sa.String(16), nullable=False, server_default="closed"),
        sa.Column("total_requests", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("successful_requests", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("failed_requests", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("retries", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("failovers", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("avg_latency_ms", sa.Float, nullable=True),
        sa.Column("success_rate", sa.Float, nullable=True),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "idx_provider_metric_snapshots_provider_recorded",
        "provider_metric_snapshots",
        ["provider", "recorded_at"],
    )
    op.create_index(
        "idx_provider_metric_snapshots_recorded_at",
        "provider_metric_snapshots",
        ["recorded_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_provider_metric_snapshots_recorded_at",
        table_name="provider_metric_snapshots",
    )
    op.drop_index(
        "idx_provider_metric_snapshots_provider_recorded",
        table_name="provider_metric_snapshots",
    )
    op.drop_table("provider_metric_snapshots")
