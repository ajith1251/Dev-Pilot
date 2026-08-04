"""Add Phase 12 code intelligence tables (code_symbols, code_relationships, repository_indexes)

Revision ID: 003
Revises: 002
Create Date: 2026-07-30 10:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Code Symbols Table ─────────────────────────────────────
    op.create_table(
        "code_symbols",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("symbol_id", sa.String(255), nullable=False, comment="Deterministic symbol ID (file::qualified_name)"),
        sa.Column("name", sa.String(255), nullable=False, comment="Short symbol name"),
        sa.Column("qualified_name", sa.String(500), nullable=False, comment="Fully qualified name"),
        sa.Column("kind", sa.String(50), nullable=False, comment="Symbol kind: class, function, method, etc."),
        sa.Column("file_path", sa.String(1024), nullable=False, comment="Repository-relative path"),
        sa.Column("language", sa.String(50), nullable=True),
        sa.Column("signature", sa.String(500), nullable=True),
        sa.Column("docstring", sa.String(500), nullable=True),
        sa.Column("start_line", sa.Integer(), nullable=True),
        sa.Column("end_line", sa.Integer(), nullable=True),
        sa.Column("parent_symbol_id", sa.String(255), nullable=True),
        sa.Column("metadata_json", JSONB(), nullable=True),
        sa.Column("repository_id", sa.String(255), nullable=False, comment="Repository identifier"),
        sa.Column("index_id", sa.String(64), nullable=False, comment="Index batch identifier"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_cs_symbol_id", "code_symbols", ["symbol_id"], unique=False)
    op.create_index("idx_cs_repository_id", "code_symbols", ["repository_id"], unique=False)
    op.create_index("idx_cs_index_id", "code_symbols", ["index_id"], unique=False)
    op.create_index("idx_cs_file_path", "code_symbols", ["file_path"], unique=False)
    op.create_index("idx_cs_kind", "code_symbols", ["kind"], unique=False)

    # ── Code Relationships Table ───────────────────────────────
    op.create_table(
        "code_relationships",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source_symbol_id", sa.String(255), nullable=False, comment="Source symbol ID"),
        sa.Column("target_symbol_id", sa.String(255), nullable=False, comment="Target symbol ID"),
        sa.Column("relationship", sa.String(50), nullable=False, comment="Relationship type: calls, imports, inherits, etc."),
        sa.Column("confidence", sa.String(20), nullable=False, server_default="medium", comment="exact, high, medium, unresolved"),
        sa.Column("source_lines", sa.dialects.postgresql.ARRAY(sa.Integer()), nullable=True, comment="Evidence line numbers"),
        sa.Column("resolution_detail", sa.String(500), nullable=True, comment="How the relationship was resolved"),
        sa.Column("weight", sa.Float(), nullable=True, server_default=sa.text("1.0")),
        sa.Column("metadata_json", JSONB(), nullable=True),
        sa.Column("repository_id", sa.String(255), nullable=False, comment="Repository identifier"),
        sa.Column("index_id", sa.String(64), nullable=False, comment="Index batch identifier"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_cr_source", "code_relationships", ["source_symbol_id"], unique=False)
    op.create_index("idx_cr_target", "code_relationships", ["target_symbol_id"], unique=False)
    op.create_index("idx_cr_relationship", "code_relationships", ["relationship"], unique=False)
    op.create_index("idx_cr_repository_id", "code_relationships", ["repository_id"], unique=False)
    op.create_index("idx_cr_index_id", "code_relationships", ["index_id"], unique=False)

    # ── Repository Indexes Table ───────────────────────────────
    op.create_table(
        "repository_indexes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("index_id", sa.String(64), nullable=False, unique=True, comment="Unique index batch identifier"),
        sa.Column("repository_id", sa.String(255), nullable=False, comment="Repository identifier (path name)"),
        sa.Column("repository_path", sa.String(1024), nullable=False, comment="Absolute path to repository"),
        sa.Column("content_fingerprint", sa.String(64), nullable=True, comment="Fingerprint of indexed content"),
        sa.Column("language_coverage", JSONB(), nullable=True, comment="Languages and file counts"),
        sa.Column("symbol_count", sa.Integer(), nullable=True, server_default=sa.text("0")),
        sa.Column("relationship_count", sa.Integer(), nullable=True, server_default=sa.text("0")),
        sa.Column("file_count", sa.Integer(), nullable=True, server_default=sa.text("0")),
        sa.Column("status", sa.String(20), nullable=False, server_default="active", comment="active, stale, rebuilding"),
        sa.Column("version", sa.String(10), nullable=True, server_default=sa.text("'12.0'"), comment="Phase version"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_ri_repository_id", "repository_indexes", ["repository_id"], unique=False)
    op.create_index("idx_ri_status", "repository_indexes", ["status"], unique=False)


def downgrade() -> None:
    op.drop_table("repository_indexes")
    op.drop_table("code_relationships")
    op.drop_table("code_symbols")
