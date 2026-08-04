"""Tests for the health-check endpoint."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    """Create a test client."""
    return TestClient(app)


class TestHealth:
    """Health endpoint tests."""

    def test_health_returns_200(self, client: TestClient) -> None:
        """GET /health should return 200 OK."""
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_health_returns_json(self, client: TestClient) -> None:
        """Response should be valid JSON with expected fields."""
        resp = client.get("/health")
        data = resp.json()

        assert data["success"] is True
        assert "app" in data["data"]
        assert data["data"]["status"] == "healthy"
        assert "version" in data["data"]
        assert "llm_provider" in data["data"]

    def test_health_version_matches(self, client: TestClient) -> None:
        """Version should match the app version."""
        from app import __version__

        resp = client.get("/health")
        data = resp.json()
        assert data["data"]["version"] == __version__
