"""Tests for the Phase 4 Planning Workflow."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.issues import (
    ImplementationPlan,
    PlanValidationResult,
    StructuredRequirements,
    TaskInput,
)
from app.services.planning_service import PlanningResult
from app.workflows.planning import PlanningWorkflow, PlanningWorkflowState


# ── Tests ─────────────────────────────────────────────────────


class TestPlanningWorkflow:
    """PlanningWorkflow tests."""

    @pytest.fixture
    def workflow(self) -> PlanningWorkflow:
        workflow = PlanningWorkflow()
        mock_service = AsyncMock()

        # Mock plan_from_task
        mock_service.plan_from_task = AsyncMock(
            return_value=PlanningResult(
                task=TaskInput(source="user_task", title="Test task", description=""),
                requirements=StructuredRequirements(
                    objective="Test objective",
                    requirements=[],
                ),
                plan=ImplementationPlan(
                    summary="Test plan",
                    objective="Test objective",
                    steps=[],
                ),
                validation=PlanValidationResult(
                    is_valid=True,
                    checked_step_count=0,
                ),
            )
        )

        # Mock plan_from_github_issue
        mock_service.plan_from_github_issue = AsyncMock(
            return_value=PlanningResult(
                task=TaskInput(
                    source="github_issue",
                    title="GitHub issue",
                    description="",
                    issue_number=42,
                    repository="owner/repo",
                ),
                requirements=StructuredRequirements(
                    objective="GitHub objective",
                    requirements=[],
                ),
                plan=ImplementationPlan(
                    summary="GitHub plan",
                    objective="GitHub objective",
                    steps=[],
                ),
                validation=PlanValidationResult(
                    is_valid=True,
                    checked_step_count=0,
                ),
            )
        )

        workflow._service = mock_service
        return workflow

    @pytest.mark.asyncio
    async def test_run_from_task_success(self, workflow: PlanningWorkflow) -> None:
        """Valid task should complete the workflow successfully."""
        state = await workflow.run_from_task(
            title="Add pagination",
            description="Add pagination to products API",
        )

        assert state.status == "completed"
        assert len(state.errors) == 0
        assert state.requirements is not None
        assert state.plan is not None
        assert state.validation is not None
        assert state.validation.is_valid is True

    @pytest.mark.asyncio
    async def test_run_from_task_empty_title(self, workflow: PlanningWorkflow) -> None:
        """Empty title should fail validation."""
        state = await workflow.run_from_task(title="")

        assert state.status == "failed"
        assert len(state.errors) > 0

    @pytest.mark.asyncio
    async def test_run_from_task_very_long_title(
        self, workflow: PlanningWorkflow,
    ) -> None:
        """Very long title should fail validation."""
        state = await workflow.run_from_task(title="x" * 1001)

        assert state.status == "failed"
        assert any("title" in e.lower() for e in state.errors)

    @pytest.mark.asyncio
    async def test_run_from_github_success(self, workflow: PlanningWorkflow) -> None:
        """Valid GitHub issue URL should complete successfully."""
        state = await workflow.run_from_github(
            url="https://github.com/owner/repo/issues/42",
        )

        assert state.status == "completed"
        assert state.requirements is not None
        assert state.plan is not None

    @pytest.mark.asyncio
    async def test_run_from_github_invalid_url(
        self, workflow: PlanningWorkflow,
    ) -> None:
        """Invalid GitHub URL should fail."""
        state = await workflow.run_from_github(url="not-a-url")

        assert state.status == "failed"
        assert len(state.errors) > 0

    @pytest.mark.asyncio
    async def test_workflow_state_timestamps(
        self, workflow: PlanningWorkflow,
    ) -> None:
        """Workflow state should have timestamps."""
        state = await workflow.run_from_task(title="Test", description="Test")

        assert state.started_at is not None
        assert state.completed_at is not None
        assert state.started_at < state.completed_at

    @pytest.mark.asyncio
    async def test_workflow_with_repo_path(self, workflow: PlanningWorkflow) -> None:
        """Workflow should accept repo_path."""
        state = await workflow.run_from_task(
            title="Test",
            description="Test",
            repo_path="/fake/path",
        )

        assert state.status == "completed"  # Fake path won't cause workflow failure

    @pytest.mark.asyncio
    async def test_workflow_pipeline_failure(
        self, workflow: PlanningWorkflow,
    ) -> None:
        """Pipeline failure should set status to failed."""
        workflow._service.plan_from_task = AsyncMock(
            side_effect=Exception("Pipeline crashed")
        )

        state = await workflow.run_from_task(title="Test", description="Test")

        assert state.status == "failed"
        assert any("Pipeline" in e for e in state.errors)

    @pytest.mark.asyncio
    async def test_requirements_propagation(
        self, workflow: PlanningWorkflow,
    ) -> None:
        """Requirements should propagate through workflow state."""
        state = await workflow.run_from_task(title="Test", description="Test")

        assert state.requirements is not None
        assert state.requirements.objective == "Test objective"

    @pytest.mark.asyncio
    async def test_plan_propagation(
        self, workflow: PlanningWorkflow,
    ) -> None:
        """Plan should propagate through workflow state."""
        state = await workflow.run_from_task(title="Test", description="Test")

        assert state.plan is not None
        assert state.plan.summary == "Test plan"

    @pytest.mark.asyncio
    async def test_validation_propagation(
        self, workflow: PlanningWorkflow,
    ) -> None:
        """Validation should propagate through workflow state."""
        state = await workflow.run_from_task(title="Test", description="Test")

        assert state.validation is not None
        assert state.validation.is_valid is True
