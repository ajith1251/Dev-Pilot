"""Tests for the Phase 4 Planning API endpoints (mocked dependencies)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.models.issues import (
    ImplementationPlan,
    PlanValidationResult,
    StructuredRequirements,
)
from app.workflows.planning import PlanningWorkflowState


client = TestClient(app)


def _make_successful_state() -> PlanningWorkflowState:
    """Create a successful workflow state for testing."""
    state = PlanningWorkflowState(
        task_title="Add pagination",
        task_description="Add pagination to products API",
        status="completed",
    )
    state.requirements = StructuredRequirements(
        objective="Add pagination to products API",
        requirements=[],
    )
    state.plan = ImplementationPlan(
        summary="Plan summary",
        objective="Add pagination to products API",
        steps=[],
        test_strategy="Add unit and integration tests",
    )
    state.validation = PlanValidationResult(
        is_valid=True,
        checked_step_count=0,
    )
    return state


def _make_failed_state() -> PlanningWorkflowState:
    """Create a failed workflow state for testing."""
    state = PlanningWorkflowState(
        task_title="Failed task",
        task_description="",
        status="failed",
    )
    state.errors = ["Analysis failed"]
    return state


class TestPlanningAPI:
    """Planning API endpoint tests (mocked workflows)."""

    def test_plan_capabilities(self) -> None:
        """GET /api/v1/planning/capabilities should return capabilities."""
        response = client.get("/api/v1/planning/capabilities")
        assert response.status_code == 200

        data = response.json()
        assert data["success"] is True
        capabilities = data["data"]
        assert "phases" in capabilities
        assert "task_sources" in capabilities
        assert "features" in capabilities

    @patch("app.api.v1.planning.PlanningWorkflow")
    def test_plan_from_task_valid(self, MockWorkflow) -> None:
        """Valid task request should return 200 with plan data."""
        mock_instance = AsyncMock()
        mock_instance.run_from_task = AsyncMock(
            return_value=_make_successful_state()
        )
        MockWorkflow.return_value = mock_instance

        response = client.post(
            "/api/v1/planning/plan",
            json={
                "title": "Add pagination",
                "description": "Add pagination to the products API",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["plan"] is not None
        assert data["data"]["requirements"] is not None
        assert data["data"]["validation"] is not None

    @patch("app.api.v1.planning.PlanningWorkflow")
    def test_plan_from_task_failure(self, MockWorkflow) -> None:
        """Workflow failure should return 200 with success=False."""
        mock_instance = AsyncMock()
        mock_instance.run_from_task = AsyncMock(
            return_value=_make_failed_state()
        )
        MockWorkflow.return_value = mock_instance

        response = client.post(
            "/api/v1/planning/plan",
            json={
                "title": "Failed task",
                "description": "",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False

    def test_plan_from_task_empty_title(self) -> None:
        """Empty title should return 422 validation error."""
        response = client.post(
            "/api/v1/planning/plan",
            json={"title": "", "description": "Test"},
        )

        assert response.status_code == 422

    def test_plan_from_task_missing_title(self) -> None:
        """Missing required title should return 422."""
        response = client.post(
            "/api/v1/planning/plan",
            json={"description": "Test"},
        )

        assert response.status_code == 422

    def test_plan_from_task_title_too_long(self) -> None:
        """Title exceeding max length should return 422."""
        response = client.post(
            "/api/v1/planning/plan",
            json={"title": "x" * 1001},
        )

        assert response.status_code == 422

    @patch("app.api.v1.planning.PlanningWorkflow")
    def test_plan_from_github_valid(self, MockWorkflow) -> None:
        """Valid GitHub issue URL should return 200."""
        mock_instance = AsyncMock()
        mock_instance.run_from_github = AsyncMock(
            return_value=_make_successful_state()
        )
        MockWorkflow.return_value = mock_instance

        response = client.post(
            "/api/v1/planning/github/plan",
            json={
                "url": "https://github.com/owner/repo/issues/42",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    @patch("app.api.v1.planning.PlanningWorkflow")
    def test_plan_from_github_invalid_url(self, MockWorkflow) -> None:
        """Invalid URL should be handled gracefully."""
        mock_instance = AsyncMock()
        mock_instance.run_from_github = AsyncMock(
            return_value=_make_failed_state()
        )
        MockWorkflow.return_value = mock_instance

        response = client.post(
            "/api/v1/planning/github/plan",
            json={"url": "not-a-url"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False

    @patch("app.api.v1.planning.PlanningWorkflow")
    def test_plan_from_task_with_repo_path(self, MockWorkflow) -> None:
        """Task with repo_path should work."""
        mock_instance = AsyncMock()
        mock_instance.run_from_task = AsyncMock(
            return_value=_make_successful_state()
        )
        MockWorkflow.return_value = mock_instance

        response = client.post(
            "/api/v1/planning/plan",
            json={
                "title": "Test task",
                "repo_path": "/fake/path",
            },
        )

        assert response.status_code == 200

    @patch("app.api.v1.planning.PlanningWorkflow")
    def test_plan_with_long_description(self, MockWorkflow) -> None:
        """Long description (within limits) should work."""
        mock_instance = AsyncMock()
        mock_instance.run_from_task = AsyncMock(
            return_value=_make_successful_state()
        )
        MockWorkflow.return_value = mock_instance

        response = client.post(
            "/api/v1/planning/plan",
            json={
                "title": "Long description task",
                "description": "x" * 10000,
            },
        )

        assert response.status_code == 200

    def test_plan_description_too_long(self) -> None:
        """Description exceeding 50K chars should return 422."""
        response = client.post(
            "/api/v1/planning/plan",
            json={
                "title": "Test",
                "description": "x" * 50001,
            },
        )

        assert response.status_code == 422
