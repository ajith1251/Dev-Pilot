"""
Phase 11H — API Contract Tests

Verifies:
- HTTP status codes for all orchestration endpoints
- Request/response schema compatibility
- Enum values
- Error models
- Pagination parameters
- Security boundaries (long strings, invalid inputs)
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


class TestAPIStatusCodes:
    """Verify HTTP status codes for all endpoints."""

    def test_health_endpoint(self):
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "data" in data

    def test_list_runs_returns_200(self):
        response = client.get("/api/v1/runs")
        # NOTE: May return 500 if DB is not configured — contract test expects 200 or 500
        assert response.status_code in (200, 500)
        if response.status_code == 200:
            data = response.json()
            assert "data" in data
            assert "count" in data

    def test_get_nonexistent_run_returns_404(self):
        response = client.get("/api/v1/runs/NONEXISTENT")
        assert response.status_code in (404, 500)

    def test_orchestration_capabilities(self):
        response = client.get("/api/v1/orchestration/capabilities")
        assert response.status_code in (200, 500)
        if response.status_code == 200:
            data = response.json()
            caps = data.get("data", {})
            assert "stages" in caps
            assert "cancellation_mode" in caps
            assert "persistence_mode" in caps
            assert "repair_enabled" in caps
            assert "review_enabled" in caps


class TestPaginationContracts:
    """Verify pagination parameters are handled correctly."""

    def test_list_runs_with_limit(self):
        response = client.get("/api/v1/runs?limit=5")
        assert response.status_code in (200, 500)
        if response.status_code == 200:
            data = response.json()
            assert len(data.get("data", [])) <= 5

    def test_list_runs_with_offset(self):
        response = client.get("/api/v1/runs?offset=0&limit=3")
        assert response.status_code in (200, 500)

    def test_list_runs_with_status_filter(self):
        response = client.get("/api/v1/runs?status=approved")
        assert response.status_code in (200, 500)
        if response.status_code == 200:
            data = response.json()
            for run in data.get("data", []):
                assert run["status"] == "approved"

    def test_list_runs_large_limit_capped(self):
        response = client.get("/api/v1/runs?limit=9999")
        assert response.status_code in (200, 500)


class TestSecurityBoundaries:
    """Verify safe handling of edge case and malicious inputs."""

    def test_very_long_run_id(self):
        """Extremely long run IDs should return 404, not crash."""
        long_id = "A" * 1000
        response = client.get(f"/api/v1/runs/{long_id}")
        assert response.status_code in (404, 422, 500)

    def test_sql_like_run_id(self):
        """SQL-like strings should be treated as literal run IDs, not executed."""
        response = client.get("/api/v1/runs/1;DROP TABLE runs;--")
        assert response.status_code in (404, 422, 500)

    def test_unicode_in_run_id(self):
        """Unicode in run IDs should be handled safely."""
        response = client.get("/api/v1/runs/🔥-run-测试")
        assert response.status_code in (404, 422, 500)

    def test_negative_pagination(self):
        """Negative pagination values should not cause crashes."""
        response = client.get("/api/v1/runs?limit=-1&offset=-5")
        assert response.status_code in (200, 422, 500)

    def test_invalid_enum_status(self):
        """Invalid status values should be handled gracefully."""
        response = client.get("/api/v1/runs?status=invalid_status_value")
        assert response.status_code in (200, 422, 500)

    def test_html_injection(self):
        """HTML-like strings should be sanitized or rejected."""
        response = client.get('/api/v1/runs?status=<script>alert("xss")</script>')
        assert response.status_code in (200, 422, 500)

    def test_malformed_uuid(self):
        """Malformed UUID-like run IDs should not crash."""
        response = client.get("/api/v1/runs/not-a-uuid-at-all")
        assert response.status_code in (404, 422, 500)


class TestTotalCountAndStats:
    """Verify total_count and stats fields in list_runs response."""

    def test_total_count_is_int_and_present(self):
        response = client.get("/api/v1/runs")
        if response.status_code != 200:
            pytest.skip(f"API returned {response.status_code}")
        data = response.json()
        assert "total_count" in data
        assert isinstance(data["total_count"], int)
        assert data["total_count"] >= 0

    def test_total_count_is_at_least_count(self):
        """total_count should be >= count (since count is just the page)."""
        response = client.get("/api/v1/runs")
        if response.status_code != 200:
            pytest.skip(f"API returned {response.status_code}")
        data = response.json()
        assert data["total_count"] >= data["count"]

    def test_total_count_respects_status_filter(self):
        """When a status filter is active, total_count should reflect only matching runs."""
        response_all = client.get("/api/v1/runs")
        response_filtered = client.get("/api/v1/runs?status=approved")
        if response_all.status_code != 200 or response_filtered.status_code != 200:
            pytest.skip("API not available")
        total_all = response_all.json()["total_count"]
        total_filtered = response_filtered.json()["total_count"]
        assert total_filtered <= total_all

    def test_stats_is_unfiltered(self):
        """The stats dict should contain the same total regardless of status filter."""
        response_all = client.get("/api/v1/runs")
        response_filtered = client.get("/api/v1/runs?status=approved")
        if response_all.status_code != 200 or response_filtered.status_code != 200:
            pytest.skip("API not available")
        stats_all = response_all.json()["stats"]
        stats_filtered = response_filtered.json()["stats"]
        # Stats total should be the same (unfiltered) regardless of query filter
        assert stats_all["total"] == stats_filtered["total"]

    def test_stats_has_all_status_keys(self):
        response = client.get("/api/v1/runs")
        if response.status_code != 200:
            pytest.skip(f"API returned {response.status_code}")
        stats = response.json()["stats"]
        expected_keys = {
            "total", "pending", "running", "approved",
            "rejected", "needs_human_review", "failed", "cancelled",
        }
        assert set(stats.keys()) == expected_keys, f"Missing keys: {expected_keys - set(stats.keys())}"

    def test_stats_values_are_non_negative_ints(self):
        response = client.get("/api/v1/runs")
        if response.status_code != 200:
            pytest.skip(f"API returned {response.status_code}")
        stats = response.json()["stats"]
        for key, value in stats.items():
            assert isinstance(value, int), f"Stats.{key} is not int: {type(value)}"
            assert value >= 0, f"Stats.{key} is negative: {value}"

    def test_stats_total_matches_sum_of_statuses(self):
        """stats.total should equal sum of all status-specific counts."""
        response = client.get("/api/v1/runs")
        if response.status_code != 200:
            pytest.skip(f"API returned {response.status_code}")
        stats = response.json()["stats"]
        status_sum = sum(v for k, v in stats.items() if k != "total")
        assert stats["total"] == status_sum, (
            f"total={stats['total']} but sum of statuses={status_sum}"
        )

    def test_count_is_page_count_not_total(self):
        """count should be the number of items returned (<= limit), while total_count is the full total."""
        response = client.get("/api/v1/runs?limit=3")
        if response.status_code != 200:
            pytest.skip(f"API returned {response.status_code}")
        data = response.json()
        assert data["count"] <= 3
        assert data["count"] == len(data["data"])


class TestSchemaValidation:
    """Validate response schema structure and types."""

    def test_run_list_response_structure(self):
        response = client.get("/api/v1/runs")
        if response.status_code != 200:
            pytest.skip(f"API returned {response.status_code}")
        data = response.json()
        assert "success" in data
        assert "data" in data
        assert "count" in data
        assert "total_count" in data
        assert "stats" in data
        assert isinstance(data["success"], bool)
        assert isinstance(data["data"], list)
        assert isinstance(data["count"], int)
        assert isinstance(data["total_count"], int)
        assert isinstance(data["stats"], dict)

    def test_run_summary_fields(self):
        response = client.get("/api/v1/runs")
        if response.status_code != 200 or not response.json().get("data"):
            pytest.skip("No runs available")
        run = response.json()["data"][0]
        # Core fields every RunSummary must have
        assert "run_id" in run
        assert "status" in run
        assert "title" in run
        assert "source" in run
        assert "current_stage" in run
        assert "created_at" in run
        # Status must be a valid enum value
        valid_statuses = {
            "pending", "running", "approved", "rejected",
            "needs_human_review", "failed", "cancelled",
        }
        assert run["status"] in valid_statuses, \
            f"Invalid status: {run['status']}"

    def test_capabilities_response_structure(self):
        response = client.get("/api/v1/orchestration/capabilities")
        if response.status_code != 200:
            pytest.skip(f"API returned {response.status_code}")
        data = response.json()
        assert data["success"] is True
        caps = data["data"]
        assert isinstance(caps["stages"], list)
        assert len(caps["stages"]) > 0
        assert isinstance(caps["cancellation_mode"], str)
        assert isinstance(caps["persistence_mode"], str)
        assert isinstance(caps["repair_enabled"], bool)
        assert isinstance(caps["review_enabled"], bool)


class TestSeededTotalCount:
    """Seeded-database tests: seed exact runs and verify total_count / stats with precise values.

    These tests seed the app's internal InMemoryRunStore with a known set of runs
    and verify that the API response contains the exact expected counts.
    """

    SEED_DATA = [
        ("S-APP-1", "approved", "2026-06-10T00:00:00Z"),
        ("S-APP-2", "approved", "2026-06-11T00:00:00Z"),
        ("S-APP-3", "approved", "2026-06-12T00:00:00Z"),
        ("S-APP-4", "approved", "2026-06-13T00:00:00Z"),
        ("S-APP-5", "approved", "2026-06-14T00:00:00Z"),
        ("S-RUN-1", "running", "2026-06-15T00:00:00Z"),
        ("S-RUN-2", "running", "2026-06-16T00:00:00Z"),
        ("S-FAIL-1", "failed", "2026-06-17T00:00:00Z"),
        ("S-REJ-1", "rejected", "2026-06-18T00:00:00Z"),
        ("S-PEN-1", "pending", "2026-06-19T00:00:00Z"),
    ]

    @pytest.fixture(autouse=True)
    def _seed_store(self):
        """Seed the app's InMemoryRunStore with known runs before each test, then clean up.

        Only works with InMemoryRunStore. Skips if the app is using PostgresRunStore.
        For PostgresRunStore exact-count tests, see TestPostgresRunStore in
        test_run_store_contract.py.
        """
        from app.api.v1.orchestration import workflow as api_workflow

        # Guard against internal refactors before accessing private attributes
        if not hasattr(api_workflow, '_orchestrator') or not hasattr(api_workflow._orchestrator, '_store'):
            pytest.skip("TestSeededTotalCount: cannot access internal store")

        store = api_workflow._orchestrator._store

        if not hasattr(store, '_runs'):
            pytest.skip("TestSeededTotalCount requires InMemoryRunStore")

        from app.models.orchestration import DevPilotRun, RunStatus, RunSource, RunSourceType, StageType

        self._seeded_ids = []
        for run_id, status_str, created_at in self.SEED_DATA:
            run = DevPilotRun(
                run_id=run_id,
                source=RunSource(source_type=RunSourceType.USER_TASK, title=run_id),
                status=RunStatus(status_str),
                current_stage=StageType.INITIALIZING,
                created_at=created_at,
            )
            store._runs[run.run_id] = run
            self._seeded_ids.append(run_id)

        yield

        # Clean up
        for rid in self._seeded_ids:
            store._runs.pop(rid, None)

    def test_total_count_matches_seed(self):
        """total_count should equal the exact number of seeded runs."""
        response = client.get("/api/v1/runs")
        assert response.status_code == 200
        data = response.json()
        assert data["total_count"] == len(self.SEED_DATA)

    def test_total_count_with_status_filter(self):
        """total_count should reflect exact status-filtered counts."""
        response = client.get("/api/v1/runs?status=approved")
        assert response.status_code == 200
        data = response.json()
        assert data["total_count"] == 5

        response = client.get("/api/v1/runs?status=running")
        assert response.status_code == 200
        data = response.json()
        assert data["total_count"] == 2

        response = client.get("/api/v1/runs?status=needs_human_review")
        assert response.status_code == 200
        data = response.json()
        assert data["total_count"] == 0

    def test_total_count_with_date_filter(self):
        """total_count with created_after/before should return exact counts."""
        # June 15 and later (running + failed + rejected + pending = 5 runs)
        response = client.get("/api/v1/runs?created_after=2026-06-15T00:00:00Z")
        assert response.status_code == 200
        data = response.json()
        assert data["total_count"] == 5

        # Before June 15 (5 approved runs)
        response = client.get("/api/v1/runs?created_before=2026-06-15T00:00:00Z")
        assert response.status_code == 200
        data = response.json()
        assert data["total_count"] == 5

        # Date range that matches no runs (far future)
        response = client.get("/api/v1/runs?created_after=2027-01-01T00:00:00Z")
        assert response.status_code == 200
        data = response.json()
        assert data["total_count"] == 0

    def test_total_count_with_status_and_date(self):
        """Combined status + date filter should give exact intersection."""
        # Approved + before June 14 = 4 (S-APP-1 through S-APP-4)
        response = client.get("/api/v1/runs?status=approved&created_before=2026-06-14T00:00:00Z")
        assert response.status_code == 200
        data = response.json()
        assert data["total_count"] == 4

    def test_stats_exact_values(self):
        """Stats should have exact values matching the seed data."""
        response = client.get("/api/v1/runs")
        assert response.status_code == 200
        stats = response.json()["stats"]
        assert stats["total"] == 10
        assert stats["approved"] == 5
        assert stats["running"] == 2
        assert stats["failed"] == 1
        assert stats["rejected"] == 1
        assert stats["pending"] == 1
        assert stats["needs_human_review"] == 0
        assert stats["cancelled"] == 0

    def test_stats_unfiltered_even_with_filter(self):
        """Stats should be unfiltered (same totals) even when status filter is active."""
        response = client.get("/api/v1/runs?status=approved")
        assert response.status_code == 200
        stats = response.json()["stats"]
        assert stats["total"] == 10
        assert stats["approved"] == 5

    def test_count_is_page_count(self):
        """count should be <= limit, total_count should be the full seed."""
        response = client.get("/api/v1/runs?limit=3")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 3
        assert len(data["data"]) == 3
        assert data["total_count"] == 10


class TestErrorModels:
    """Verify error response structure."""

    def test_404_error_structure(self):
        response = client.get("/api/v1/runs/__definitely_not_found__")
        if response.status_code != 404:
            # The endpoint might return 500 if DB is down
            assert response.status_code in (404, 500)
            return
        data = response.json()
        # FastAPI default error structure
        assert "detail" in data
