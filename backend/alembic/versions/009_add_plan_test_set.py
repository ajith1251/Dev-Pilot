"""Add impact-selected test set to plan versions

Revision ID: 009
Revises: 008
Create Date: 2026-08-01 12:00:00.000000

Adds `test_set` (JSONB) to the `plan_versions` table so each immutable plan
version carries the impact-analysis-selected test files that should target
it (Phase 12d / Phase 16 replanning). Persisting the selection makes it
survive a restart and lets `_plan_from_version` restore the targeting
strategy when continuing from a checkpoint.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "plan_versions",
        sa.Column("test_set", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("plan_versions", "test_set")
