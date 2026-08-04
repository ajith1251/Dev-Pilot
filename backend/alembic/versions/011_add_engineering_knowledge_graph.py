"""011_add_engineering_knowledge_graph

Phase 18 — Engineering Knowledge Graph (EKG).

Adds three normalized tables plus bounded JSONB metadata:
- ekg_nodes      — graph entities (code, requirements, plans, goals,
                   patches, tests, review, gate, evidence, consensus,
                   contradictions, notebook, decisions, runs, memory)
- ekg_edges      — typed, directed, temporal relationships
- ekg_versions   — incremental graph version records (never full rebuild)

Revision ID: 011
Revises: 010
Create Date: 2026-08-02

"""

from typing import Optional

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "011"
down_revision = "010"
branch_labels: Optional[str] = None
depends_on: Optional[str] = None


def upgrade() -> None:
    # ── ekg_nodes ──────────────────────────────────────────────
    op.create_table(
        "ekg_nodes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("node_id", sa.String(length=40), nullable=False),
        sa.Column("node_type", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("qualified_name", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("kind", sa.String(length=50), nullable=False, server_default=""),
        sa.Column("source_ref", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("source_type", sa.String(length=50), nullable=False, server_default=""),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        sa.Column("provenance", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("graph_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("node_id", name="uq_ekg_nodes_node_id"),
    )
    op.create_index("idx_ekg_node_type", "ekg_nodes", ["node_type"])
    op.create_index("idx_ekg_node_source", "ekg_nodes", ["source_type", "source_ref"])
    op.create_index("idx_ekg_node_name", "ekg_nodes", ["name"])
    op.create_index("idx_ekg_node_version", "ekg_nodes", ["graph_version"])

    # ── ekg_edges ──────────────────────────────────────────────
    op.create_table(
        "ekg_edges",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("edge_id", sa.String(length=40), nullable=False),
        sa.Column("source_id", sa.String(length=40), nullable=False),
        sa.Column("target_id", sa.String(length=40), nullable=False),
        sa.Column("relationship", sa.String(length=40), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False, server_default="1.0"),
        # Named metadata_json (NOT metadata): SQLAlchemy reserves `metadata`
        # on declarative models, and the ORM maps this column as
        # EKEdgeModel.metadata_json.
        sa.Column("metadata_json", postgresql.JSONB(), nullable=True),
        sa.Column("provenance", postgresql.JSONB(), nullable=True),
        sa.Column("graph_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("edge_id", name="uq_ekg_edges_edge_id"),
    )
    op.create_index("idx_ekg_edge_source", "ekg_edges", ["source_id"])
    op.create_index("idx_ekg_edge_target", "ekg_edges", ["target_id"])
    op.create_index(
        "idx_ekg_edge_source_target_rel",
        "ekg_edges",
        ["source_id", "target_id", "relationship"],
    )
    op.create_index("idx_ekg_edge_rel", "ekg_edges", ["relationship"])
    op.create_index("idx_ekg_edge_version", "ekg_edges", ["graph_version"])

    # ── ekg_versions ───────────────────────────────────────────
    op.create_table(
        "ekg_versions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("summary", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("updated_nodes", postgresql.JSONB(), nullable=True),
        sa.Column("updated_edges", postgresql.JSONB(), nullable=True),
        sa.Column("superseded_node_ids", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("idx_ekg_versions_version", "ekg_versions", ["version"], unique=True)
    op.create_index("idx_ekg_versions_run", "ekg_versions", ["run_id"])


def downgrade() -> None:
    op.drop_index("idx_ekg_versions_run", table_name="ekg_versions")
    op.drop_index("idx_ekg_versions_version", table_name="ekg_versions")
    op.drop_table("ekg_versions")

    op.drop_index("idx_ekg_edge_version", table_name="ekg_edges")
    op.drop_index("idx_ekg_edge_rel", table_name="ekg_edges")
    op.drop_index("idx_ekg_edge_source_target_rel", table_name="ekg_edges")
    op.drop_index("idx_ekg_edge_target", table_name="ekg_edges")
    op.drop_index("idx_ekg_edge_source", table_name="ekg_edges")
    op.drop_table("ekg_edges")

    op.drop_index("idx_ekg_node_version", table_name="ekg_nodes")
    op.drop_index("idx_ekg_node_name", table_name="ekg_nodes")
    op.drop_index("idx_ekg_node_source", table_name="ekg_nodes")
    op.drop_index("idx_ekg_node_type", table_name="ekg_nodes")
    op.drop_table("ekg_nodes")
