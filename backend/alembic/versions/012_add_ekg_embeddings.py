"""012_add_ekg_embeddings

Phase 19 — EKG semantic embeddings (pgvector, guarded).

Adds one table for persisting node embeddings so semantic retrieval
survives restart on deployments that have the pgvector extension:

- ekg_embeddings — node_id -> embedding vector + model metadata

Graceful degradation (mirrors 005): the `vector` extension is optional.
If it is not available on this deployment, the table is skipped so
`alembic upgrade head` still succeeds everywhere (the in-memory semantic
index in EngineeringKnowledgeGraphService remains authoritative).

The embedding column is a REAL vector(256) column (raw DDL) so the
`<=>` cosine operator works directly — unlike migration 005's ARRAY
column. Dimension 256 matches settings.EMBEDDING_DIMENSION's default;
deployments that configure a different EMBEDDING_DIMENSION must edit
this DDL accordingly (the in-memory index is dimension-agnostic and
remains authoritative either way).

Revision ID: 012
Revises: 011
Create Date: 2026-08-02
"""

from typing import Optional

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "012"
down_revision = "011"
branch_labels: Optional[str] = None
depends_on: Optional[str] = None


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
        # pgvector not installed — semantic persistence disabled.
        # The revision is still recorded so the graph stays linear.
        return

    bind.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
    bind.execute(
        sa.text(
            """
            CREATE TABLE ekg_embeddings (
                id         SERIAL PRIMARY KEY,
                node_id    VARCHAR(40) NOT NULL,
                embedding  vector(256) NOT NULL,
                model      VARCHAR(64) NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT uq_ekg_embeddings_node_id UNIQUE (node_id)
            )
            """
        )
    )
    op.create_index(
        "idx_ekg_embeddings_node_id",
        "ekg_embeddings", ["node_id"],
    )


def downgrade() -> None:
    # DROP TABLE IF EXISTS does not require the vector extension — drop
    # unconditionally so an extension uninstall between upgrade/downgrade
    # cannot leave a stray table behind.
    op.execute(sa.text("DROP TABLE IF EXISTS ekg_embeddings"))
