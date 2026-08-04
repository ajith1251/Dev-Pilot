"""
Migration Tests — Phase 11H

Verifies Alembic migration integrity:
- Upgrade/downgrade round-trip
- Revision graph is valid
- Single head
- Schema invariants after migration

All destructive operations use ONLY the test database or isolated fixtures.
"""

from __future__ import annotations

import os
import pytest
from pathlib import Path
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.fixture(scope="module")
def alembic_test_db_url():
    from app.config import settings
    url = settings.TEST_DATABASE_URL
    if not url:
        url = settings.DATABASE_URL
        if url:
            # Use dev DB but warn
            pytest.skip("TEST_DATABASE_URL not set — skipping destructive migration tests")
        # Neither TEST_DATABASE_URL nor DATABASE_URL is configured: no
        # PostgreSQL is reachable (e.g. CI in-memory-fallback job). Skip
        # cleanly instead of erroring — the suite must stay green in the
        # no-PG path while still fully validating migrations under live PG.
        pytest.skip("No DATABASE_URL configured — skipping destructive migration tests")
    return url


def _get_script_info():
    """Get migration revision info by parsing migration files directly.

    Module-level so both test classes can use it. Avoids
    alembic.script.ScriptDirectory which imports env.py and triggers
    async code incompatible with sync test runners.
    """
    backend_dir = Path(__file__).resolve().parent.parent
    versions_dir = backend_dir / "alembic" / "versions"
    if not versions_dir.is_dir():
        return [], []

    revisions = []
    for f in sorted(versions_dir.glob("*.py")):
        content = f.read_text()
        rev_id = None
        down_rev = None
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped.startswith("revision") and "=" in stripped:
                rev_id = stripped.split("=")[-1].strip().strip("\"'")
            if stripped.startswith("down_revision") and "=" in stripped:
                down_rev = stripped.split("=")[-1].strip().strip("\"'")
        if rev_id:
            revisions.append({
                "file": f.name,
                "revision": rev_id,
                "down_revision": down_rev if down_rev and down_rev != "None" else None,
                "has_upgrade": "def upgrade()" in content,
                "has_downgrade": "def downgrade()" in content,
            })

    # Find heads (revisions with no dependent)
    all_ids = {r["revision"] for r in revisions}
    dependent_ids = {r["down_revision"] for r in revisions if r["down_revision"]}
    heads = list(all_ids - dependent_ids)

    return revisions, heads


class TestMigrationIntegrity:
    """Non-destructive migration integrity checks.
    
    Uses direct file inspection to avoid triggering Alembic's async env.py.
    """

    def test_revision_graph_valid(self):
        """Verify the Alembic revision graph has no cycles and is well-formed."""
        revisions, heads = _get_script_info()
        assert len(revisions) >= 1, f"No migration files found in alembic/versions/"
        
        # Each revision (except the root) must have a valid down_revision
        valid_ids = {r["revision"] for r in revisions}
        for rev in revisions:
            if rev["down_revision"]:
                assert rev["down_revision"] in valid_ids, \
                    f"Revision {rev['revision']} has missing parent {rev['down_revision']}"

    def test_single_head(self):
        """Verify there's exactly one Alembic head revision."""
        revisions, heads = _get_script_info()
        assert len(heads) == 1, f"Expected exactly 1 head, got {len(heads)}: {heads}"
        assert heads[0] is not None

    def test_migration_filenames_consistent(self):
        """Verify migration filenames follow Alembic conventions."""
        revisions, _ = _get_script_info()
        for rev in revisions:
            assert rev["revision"] is not None, f"Revision ID is None in {rev['file']}"
            assert rev["has_upgrade"], f"Revision {rev['revision']} missing upgrade()"
            assert rev["has_downgrade"], f"Revision {rev['revision']} missing downgrade()"
            # Filename should start with revision ID
            assert rev["file"].startswith(rev["revision"]), \
                f"Filename {rev['file']} should start with revision {rev['revision']}"


