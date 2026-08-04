"""Add run context JSONB column for durable run state

Revision ID: 008
Revises: 007
Create Date: 2026-08-01 12:00:00.000000

Adds `context_json` to the `runs` table so PostgresRunStore can round-trip
the run's context payload — repository_profile, requirements, plan,
retrieved_context, and the patch/test/repair/review/gate outputs — that the
Phase 11 schema did not persist. Without it, execute_run's store re-hydration
drops the autonomy controller's pre-populated context and the strict state
machine rejects the first real transition.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column("context_json", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("runs", "context_json")
