"""Add Phase 13 repository memories table (repository_memories)

Revision ID: 004
Revises: 003
Create Date: 2026-07-30 12:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "repository_memories",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "memory_id", sa.String(64), nullable=False, unique=True,
            comment="Unique memory identifier",
        ),
        sa.Column(
            "repository_id", sa.String(255), nullable=False,
            comment="Repository this memory belongs to",
        ),
        sa.Column(
            "memory_type", sa.String(32), nullable=False,
            comment="architecture, convention, successful_change, failed_approach, etc.",
        ),
        sa.Column(
            "status", sa.String(16), nullable=False, server_default="provisional",
            comment="verified, provisional, stale, invalid",
        ),
        sa.Column(
            "content", sa.Text(), nullable=False,
            comment="The memory content / knowledge statement",
        ),
        sa.Column(
            "confidence", sa.Float(), nullable=False, server_default="0.0",
            comment="Confidence score 0.0-1.0",
        ),
        sa.Column(
            "symbol_names", JSONB(), nullable=True,
            comment="Referenced symbol names",
        ),
        sa.Column(
            "file_paths", JSONB(), nullable=True,
            comment="Referenced file paths",
        ),
        sa.Column(
            "evidence", JSONB(), nullable=True,
            comment="Evidence backing this memory",
        ),
        sa.Column(
            "source_run_id", sa.String(32), nullable=True,
            comment="Run ID that created this memory",
        ),
        sa.Column(
            "tags", JSONB(), nullable=True,
            comment="Tags for categorization",
        ),
        sa.Column(
            "version", sa.Integer(), nullable=False, server_default="1",
            comment="Version number",
        ),
        sa.Column(
            "related_commit", sa.String(40), nullable=True,
            comment="Related commit hash",
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "last_used_at", sa.DateTime(timezone=True), nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_rm_memory_id", "repository_memories", ["memory_id"], unique=True)
    op.create_index("idx_rm_repository_id", "repository_memories", ["repository_id"])
    op.create_index("idx_rm_repository_type", "repository_memories", ["repository_id", "memory_type"])
    op.create_index("idx_rm_memory_type", "repository_memories", ["memory_type"])
    op.create_index("idx_rm_status", "repository_memories", ["status"])
    op.create_index("idx_rm_updated_at", "repository_memories", ["updated_at"])
    op.create_index("idx_rm_source_run_id", "repository_memories", ["source_run_id"])


def downgrade() -> None:
    op.drop_index("idx_rm_source_run_id")
    op.drop_index("idx_rm_updated_at")
    op.drop_index("idx_rm_status")
    op.drop_index("idx_rm_memory_type")
    op.drop_index("idx_rm_repository_type")
    op.drop_index("idx_rm_repository_id")
    op.drop_index("idx_rm_memory_id")
    op.drop_table("repository_memories")