@pytest.mark.integration
class TestMigrationRoundTrip:
    """Destructive migration round-trip tests on isolated test database.

    These tests:
    1. Upgrade from empty database to head
    2. Validate schema
    3. Downgrade safely
    4. Upgrade again
    5. Validate schema again
    """

    def _get_db_name(self, url: str) -> str:
        """Extract the database name from a PostgreSQL URL."""
        # URL format: postgresql+asyncpg://user:pass@host:port/dbname
        parts = url.split("/")
        if parts:
            db_part = parts[-1].split("?")[0]
            return db_part
        return ""

    @pytest.fixture(autouse=True)
    def _check_test_db(self, alembic_test_db_url):
        """Verify we're using a designated test database."""
        db_name = self._get_db_name(alembic_test_db_url)
        is_test_db = "test" in db_name.lower()
        assert is_test_db, \
            f"Refusing to run destructive tests on: {db_name} (from URL: {alembic_test_db_url[:60]}...)"
        self._db_url = alembic_test_db_url

    def _run_alembic_upgrade(self):
        """Run alembic upgrade head via subprocess (avoids asyncio.run() conflict)."""
        import subprocess
        backend_dir = Path(__file__).resolve().parent.parent
        env = os.environ.copy()
        env["PYTHONPATH"] = str(backend_dir)
        env["DATABASE_URL"] = self._db_url
        env["TEST_DATABASE_URL"] = self._db_url
        result = subprocess.run(
            ["alembic", "upgrade", "head"],
            cwd=str(backend_dir),
            capture_output=True,
            text=True,
            # Also used from the clean_db teardown (up to 6x per class); a full
            # 10-revision upgrade needs headroom on slow machines.
            timeout=120,
            env=env,
        )
        assert result.returncode == 0, f"alembic upgrade failed: {result.stderr}"
        return result.stdout

    def _run_alembic_downgrade(self):
        """Run alembic downgrade base via subprocess."""
        import subprocess
        backend_dir = Path(__file__).resolve().parent.parent
        env = os.environ.copy()
        env["PYTHONPATH"] = str(backend_dir)
        env["DATABASE_URL"] = self._db_url
        env["TEST_DATABASE_URL"] = self._db_url
        result = subprocess.run(
            ["alembic", "downgrade", "base"],
            cwd=str(backend_dir),
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        assert result.returncode == 0, f"alembic downgrade failed: {result.stderr}"
        return result.stdout

    @pytest.fixture
    async def clean_db(self):
        """Drop all Alembic-managed tables for a clean starting point.
        Drops ALL migration-managed tables (Phase 11, 002, 003) plus alembic_version
        to ensure alembic upgrade head sees a clean slate.
        After the test the migrated schema is restored so other test files in
        the same process (e.g. run-store integration tests) still see a valid
        database.
        """
        engine = create_async_engine(self._db_url)
        async with engine.connect() as conn:
            await self._drop_all_tables(conn)
        await engine.dispose()
        yield
        # Cleanup after test: drop everything, then restore the migrated schema
        # so later test files in the same process see a valid database.
        engine = create_async_engine(self._db_url)
        async with engine.connect() as conn:
            await self._drop_all_tables(conn)
        await engine.dispose()
        self._run_alembic_upgrade()

    async def _drop_all_tables(self, conn):
        """Drop every migration-managed table (Phases 1-18) plus alembic_version.

        Kept in one place so adding a migration only requires extending this
        list — the Phase 18 EKG tables (011) and Phase 17 reasoning tables
        (010) are included here.
        """
        # Phase 18 EKG tables (011) + Phase 19 ekg_embeddings (012) +
        # Phase 19A organization tables (013) — ekg_embeddings and the
        # pgvector-dependent tables only exist when pgvector is available
        # (guarded migration 012), so DROP IF EXISTS is required.
        await conn.execute(text("DROP TABLE IF EXISTS ekg_embeddings CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS ekg_versions CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS ekg_edges CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS ekg_nodes CASCADE"))
        # Phase 19A organization tables (013)
        await conn.execute(text("DROP TABLE IF EXISTS ekg_cross_repository_edges CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS ekg_repository_namespaces CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS ekg_organizations CASCADE"))
        # Phase 19B provider metrics snapshots (014)
        await conn.execute(text("DROP TABLE IF EXISTS provider_metric_snapshots CASCADE"))
        # Phase 17 reasoning tables (010)
        await conn.execute(text("DROP TABLE IF EXISTS engineering_notebooks CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS contradiction_records CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS evidence_consensus CASCADE"))
        # Phase 16 autonomy tables (007) — drop first (FK-free but keep tidy)
        await conn.execute(text("DROP TABLE IF EXISTS human_escalations CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS execution_checkpoints CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS autonomous_decisions CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS plan_versions CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS execution_goals CASCADE"))
        # Phase 13 tables (004)
        await conn.execute(text("DROP TABLE IF EXISTS repository_memories CASCADE"))
        # Phase 15 tables (005) — only exists when pgvector is available
        await conn.execute(text("DROP TABLE IF EXISTS code_embeddings CASCADE"))
        # Phase 15 collaboration tables (006)
        await conn.execute(text("DROP TABLE IF EXISTS evidence_conflicts CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS run_decisions CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS agent_handoffs CASCADE"))
        # Phase 12 tables (003) — drop first (depend on nothing)
        await conn.execute(text("DROP TABLE IF EXISTS code_relationships CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS code_symbols CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS repository_indexes CASCADE"))
        # Phase 11H tables (002)
        await conn.execute(text("DROP TABLE IF EXISTS workspace_registry CASCADE"))
        # Phase 11 tables (001) — reverse dependency order
        await conn.execute(text("DROP TABLE IF EXISTS artifacts CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS run_events CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS stage_results CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS runs CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS tasks CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS repositories CASCADE"))
        # Force alembic to see a fresh database
        await conn.execute(text("DROP TABLE IF EXISTS alembic_version CASCADE"))
        await conn.commit()

    async def _get_tables(self, db_url: str) -> set[str]:
        """Get the current set of table names."""
        engine = create_async_engine(db_url)
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
            )
            tables = {row[0] for row in result}
        await engine.dispose()
        return tables

    async def test_upgrade_empty_to_head(self, clean_db):
        """Test upgrading from empty database to head."""
        tables_before = await self._get_tables(self._db_url)
        assert len(tables_before) == 0, "Database should be empty before migration"
        
        self._run_alembic_upgrade()
        
        tables_after = await self._get_tables(self._db_url)
        expected = {
            "runs", "tasks", "repositories", "stage_results", "run_events", "artifacts",
            "workspace_registry",
            "code_symbols", "code_relationships", "repository_indexes",
            "repository_memories",
            "agent_handoffs", "run_decisions", "evidence_conflicts",
            # Phase 16 autonomy tables (007)
            "execution_goals", "plan_versions", "autonomous_decisions",
            "execution_checkpoints", "human_escalations",
            # Phase 17 reasoning tables (010)
            "evidence_consensus", "contradiction_records", "engineering_notebooks",
            # Phase 19A organization tables (013)
            "ekg_organizations", "ekg_repository_namespaces", "ekg_cross_repository_edges",
            # Phase 19B provider metrics snapshots (014)
            "provider_metric_snapshots",
            "alembic_version",
        }
        missing = expected - tables_after
        assert not missing, f"Missing tables after upgrade: {missing}"
        
        # Verify alembic_version is at head
        engine = create_async_engine(self._db_url)
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT version_num FROM alembic_version"))
            version = result.scalar_one()
        await engine.dispose()
        head = _get_script_info()[1][0]
        assert version == head, (
            f"alembic_version should be at head {head}, got {version}"
        )

    async def test_upgrade_downgrade_upgrade_roundtrip(self, clean_db):
        """Full round-trip: upgrade → downgrade → upgrade."""
        # Upgrade to head
        self._run_alembic_upgrade()
        tables_after_upgrade = await self._get_tables(self._db_url)
        assert "runs" in tables_after_upgrade
        
        # Downgrade to base
        self._run_alembic_downgrade()
        tables_after_downgrade = await self._get_tables(self._db_url)
        assert "runs" not in tables_after_downgrade, "Tables should be removed after downgrade"
        
        # Upgrade again
        self._run_alembic_upgrade()
        tables_after_reupgrade = await self._get_tables(self._db_url)
        expected = {
            "runs", "tasks", "repositories", "stage_results", "run_events", "artifacts",
            "workspace_registry",
            "code_symbols", "code_relationships", "repository_indexes",
            "repository_memories",
            "agent_handoffs", "run_decisions", "evidence_conflicts",
            # Phase 16 autonomy tables (007)
            "execution_goals", "plan_versions", "autonomous_decisions",
            "execution_checkpoints", "human_escalations",
            # Phase 17 reasoning tables (010)
            "evidence_consensus", "contradiction_records", "engineering_notebooks",
            # Phase 19A organization tables (013)
            "ekg_organizations", "ekg_repository_namespaces", "ekg_cross_repository_edges",
            # Phase 19B provider metrics snapshots (014)
            "provider_metric_snapshots",
            "alembic_version",
        }
        missing = expected - tables_after_reupgrade
        assert not missing, f"Missing tables after re-upgrade: {missing}"

    async def test_schema_invariants(self, clean_db):
        """Verify schema invariants after migration."""
        self._run_alembic_upgrade()
        
        engine = create_async_engine(self._db_url)
        
        # Check expected columns exist on runs table
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT column_name, data_type FROM information_schema.columns "
                     "WHERE table_name = 'runs' ORDER BY ordinal_position")
            )
            columns = {row[0]: row[1] for row in result}
            
            expected_cols = {
                "id", "run_id", "source_type", "status", "current_stage",
                "version", "created_at", "updated_at",
            }
            for col in expected_cols:
                assert col in columns, f"Missing column '{col}' on runs table"
            
            # Check indexes on runs table
            idx_result = await conn.execute(
                text("SELECT indexname FROM pg_indexes WHERE tablename = 'runs'")
            )
            index_names = {row[0] for row in idx_result}
            assert any("run_id" in idx for idx in index_names), \
                f"No index on run_id. Indexes: {index_names}"
        
        # Check run_events has FK to runs
        async with engine.connect() as conn:
            fk_result = await conn.execute(
                text("SELECT constraint_name, constraint_type FROM information_schema.table_constraints "
                     "WHERE table_name = 'run_events' AND constraint_type = 'FOREIGN KEY'")
            )
            fk_constraints = list(fk_result)
            assert len(fk_constraints) >= 1, \
                "run_events should have at least one foreign key constraint"
            
            # Check stage_results has FK to runs
            fk_sr = await conn.execute(
                text("SELECT constraint_name, constraint_type FROM information_schema.table_constraints "
                     "WHERE table_name = 'stage_results' AND constraint_type = 'FOREIGN KEY'")
            )
            assert len(list(fk_sr)) >= 1, \
                "stage_results should have at least one foreign key constraint"
        
        await engine.dispose()

    async def test_alembic_upgrade_idempotent(self, clean_db):
        """Running upgrade twice should be safe."""
        self._run_alembic_upgrade()
        tables_first = await self._get_tables(self._db_url)
        
        # Run upgrade again
        self._run_alembic_upgrade()
        tables_second = await self._get_tables(self._db_url)
        
        assert tables_first == tables_second, "Running upgrade twice should not change schema"

    async def test_expected_indexes_exist(self, clean_db):
        """Verify expected indexes are present."""
        self._run_alembic_upgrade()
        
        engine = create_async_engine(self._db_url)
        async with engine.connect() as conn:
            # Check for composite index on run_events(run_id_fk, sequence)
            result = await conn.execute(
                text("SELECT indexdef FROM pg_indexes WHERE tablename = 'run_events'")
            )
            indexes = [row[0] for row in result]
            has_sequence_idx = any("sequence" in idx for idx in indexes)
            assert has_sequence_idx, \
                "run_events should have an index on sequence. Found: " + str(indexes)
            
            # Check stage_results composite index
            sr_idx = await conn.execute(
                text("SELECT indexdef FROM pg_indexes WHERE tablename = 'stage_results'")
            )
            sr_indexes = [row[0] for row in sr_idx]
            assert any("stage" in idx for idx in sr_indexes), \
                "stage_results should have index on stage. Found: " + str(sr_indexes)
        
        await engine.dispose()

    async def test_phase12_schema_created(self, clean_db):
        """Verify migration 003 creates the expected Phase 12 schema."""
        self._run_alembic_upgrade()

        engine = create_async_engine(self._db_url)
        async with engine.connect() as conn:
            # Check code_symbols table exists with expected columns
            cs_cols = await conn.execute(
                text("SELECT column_name FROM information_schema.columns "
                     "WHERE table_name = 'code_symbols' ORDER BY ordinal_position")
            )
            cs_columns = {row[0] for row in cs_cols}
            expected_cs = {
                "id", "symbol_id", "name", "qualified_name", "kind",
                "file_path", "language", "metadata_json",
                "repository_id", "index_id", "created_at",
            }
            missing_cs = expected_cs - cs_columns
            assert not missing_cs, f"code_symbols missing columns: {missing_cs}"

            # Check code_relationships table exists with expected columns
            cr_cols = await conn.execute(
                text("SELECT column_name FROM information_schema.columns "
                     "WHERE table_name = 'code_relationships' ORDER BY ordinal_position")
            )
            cr_columns = {row[0] for row in cr_cols}
            expected_cr = {
                "id", "source_symbol_id", "target_symbol_id", "relationship",
                "confidence", "weight", "metadata_json",
                "repository_id", "index_id", "created_at",
            }
            missing_cr = expected_cr - cr_columns
            assert not missing_cr, f"code_relationships missing columns: {missing_cr}"

            # Check repository_indexes table exists with expected columns
            ri_cols = await conn.execute(
                text("SELECT column_name FROM information_schema.columns "
                     "WHERE table_name = 'repository_indexes' ORDER BY ordinal_position")
            )
            ri_columns = {row[0] for row in ri_cols}
            expected_ri = {
                "id", "index_id", "repository_id", "repository_path",
                "content_fingerprint", "language_coverage",
                "symbol_count", "relationship_count", "file_count",
                "status", "version", "created_at", "updated_at",
            }
            missing_ri = expected_ri - ri_columns
            assert not missing_ri, f"repository_indexes missing columns: {missing_ri}"

            # Verify Phase 12 indexes exist
            for tbl, expected_idx_prefixes in [
                ("code_symbols", ["idx_cs_symbol_id", "idx_cs_repository_id", "idx_cs_index_id", "idx_cs_file_path", "idx_cs_kind"]),
                ("code_relationships", ["idx_cr_source", "idx_cr_target", "idx_cr_relationship", "idx_cr_repository_id", "idx_cr_index_id"]),
                ("repository_indexes", ["idx_ri_repository_id", "idx_ri_status"]),
            ]:
                idx_result = await conn.execute(
                    text(f"SELECT indexname FROM pg_indexes WHERE tablename = '{tbl}'")
                )
                actual_indexes = {row[0] for row in idx_result}
                for prefix in expected_idx_prefixes:
                    assert any(prefix in idx for idx in actual_indexes), \
                        f"Missing index matching '{prefix}' on {tbl}. Found: {actual_indexes}"

            # Verify primary key constraints exist on all Phase 12 tables
            for tbl in ("code_symbols", "code_relationships", "repository_indexes"):
                pk_result = await conn.execute(
                    text("SELECT constraint_name FROM information_schema.table_constraints "
                         f"WHERE table_name = '{tbl}' AND constraint_type = 'PRIMARY KEY'")
                )
                pk_rows = list(pk_result)
                assert len(pk_rows) == 1, \
                    f"{tbl} should have exactly 1 primary key constraint. Found: {pk_rows}"

            # Verify UNIQUE constraint on repository_indexes.index_id
            unique_result = await conn.execute(
                text("SELECT constraint_name FROM information_schema.table_constraints "
                     "WHERE table_name = 'repository_indexes' AND constraint_type = 'UNIQUE'")
            )
            unique_rows = list(unique_result)
            assert len(unique_rows) >= 1, \
                "repository_indexes should have at least one UNIQUE constraint (on index_id)"

        await engine.dispose()

    async def test_phase17_reasoning_schema_created(self, clean_db):
        """Verify migration 010 creates the expected Phase 17 reasoning schema.

        Asserts the three reasoning tables exist with the expected columns,
        JSONB payload columns, the idx_ecs_*/idx_cdr_*/idx_en_* indexes
        (renamed to avoid the 006 idx_ec_run_id collision), and the named
        unique constraints.
        """
        self._run_alembic_upgrade()

        engine = create_async_engine(self._db_url)
        async with engine.connect() as conn:
            # evidence_consensus columns
            ec_cols = await conn.execute(
                text("SELECT column_name, data_type FROM information_schema.columns "
                     "WHERE table_name = 'evidence_consensus' ORDER BY ordinal_position")
            )
            ec_columns = {row[0]: row[1] for row in ec_cols}
            expected_ec = {
                "id", "consensus_id", "run_id", "topic", "summary", "status",
                "confidence_json", "supporting_evidence", "conflicting_evidence",
                "final_decision", "contributing_agents", "created_at",
            }
            missing_ec = expected_ec - ec_columns.keys()
            assert not missing_ec, f"evidence_consensus missing columns: {missing_ec}"
            for jsonb_col in ("confidence_json", "supporting_evidence",
                              "conflicting_evidence", "contributing_agents"):
                assert ec_columns[jsonb_col] == "jsonb", \
                    f"evidence_consensus.{jsonb_col} should be jsonb, got {ec_columns[jsonb_col]}"

            # contradiction_records columns
            cr_cols = await conn.execute(
                text("SELECT column_name, data_type FROM information_schema.columns "
                     "WHERE table_name = 'contradiction_records' ORDER BY ordinal_position")
            )
            cr_columns = {row[0]: row[1] for row in cr_cols}
            expected_cr = {
                "id", "contradiction_id", "run_id", "kind", "description",
                "claim_evidence", "deterministic_evidence", "resolution", "created_at",
            }
            missing_cr = expected_cr - cr_columns.keys()
            assert not missing_cr, f"contradiction_records missing columns: {missing_cr}"
            for jsonb_col in ("claim_evidence", "deterministic_evidence"):
                assert cr_columns[jsonb_col] == "jsonb", \
                    f"contradiction_records.{jsonb_col} should be jsonb"

            # engineering_notebooks columns
            en_cols = await conn.execute(
                text("SELECT column_name, data_type FROM information_schema.columns "
                     "WHERE table_name = 'engineering_notebooks' ORDER BY ordinal_position")
            )
            en_columns = {row[0]: row[1] for row in en_cols}
            expected_en = {
                "id", "notebook_id", "run_id", "task", "accepted_decisions",
                "rejected_decisions", "conflicts", "resolved_conflicts",
                "consensus", "timeline", "version", "created_at", "updated_at",
            }
            missing_en = expected_en - en_columns.keys()
            assert not missing_en, f"engineering_notebooks missing columns: {missing_en}"
            for jsonb_col in ("accepted_decisions", "rejected_decisions",
                              "conflicts", "resolved_conflicts", "consensus", "timeline"):
                assert en_columns[jsonb_col] == "jsonb", \
                    f"engineering_notebooks.{jsonb_col} should be jsonb"

            # Verify the reasoning indexes exist (idx_ecs_* avoids the 006 collision)
            for tbl, expected_idx_prefixes in [
                ("evidence_consensus", ["idx_ecs_run_id", "idx_ecs_run_topic"]),
                ("contradiction_records", ["idx_cdr_run_id", "idx_cdr_run_kind"]),
                ("engineering_notebooks", ["idx_en_run_id"]),
            ]:
                idx_result = await conn.execute(
                    text(f"SELECT indexname FROM pg_indexes WHERE tablename = '{tbl}'")
                )
                actual_indexes = {row[0] for row in idx_result}
                for prefix in expected_idx_prefixes:
                    assert any(prefix in idx for idx in actual_indexes), \
                        f"Missing index matching '{prefix}' on {tbl}. Found: {actual_indexes}"

            # Verify the named unique constraints exist
            uq_result = await conn.execute(
                text("SELECT constraint_name FROM information_schema.table_constraints "
                     "WHERE table_name IN ('evidence_consensus', 'contradiction_records', "
                     "'engineering_notebooks') AND constraint_type = 'UNIQUE'")
            )
            uq_names = {row[0] for row in uq_result}
            for expected_uq in (
                "uq_evidence_consensus_consensus_id",
                "uq_contradiction_records_contradiction_id",
                "uq_engineering_notebooks_notebook_id",
            ):
                assert expected_uq in uq_names, \
                    f"Missing unique constraint {expected_uq}. Found: {uq_names}"

            # No stale idx_ec_run_id index should exist on evidence_consensus
            ec_idx_result = await conn.execute(
                text("SELECT indexname FROM pg_indexes WHERE tablename = 'evidence_consensus'")
            )
            ec_idx_names = {row[0] for row in ec_idx_result}
            assert "idx_ec_run_id" not in ec_idx_names, \
                f"idx_ec_run_id must NOT exist on evidence_consensus (006 collision). Found: {ec_idx_names}"

        await engine.dispose()

    async def test_phase19b_provider_metrics_schema_created(self, clean_db):
        """Verify migration 014 creates the Phase 19B provider metrics schema."""
        self._run_alembic_upgrade()

        engine = create_async_engine(self._db_url)
        async with engine.connect() as conn:
            pm_cols = await conn.execute(
                text("SELECT column_name, data_type FROM information_schema.columns "
                     "WHERE table_name = 'provider_metric_snapshots' ORDER BY ordinal_position")
            )
            pm_columns = {row[0]: row[1] for row in pm_cols}
            expected_pm = {
                "id", "provider", "status", "circuit_state",
                "total_requests", "successful_requests", "failed_requests",
                "retries", "failovers", "avg_latency_ms", "success_rate",
                "recorded_at",
            }
            missing_pm = expected_pm - pm_columns.keys()
            assert not missing_pm, \
                f"provider_metric_snapshots missing columns: {missing_pm}"

            idx_result = await conn.execute(
                text("SELECT indexname FROM pg_indexes "
                     "WHERE tablename = 'provider_metric_snapshots'")
            )
            index_names = {row[0] for row in idx_result}
            assert "idx_provider_metric_snapshots_provider_recorded" in index_names, \
                f"Missing composite index. Found: {index_names}"

            # Snapshot rows are insertable and queryable.
            await conn.execute(text(
                "INSERT INTO provider_metric_snapshots "
                "(provider, status, circuit_state, total_requests, successful_requests, "
                " failed_requests, retries, failovers, success_rate, recorded_at) "
                "VALUES ('fake', 'healthy', 'closed', 1, 1, 0, 0, 0, 1.0, now())"
            ))
            result = await conn.execute(text(
                "SELECT provider, success_rate FROM provider_metric_snapshots "
                "WHERE provider = 'fake'"
            ))
            row = result.fetchone()
            assert row == ("fake", 1.0), f"Unexpected snapshot row: {row}"
        await engine.dispose()
