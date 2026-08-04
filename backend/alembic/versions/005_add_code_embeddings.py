"""Add Phase 15 (12d) code_embeddings table for pgvector persistence

Revision ID: 005
Revises: 004
Create Date: 2026-07-31 12:00:00.000000

Graceful degradation: the `vector` extension is optional. If it is not
available on this deployment, the table is skipped so `alembic upgrade
head` still succeeds everywhere (matching the in-memory fallback in
VectorStore).
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _vector_available(bind) -> bool:
    """Check whether the pgvector extension is available in this DB."""
    try:
        result = bind.execute(
            sa.text(
                "SELECT 1 FROM pg_available_extensions WHERE name = 'vector'"
            )
        ).fetchone()
        return result is not None
    except Exception:
        return False


def upgrade() -> None:
    bind = op.get_bind()

    if not _vector_available(bind):
        # pgvector not installed — vector persistence disabled.
        # The revision is still recorded so the graph stays linear.
        return

    bind.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))

    op.create_table(
        "code_embeddings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "repository_id", sa.String(255), nullable=False,
            comment="Repository this embedding belongs to",
        ),
        sa.Column(
            "index_id", sa.String(64), nullable=False,
            comment="Code intelligence index ID",
        ),
        sa.Column(
            "symbol_id", sa.String(512), nullable=False,
            comment="Graph symbol ID (file::qualified_name)",
        ),
        sa.Column(
            "embedding", sa.dialects.postgresql.ARRAY(sa.Float()),
            nullable=False, comment="Embedding vector (pgvector)",
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "repository_id", "index_id", "symbol_id",
            name="uq_code_embeddings_repo_index_symbol",
        ),
    )
    op.create_index(
        "idx_code_embeddings_repo",
        "code_embeddings", ["repository_id"],
    )
    op.create_index(
        "idx_code_embeddings_index",
        "code_embeddings", ["index_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    # Symmetric with the guarded upgrade: only drop when pgvector is
    # available (the table only exists if the extension was present).
    if _vector_available(bind):
        op.execute(sa.text("DROP TABLE IF EXISTS code_embeddings"))
