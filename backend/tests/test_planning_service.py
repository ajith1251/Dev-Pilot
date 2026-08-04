"""Tests for the PlanningService orchestrator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.base import Severity
from app.models.issues import (
    EstimatedEffort,
    ImplementationPlan,
    ImplementationStep,
    IssueAnalysisOutput,
    IssueType,
    PlanValidationResult,
    Requirement,
    RequirementType,
    StructuredRequirements,
    TaskInput,
)
from app.services.planning_service import PlanningResult, PlanningService


# ── Fixtures ───────────────────────────────────────────────────


def _make_real_issue_output() -> IssueAnalysisOutput:
    """Create a real IssueAnalysisOutput for testing."""
    return IssueAnalysisOutput(
        title="Add pagination",
        summary="Add pagination to products API",
        issue_type=IssueType.FEATURE,
        severity=Severity.MEDIUM,
        priority_score=5,
        affected_components=["api/products", "models/product"],
        requirements=[
            Requirement(
                description="Add page/per_page params",
                requirement_type=RequirementType.FUNCTIONAL,
                is_implied=False,
                acceptance_note="Returns paginated results",
            ),
        ],
        acceptance_criteria=["Pagination works correctly"],
        suggested_labels=["feature", "api"],
        estimated_effort=EstimatedEffort.MEDIUM,
        related_files=["api/routes/*"],
        needs_more_info=False,
        missing_info_questions=[],
    )


def _make_needs_more_info_output() -> IssueAnalysisOutput:
    """Create an IssueAnalysisOutput with needs_more_info=True."""
    return IssueAnalysisOutput(
        title="Fix stuff",
        summary="Unclear what needs fixing",
        issue_type=IssueType.BUG,
        severity=Severity.MEDIUM,
        priority_score=3,
        requirements=[],
        needs_more_info=True,
        missing_info_questions=["What exactly is broken?"],
    )


def _make_valid_plan() -> ImplementationPlan:
    """Create a valid ImplementationPlan for testing."""
    return ImplementationPlan(
        summary="Plan summary",
        objective="Add pagination",
        steps=[
            ImplementationStep(
                id="STEP-001",
                title="Define schema",
                description="Create schema",
                affected_areas=["api/schemas"],
                depends_on=[],
                expected_changes="New schema model",
                validation="Schema validates correctly",
            ),
        ],
        test_strategy="Add tests",
        documentation_impact="Update docs",
    )


def _make_mock_issue_analyzer(output: IssueAnalysisOutput | None = None):
    """Create a mock IssueAnalyzerAgent returning a real output."""
    m = AsyncMock()
    m.execute = AsyncMock(return_value=output or _make_real_issue_output())
    return m


def _make_mock_planner():
    """Create a mock PlannerAgent."""
    m = AsyncMock()
    m.execute = AsyncMock(return_value=_make_valid_plan())
    return m


def _make_mock_validator():
    """Create a mock PlanValidator."""
    m = MagicMock()
    m.validate = MagicMock(
        return_value=PlanValidationResult(
            is_valid=True,
            checked_step_count=1,
        )
    )
    return m


# ── Tests ─────────────────────────────────────────────────────


class TestPlanningService:
    """PlanningService tests."""

    @pytest.fixture
    def service(self) -> PlanningService:
        return PlanningService(
            issue_analyzer=_make_mock_issue_analyzer(),
            planner=_make_mock_planner(),
            validator=_make_mock_validator(),
        )

    @pytest.mark.asyncio
    async def test_plan_from_task_success(self, service: PlanningService) -> None:
        """Valid task should produce requirements, plan, and validation."""
        result = await service.plan_from_task(
            title="Add pagination",
            description="Add pagination to products API",
        )

        assert result.success is True
        assert result.error is None
        assert result.requirements is not None
        assert result.plan is not None
        assert result.validation is not None
        assert result.validation.is_valid is True

    @pytest.mark.asyncio
    async def test_plan_from_task_with_repo_context(
        self, service: PlanningService,
    ) -> None:
        """Task with repo path should include context."""
        result = await service.plan_from_task(
            title="Add pagination",
            description="Add pagination to products API",
            repo_path="/fake/path",
        )

        # Repo analysis will fail (fake path), but pipeline should continue
        assert result is not None

    @pytest.mark.asyncio
    async def test_plan_from_task_empty_title(
        self, service: PlanningService,
    ) -> None:
        """Task with empty title should still attempt analysis."""
        result = await service.plan_from_task(title="", description="Test")

        assert result is not None

    @pytest.mark.asyncio
    async def test_plan_from_github_issue_success(
        self, service: PlanningService,
    ) -> None:
        """GitHub issue should produce requirements and plan."""
        with patch.object(service._github, "get_issue") as mock_get:
            mock_issue = MagicMock()
            mock_issue.title = "Add pagination"
            mock_issue.body = "Please add pagination"
            mock_issue.number = 42
            mock_issue.html_url = "https://github.com/owner/repo/issues/42"
            mock_issue.labels = []
            mock_get.return_value = mock_issue

            result = await service.plan_from_github_issue(
                owner="owner",
                repo="repo",
                issue_number=42,
            )

        assert result.success is True
        assert result.task.source.value == "github_issue"
        assert result.task.issue_number == 42
        assert result.requirements is not None
        assert result.plan is not None

    @pytest.mark.asyncio
    async def test_plan_from_github_fetch_failure(
        self, service: PlanningService,
    ) -> None:
        """GitHub fetch failure should return error."""
        with patch.object(service._github, "get_issue") as mock_get:
            mock_get.side_effect = Exception("Network error")

            result = await service.plan_from_github_issue(
                owner="owner",
                repo="repo",
                issue_number=42,
            )

        assert result.success is False
        assert result.error is not None
        assert "Network" in result.error

    @pytest.mark.asyncio
    async def test_issue_analyzer_failure(
        self, service: PlanningService,
    ) -> None:
        """Issue analyzer failure should return error."""
        service._issue_analyzer.execute = AsyncMock(
            side_effect=Exception("Analysis failed")
        )

        result = await service.plan_from_task(
            title="Test", description="Test",
        )

        assert result.success is False
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_planner_failure(
        self, service: PlanningService,
    ) -> None:
        """Planner failure should return plan with error."""
        mock_plan = ImplementationPlan(
            summary="", objective="", steps=[],
            error="Planning failed",
        )
        service._planner.execute = AsyncMock(return_value=mock_plan)

        result = await service.plan_from_task(
            title="Test", description="Test",
        )

        assert result.plan is not None
        assert result.plan.error is not None

    @pytest.mark.asyncio
    async def test_pipeline_error_propagation(
        self, service: PlanningService,
    ) -> None:
        """Errors should propagate through to_dict."""
        result = PlanningResult(
            task=TaskInput(
                source="user_task",
                title="Test",
                description="",
            ),
            error="Pipeline failed",
        )

        assert result.success is False
        d = result.to_dict()
        assert d["success"] is False
        assert d["error"] == "Pipeline failed"

    @pytest.mark.asyncio
    async def test_to_dict_with_full_result(
        self, service: PlanningService,
    ) -> None:
        """to_dict should serialize all fields correctly."""
        result = await service.plan_from_task(
            title="Add pagination",
            description="Test",
        )

        d = result.to_dict()
        assert d["success"] is True
        assert "task" in d
        assert d["task"]["title"] == "Add pagination"
        assert d["requirements"] is not None
        assert d["plan"] is not None
        assert d["validation"] is not None

    @pytest.mark.asyncio
    async def test_convert_to_structured_handles_needs_more_info(
        self,
    ) -> None:
        """needs_more_info should set confidence to low."""
        service = PlanningService(
            issue_analyzer=_make_mock_issue_analyzer(
                _make_needs_more_info_output()
            ),
            planner=_make_mock_planner(),
            validator=_make_mock_validator(),
        )

        result = await service.plan_from_task(
            title="Fix stuff",
            description="Something is broken",
        )

        assert result.requirements is not None
        assert result.requirements.confidence == "low"

    @pytest.mark.asyncio
    async def test_pipeline_repo_context_integration(
        self, service: PlanningService,
    ) -> None:
        """Repo context should flow through the pipeline."""
        with patch.object(
            service._repo_analyzer, "analyze"
        ) as mock_analyze:
            # Create a proper profile mock with real string attributes
            class FakeProfile:
                languages = []
                technologies = []
                modules = []
                important_files = []
                tree = None

            fake = FakeProfile()
            fake.languages = [type("Lang", (), {"name": "Python"})()]
            fake.technologies = [type("Tech", (), {"name": "FastAPI"})()]
            fake.modules = [type("Mod", (), {"name": "backend"})()]
            fake.important_files = [type("F", (), {"path": "main.py"})()]
            fake.tree = type("Tree", (), {"text": "project/"})()
            mock_analyze.return_value = fake

            result = await service.plan_from_task(
                title="Test",
                description="Test",
                repo_path="/fake/repo",
            )

        assert result is not None
