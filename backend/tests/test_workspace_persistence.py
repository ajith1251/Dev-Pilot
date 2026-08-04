"""
Tests for workspace persistence integration.

Covers:
- WorkspaceModel ORM model
- PostgresRunStore workspace methods (save, get, list, delete, count)
- TestingService persistent workspace tracking
- WorkspaceService persistent workspace lookup
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.db.models import WorkspaceModel
from app.services.postgres_run_store import PostgresRunStore
from app.services.testing_service import TestingService
from app.services.workspace_service import WorkspaceService


# ═════════════════════════════════════════════════════════════════
# 1 — WORKSPACE MODEL TESTS
# ═════════════════════════════════════════════════════════════════


class TestWorkspaceModel:
    """Test WorkspaceModel ORM model creation and attributes."""

    def test_model_has_correct_tablename(self):
        """WorkspaceModel should use 'workspace_registry' table."""
        assert WorkspaceModel.__tablename__ == "workspace_registry"

    def test_model_has_required_columns(self):
        """WorkspaceModel should have all required columns."""
        columns = {c.name for c in WorkspaceModel.__table__.columns}
        required = {
            "id", "workspace_id", "source_repository", "root_path",
            "run_id", "fingerprint", "writable", "workspace_type",
            "created_at", "updated_at",
        }
        assert required.issubset(columns), f"Missing columns: {required - columns}"

    def test_workspace_id_is_unique_indexed(self):
        """workspace_id should have a unique index."""
        indexes = WorkspaceModel.__table__.indexes
        unique_indexes = {idx.name for idx in indexes if idx.unique}
        assert "ix_workspace_registry_workspace_id" in unique_indexes

    def test_run_id_is_indexed(self):
        """run_id should be indexed (not unique)."""
        indexes = WorkspaceModel.__table__.indexes
        index_names = {idx.name for idx in indexes}
        assert "ix_workspace_registry_run_id" in index_names


# ═════════════════════════════════════════════════════════════════
# 2 — POSTGRESRUNSTORE WORKSPACE METHODS (MOCKED)
# ═════════════════════════════════════════════════════════════════


class TestPostgresRunStoreWorkspaceMethods:
    """Test workspace persistence methods with mocked DB session."""

    def setup_method(self):
        self.store = PostgresRunStore()
        self.mock_session = AsyncMock()
        self.mock_session.__aenter__ = AsyncMock(return_value=self.mock_session)
        self.mock_session.__aexit__ = AsyncMock(return_value=None)
        self.mock_factory = MagicMock(return_value=self.mock_session)
        self.store._get_session_factory = MagicMock(return_value=self.mock_factory)

    # ── save_workspace ──

    @pytest.mark.asyncio
    async def test_save_workspace_creates_new(self):
        """save_workspace should create a new workspace record."""
        # Mock: no existing workspace found
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        self.mock_session.execute = AsyncMock(return_value=mock_result)

        result = await self.store.save_workspace(
            workspace_id="ws-test-001",
            root_path="/tmp/workspaces/ws-test-001",
            source_repository="/tmp/repos/my-project",
            run_id="RUN-ABC123",
            writable=True,
            workspace_type="coding",
        )

        assert result["workspace_id"] == "ws-test-001"
        assert result["root_path"] == "/tmp/workspaces/ws-test-001"
        assert result["run_id"] == "RUN-ABC123"
        assert result["workspace_type"] == "coding"
        assert result["writable"] is True

        # Verify session.add was called (new record)
        self.mock_session.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_workspace_updates_existing(self):
        """save_workspace should update an existing workspace record.

        Note: The returned dict reflects the model's attribute state at
        commit time. Since we're mocking the session, the model is a real
        WorkspaceModel and attribute updates on it are tracked by the
        mock, not persisted.
        """
        existing_model = WorkspaceModel(
            workspace_id="ws-test-002",
            root_path="/tmp/old-path",
            writable=True,
            workspace_type="coding",
        )
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing_model
        self.mock_session.execute = AsyncMock(return_value=mock_result)

        result = await self.store.save_workspace(
            workspace_id="ws-test-002",
            root_path="/tmp/new-path",
            run_id="RUN-NEW",
            writable=True,
            workspace_type="testing",
        )

        # Verify the returned record has the updated workspace_id
        assert result["workspace_id"] == "ws-test-002"
        # The model attributes should have been updated
        assert existing_model.workspace_type == "testing"
        assert existing_model.root_path == "/tmp/new-path"

        # Verify session.add was NOT called (update existing)
        self.mock_session.add.assert_not_called()

    # ── get_workspace ──

    @pytest.mark.asyncio
    async def test_get_workspace_found(self):
        """get_workspace should return workspace data when found."""
        mock_model = WorkspaceModel(
            workspace_id="ws-001",
            root_path="/tmp/workspaces/ws-001",
            source_repository="/tmp/repos/project",
            run_id="RUN-001",
            fingerprint="abc123",
            writable=True,
            workspace_type="coding",
        )
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_model
        self.mock_session.execute = AsyncMock(return_value=mock_result)

        result = await self.store.get_workspace("ws-001")

        assert result is not None
        assert result["workspace_id"] == "ws-001"
        assert result["root_path"] == "/tmp/workspaces/ws-001"
        assert result["run_id"] == "RUN-001"
        assert result["fingerprint"] == "abc123"

    @pytest.mark.asyncio
    async def test_get_workspace_not_found(self):
        """get_workspace should return None when not found."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        self.mock_session.execute = AsyncMock(return_value=mock_result)

        result = await self.store.get_workspace("ws-nonexistent")
        assert result is None

    # ── list_workspaces ──

    @pytest.mark.asyncio
    async def test_list_workspaces_no_filter(self):
        """list_workspaces should return all workspace registrations."""
        mock_models = [
            WorkspaceModel(
                workspace_id=f"ws-{i:03d}",
                root_path=f"/tmp/ws-{i:03d}",
                workspace_type="coding",
            )
            for i in range(3)
        ]
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = mock_models
        self.mock_session.execute = AsyncMock(return_value=mock_result)

        results = await self.store.list_workspaces()

        assert len(results) == 3
        assert results[0]["workspace_id"] == "ws-000"
        assert results[2]["workspace_id"] == "ws-002"

    @pytest.mark.asyncio
    async def test_list_workspaces_filtered_by_run_id(self):
        """list_workspaces should filter by run_id when provided."""
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result.scalars.return_value = mock_scalars
        self.mock_session.execute = AsyncMock(return_value=mock_result)

        results = await self.store.list_workspaces(run_id="RUN-001")

        assert len(results) == 0
        # Verify execute was called (indicates query was built and run)
        self.mock_session.execute.assert_called_once()

    # ── delete_workspace ──

    @pytest.mark.asyncio
    async def test_delete_workspace_found(self):
        """delete_workspace should return True when deleted."""
        mock_model = WorkspaceModel(
            workspace_id="ws-to-delete",
            root_path="/tmp/ws-to-delete",
        )
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_model
        self.mock_session.execute = AsyncMock(return_value=mock_result)

        result = await self.store.delete_workspace("ws-to-delete")
        assert result is True
        self.mock_session.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_workspace_not_found(self):
        """delete_workspace should return False when not found."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        self.mock_session.execute = AsyncMock(return_value=mock_result)

        result = await self.store.delete_workspace("ws-nonexistent")
        assert result is False

    # ── count_workspaces ──

    @pytest.mark.asyncio
    async def test_count_workspaces(self):
        """count_workspaces should return the count."""
        mock_result = MagicMock()
        mock_result.scalar.return_value = 5
        self.mock_session.execute = AsyncMock(return_value=mock_result)

        count = await self.store.count_workspaces()
        assert count == 5

    @pytest.mark.asyncio
    async def test_count_workspaces_filtered(self):
        """count_workspaces should filter by type."""
        mock_result = MagicMock()
        mock_result.scalar.return_value = 3
        self.mock_session.execute = AsyncMock(return_value=mock_result)

        count = await self.store.count_workspaces(workspace_type="testing")
        assert count == 3


# ═════════════════════════════════════════════════════════════════
# 3 — TESTINGSERVICE WORKSPACE PERSISTENCE
# ═════════════════════════════════════════════════════════════════


class TestTestingServiceWorkspacePersistence:
    """Test TestingService workspace tracking with persistent store."""

    def setup_method(self):
        self.mock_store = MagicMock()
        self.mock_store.save_workspace = AsyncMock()
        self.mock_store.get_workspace = AsyncMock()
        self.mock_store.delete_workspace = AsyncMock()
        self.service = TestingService(run_store=self.mock_store)

    def test_has_persistent_workspace_store_true(self):
        """_has_persistent_workspace_store should return True when run_store supports workspace methods."""
        assert self.service._has_persistent_workspace_store() is True

    def test_has_persistent_workspace_store_false(self):
        """_has_persistent_workspace_store should return False without run_store."""
        service = TestingService()
        assert service._has_persistent_workspace_store() is False

    def test_register_workspace_in_memory_fallback(self):
        """register_workspace should work without a persistent store."""
        service = TestingService()
        service.register_workspace("ws-001", "/tmp/ws-001")
        assert service.get_workspace_root("ws-001") == "/tmp/ws-001"
        assert service.workspace_count == 1

    def test_unregister_workspace_removes_from_memory(self):
        """unregister_workspace should remove from in-memory tracking."""
        self.service.register_workspace("ws-001", "/tmp/ws-001")
        self.service.unregister_workspace("ws-001")
        assert self.service.get_workspace_root("ws-001") is None
        assert self.service.workspace_count == 0


# ═════════════════════════════════════════════════════════════════
# 4 — WORKSPACESERVICE PERSISTENCE
# ═════════════════════════════════════════════════════════════════


class TestWorkspaceServicePersistence:
    """Test WorkspaceService persistent workspace integration."""

    def setup_method(self):
        self.mock_store = MagicMock()
        self.mock_store.save_workspace = AsyncMock()
        self.mock_store.get_workspace = AsyncMock()
        self.mock_store.delete_workspace = AsyncMock()
        self.service = WorkspaceService(run_store=self.mock_store)

    def test_get_workspace_returns_none_without_store(self):
        """get_workspace should return None without a persistent store."""
        service = WorkspaceService()
        result = service.get_workspace("ws-001")
        assert result is None

    def test_create_workspace_basic(self):
        """create_workspace should work without a persistent store."""
        import tempfile
        import os

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a subdirectory to use as source
            source = os.path.join(tmpdir, "source")
            os.makedirs(source)
            with open(os.path.join(source, "test.txt"), "w") as f:
                f.write("hello")

            service = WorkspaceService()
            workspace = service.create_workspace(source)
            assert workspace.workspace_id is not None
            assert workspace.root_path is not None
            assert workspace.writable is True

            # Cleanup
            import shutil
            shutil.rmtree(workspace.root_path, ignore_errors=True)

    def test_get_workspace_no_persistent_fallback(self):
        """get_workspace should not use run_store if get_workspace attr missing."""
        store = MagicMock()  # No save_workspace, get_workspace, etc.
        service = WorkspaceService(run_store=store)
        result = service.get_workspace("ws-001")
        assert result is None

    def test_create_workspace_no_run_store(self):
        """create_workspace should work when run_store has no save_workspace."""
        store = MagicMock(spec=[])  # Empty spec - no methods
        service = WorkspaceService(run_store=store)
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            import os
            source = os.path.join(tmpdir, "source")
            os.makedirs(source)
            workspace = service.create_workspace(source)
            assert workspace is not None
            import shutil
            shutil.rmtree(workspace.root_path, ignore_errors=True)


# ═════════════════════════════════════════════════════════════════
# 5 — MODEL REPR
# ═════════════════════════════════════════════════════════════════


class TestWorkspaceModelRepr:
    """Test WorkspaceModel string representation."""

    def test_repr_contains_workspace_id(self):
        """__repr__ should include the workspace_id."""
        model = WorkspaceModel(
            workspace_id="ws-repr-test",
            root_path="/tmp/ws-repr-test",
        )
        assert "ws-repr-test" in repr(model)

    def test_repr_contains_root_path(self):
        """__repr__ should include the root path."""
        model = WorkspaceModel(
            workspace_id="ws-repr-test",
            root_path="/tmp/ws-repr-test",
        )
        assert "/tmp/ws-repr-test" in repr(model)
