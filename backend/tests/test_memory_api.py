"""
Phase 15 — Tests for the /api/v1/memory browsing & invalidation endpoints.

Uses mocked RepositoryMemoryService so no live PostgreSQL is required.
Verifies response envelope, filters, invalidation, and deletion.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _make_memory(memory_id="mem_001", status="verified", memory_type="architecture"):
    mem = MagicMock()
    mem.memory_id = memory_id
    mem.repository_id = "devpilot"
    mem.memory_type.value = memory_type
    mem.status.value = status
    mem.content = "AuthService handles all authentication logic"
    mem.confidence = 0.85
    mem.symbol_names = ["AuthService"]
    mem.file_paths = ["auth_service.py"]
    mem.source_run_id = None
    mem.version = 1
    mem.created_at = "2026-07-30T00:00:00+00:00"
    mem.updated_at = "2026-07-30T00:00:00+00:00"
    mem.last_used_at = None
    mem.evidence = []
    return mem


def _mock_service():
    svc = MagicMock()
    svc.list_repository_ids = AsyncMock(return_value=["devpilot", "other-repo"])
    svc.query_memories = AsyncMock(return_value=[_make_memory()])
    svc.get_memory_stats = AsyncMock(return_value={
        "total": 12, "by_type": {"architecture": 4}, "by_status": {"verified": 8},
        "avg_confidence": 0.78,
    })
    svc.invalidate_memories_for_symbols = AsyncMock(return_value=3)
    svc.get_memory = AsyncMock(return_value=_make_memory())
    svc.update_memory = AsyncMock(return_value=_make_memory(status="stale"))
    svc.delete_memory = AsyncMock(return_value=True)
    return svc


class TestMemoryRepositories:
    @patch("app.api.v1.memory._get_service")
    def test_list_repositories(self, mock_get):
        svc = _mock_service()
        mock_get.return_value = svc
        res = client.get("/api/v1/memory/repositories")
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert "devpilot" in data["data"]["repositories"]
        assert data["data"]["count"] == 2

    @patch("app.api.v1.memory._get_service")
    def test_list_repositories_error(self, mock_get):
        svc = _mock_service()
        svc.list_repository_ids = AsyncMock(side_effect=RuntimeError("boom"))
        mock_get.return_value = svc
        res = client.get("/api/v1/memory/repositories")
        assert res.status_code == 200
        assert res.json()["success"] is False


class TestListMemories:
    @patch("app.api.v1.memory._get_service")
    def test_list_memories(self, mock_get):
        svc = _mock_service()
        mock_get.return_value = svc
        res = client.get("/api/v1/memory/devpilot")
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["data"]["count"] == 1
        assert data["data"]["memories"][0]["memory_id"] == "mem_001"
        assert data["data"]["memories"][0]["status"] == "verified"

    @patch("app.api.v1.memory._get_service")
    def test_list_memories_with_filters(self, mock_get):
        svc = _mock_service()
        mock_get.return_value = svc
        res = client.get(
            "/api/v1/memory/devpilot",
            params={"status": "verified", "memory_type": "architecture", "symbol": "AuthService"},
        )
        assert res.status_code == 200
        assert res.json()["success"] is True
        # Verify filter args were passed through to the service
        _, kwargs = svc.query_memories.call_args
        query = kwargs["query"]
        assert query.repository_id == "devpilot"
        assert query.symbol_names == ["AuthService"]


class TestMemoryStats:
    @patch("app.api.v1.memory._get_service")
    def test_stats(self, mock_get):
        svc = _mock_service()
        mock_get.return_value = svc
        res = client.get("/api/v1/memory/devpilot/stats")
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["data"]["total"] == 12
        assert data["data"]["by_status"]["verified"] == 8


class TestInvalidation:
    @patch("app.api.v1.memory._get_service")
    def test_invalidate_by_symbols(self, mock_get):
        svc = _mock_service()
        mock_get.return_value = svc
        res = client.post(
            "/api/v1/memory/devpilot/invalidate-symbols",
            params={"symbols": "AuthService, TokenService", "reason": "code changed"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["data"]["invalidated"] == 3
        _, kwargs = svc.invalidate_memories_for_symbols.call_args
        assert kwargs["symbol_names"] == ["AuthService", "TokenService"]

    @patch("app.api.v1.memory._get_service")
    def test_invalidate_by_symbols_empty(self, mock_get):
        svc = _mock_service()
        mock_get.return_value = svc
        res = client.post(
            "/api/v1/memory/devpilot/invalidate-symbols",
            params={"symbols": " , "},
        )
        assert res.json()["success"] is False

    @patch("app.api.v1.memory._get_service")
    def test_invalidate_single_memory(self, mock_get):
        svc = _mock_service()
        mock_get.return_value = svc
        res = client.post("/api/v1/memory/mem_001/invalidate", params={"reason": "manual"})
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["data"]["status"] == "stale"

    @patch("app.api.v1.memory._get_service")
    def test_invalidate_missing_memory(self, mock_get):
        svc = _mock_service()
        svc.get_memory = AsyncMock(return_value=None)
        mock_get.return_value = svc
        res = client.post("/api/v1/memory/nope/invalidate")
        assert res.json()["success"] is False
        assert res.json()["error"] == "NotFound"


class TestDeletion:
    @patch("app.api.v1.memory._get_service")
    def test_delete_memory(self, mock_get):
        svc = _mock_service()
        mock_get.return_value = svc
        res = client.delete("/api/v1/memory/mem_001")
        assert res.status_code == 200
        assert res.json()["success"] is True

    @patch("app.api.v1.memory._get_service")
    def test_delete_missing_memory(self, mock_get):
        svc = _mock_service()
        svc.delete_memory = AsyncMock(return_value=False)
        mock_get.return_value = svc
        res = client.delete("/api/v1/memory/nope")
        assert res.json()["success"] is False
        assert res.json()["error"] == "NotFound"
