"""Add workspace_registry table for persistent workspace tracking

Revision ID: 002
Revises: 001
Create Date: 2026-07-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "workspace_registry",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("workspace_id", sa.String(64), nullable=False),
        sa.Column("source_repository", sa.String(1024), nullable=True),
        sa.Column("root_path", sa.String(1024), nullable=False),
        sa.Column("run_id", sa.String(32), nullable=True),
        sa.Column("fingerprint", sa.String(64), nullable=True),
        sa.Column("writable", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "workspace_type",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'coding'"),
        ),
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
    op.create_index(
        "ix_workspace_registry_workspace_id",
        "workspace_registry",
        ["workspace_id"],
        unique=True,
    )
    op.create_index(
        "ix_workspace_registry_run_id",
        "workspace_registry",
        ["run_id"],
    )


def downgrade() -> None:
    op.drop_table("workspace_registry")
