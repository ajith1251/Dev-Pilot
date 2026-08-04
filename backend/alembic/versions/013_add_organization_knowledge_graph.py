"""013_add_organization_knowledge_graph

Phase 19A — Organization-wide knowledge graph (repository namespaces +
cross-repository edges + organization metadata).

Adds:
- ekg_organizations            — organization-level metadata (multi-tenant ready)
- ekg_repository_namespaces    — registered repository namespace registry
- ekg_cross_repository_edges   — explicit, deterministic cross-repository edges
                                  (the ONLY bridges that let retrieval cross a
                                  repository boundary)

Also extends the Phase 18 tables with repository_id so a single shared
PostgreSQL schema can hold nodes from many repositories without key collisions:
- ekg_nodes.repository_id  (String(64), indexed, default 'default')
- ekg_edges.repository_id  (String(64), indexed, default 'default')

Backward compatible: existing Phase 18 rows get repository_id='default' so
EngineeringKnowledgeGraphService.recover() continues to load them exactly as
before. New per-repository graphs stamp their own repository_id on writes.

Revision ID: 013
Revises: 012
Create Date: 2026-08-03
"""

from typing import Optional

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "013"
down_revision = "012"
branch_labels: Optional[str] = None
depends_on: Optional[str] = None


def upgrade() -> None:
    bind = op.get_bind()

    # ── Organization metadata ──────────────────────────────────────────
    op.create_table(
        "ekg_organizations",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "organization_id", sa.String(64),
            unique=True, nullable=False,
        ),
        sa.Column(
            "name", sa.String(200),
            nullable=False, server_default="Organization Knowledge Graph",
        ),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "idx_ekg_organizations_organization_id",
        "ekg_organizations", ["organization_id"],
        unique=True,
    )

    # ── Repository namespace registry ──────────────────────────────────
    op.create_table(
        "ekg_repository_namespaces",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "repository_id", sa.String(64),
            nullable=False,
        ),
        sa.Column(
            "namespace_id", sa.String(64),
            nullable=False, server_default="",
        ),
        sa.Column(
            "organization_id", sa.String(64),
            nullable=False, server_default="default",
        ),
        sa.Column("name", sa.String(200), nullable=False, server_default=""),
        sa.Column(
            "path", sa.String(1024),
            nullable=False, server_default="",
        ),
        sa.Column(
            "source_type", sa.String(32),
            nullable=False, server_default="local",
        ),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("now()"),
        ),
    )
    op.create_unique_constraint(
        "uq_ekg_repo_ns_repository_id",
        "ekg_repository_namespaces", ["repository_id"],
    )
    op.create_index(
        "idx_ekg_repo_ns_organization",
        "ekg_repository_namespaces", ["organization_id"],
    )
    op.create_index(
        "idx_ekg_repo_ns_source_type",
        "ekg_repository_namespaces", ["source_type"],
    )

    # ── Cross-repository edges ─────────────────────────────────────────
    op.create_table(
        "ekg_cross_repository_edges",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "edge_id", sa.String(64),
            nullable=False,
        ),
        sa.Column(
            "source_repository_id", sa.String(64),
            nullable=False,
        ),
        sa.Column(
            "target_repository_id", sa.String(64),
            nullable=False,
        ),
        sa.Column(
            "relationship", sa.String(64),
            nullable=False,
        ),
        sa.Column(
            "weight", sa.Float,
            nullable=False, server_default=sa.text("1.0"),
        ),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("provenance", sa.JSON(), nullable=True),
        sa.Column(
            "graph_version", sa.Integer,
            nullable=False, server_default=sa.text("1"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("now()"),
        ),
    )
    op.create_unique_constraint(
        "uq_ekg_cross_edges_edge_id",
        "ekg_cross_repository_edges", ["edge_id"],
    )
    op.create_index(
        "idx_ekg_cross_edges_source_target_rel",
        "ekg_cross_repository_edges",
        ["source_repository_id", "target_repository_id", "relationship"],
    )

    # ── Extend Phase 18 tables with repository_id ─────────────────────
    # These columns are nullable during migration; existing rows get the
    # default 'default' so recover() loads them identically to pre-013.
    op.add_column(
        "ekg_nodes",
        sa.Column(
            "repository_id", sa.String(64),
            nullable=False, server_default="default",
        ),
    )
    op.create_index(
        "idx_ekg_nodes_repository_id",
        "ekg_nodes", ["repository_id"],
    )

    op.add_column(
        "ekg_edges",
        sa.Column(
            "repository_id", sa.String(64),
            nullable=False, server_default="default",
        ),
    )
    op.create_index(
        "idx_ekg_edges_repository_id",
        "ekg_edges", ["repository_id"],
    )


def downgrade() -> None:
    # Drop new indexes/constraints/tables first, then revert column additions.
    op.drop_index("idx_ekg_edges_repository_id", table_name="ekg_edges")
    op.drop_column("ekg_edges", "repository_id")

    op.drop_index("idx_ekg_nodes_repository_id", table_name="ekg_nodes")
    op.drop_column("ekg_nodes", "repository_id")

    op.drop_index(
        "idx_ekg_cross_edges_source_target_rel",
        table_name="ekg_cross_repository_edges",
    )
    op.drop_constraint(
        "uq_ekg_cross_edges_edge_id",
        "ekg_cross_repository_edges", type_="unique",
    )
    op.drop_table("ekg_cross_repository_edges")

    op.drop_index(
        "idx_ekg_repo_ns_source_type",
        table_name="ekg_repository_namespaces",
    )
    op.drop_index(
        "idx_ekg_repo_ns_organization",
        table_name="ekg_repository_namespaces",
    )
    op.drop_constraint(
        "uq_ekg_repo_ns_repository_id",
        "ekg_repository_namespaces", type_="unique",
    )
    op.drop_table("ekg_repository_namespaces")

    op.drop_index(
        "idx_ekg_organizations_organization_id",
        table_name="ekg_organizations",
    )
    op.drop_table("ekg_organizations")
