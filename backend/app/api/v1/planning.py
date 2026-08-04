"""
Planning API endpoints — Phase 4.

POST /api/v1/planning/plan         — Plan from a user task (with optional repo path)
POST /api/v1/planning/github/plan  — Plan from a GitHub issue URL

GET  /api/v1/planning/capabilities — List planning capabilities
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.logging import logger
from app.models.base import Response
from app.workflows.planning import PlanningWorkflow

router = APIRouter(prefix="/api/v1/planning", tags=["planning"])


class PlanFromTaskRequest(BaseModel):
    """Request to plan from a user-provided task."""

    title: str = Field(
        min_length=1, max_length=1000,
        description="Task title (what needs to be done)",
    )
    description: str = Field(
        default="", max_length=50_000,
        description="Detailed task description",
    )
    repo_path: Optional[str] = Field(
        default=None,
        description="Optional local repository path for context",
    )


class PlanFromGitHubRequest(BaseModel):
    """Request to plan from a GitHub issue."""

    url: str = Field(
        description="GitHub issue URL (e.g. https://github.com/owner/repo/issues/42)",
    )
    issue_number: Optional[int] = Field(
        default=None,
        description="Issue number (if not extractable from URL)",
    )


@router.post("/plan", response_model=Response)
async def plan_from_task(request: PlanFromTaskRequest) -> Response:
    """Create an implementation plan from a user-provided task.

    If repo_path is provided, the repository is analyzed for context.
    The pipeline produces: requirements → plan → validation.
    """
    logger.info(
        "API: Planning from task: '%s' (repo=%s)",
        request.title[:80], request.repo_path or "(none)",
    )

    try:
        workflow = PlanningWorkflow()
        state = await workflow.run_from_task(
            title=request.title,
            description=request.description,
            repo_path=request.repo_path,
        )

        result_data = _build_response_data(state)

        return Response(
            success=state.status == "completed",
            data=result_data,
            message=f"Planning {state.status}",
        )

    except Exception as exc:
        logger.error("API: Planning failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Planning failed: {exc}")


@router.post("/github/plan", response_model=Response)
async def plan_from_github(request: PlanFromGitHubRequest) -> Response:
    """Create an implementation plan from a GitHub issue.

    The issue is fetched via the GitHub API, then analyzed through
    the planning pipeline to produce structured requirements and a plan.
    """
    logger.info("API: Planning from GitHub issue: %s", request.url)

    try:
        workflow = PlanningWorkflow()
        state = await workflow.run_from_github(
            url=request.url,
            issue_number=request.issue_number,
        )

        result_data = _build_response_data(state)

        return Response(
            success=state.status == "completed",
            data=result_data,
            message=f"Planning {state.status}",
        )

    except Exception as exc:
        logger.error("API: GitHub planning failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"GitHub planning failed: {exc}")


@router.get("/capabilities", response_model=Response)
async def get_planning_capabilities() -> Response:
    """List planning capabilities."""
    return Response(
        success=True,
        data={
            "phases": ["issue_analysis", "planning", "plan_validation"],
            "task_sources": ["user_task", "github_issue"],
            "features": [
                "structured_requirements",
                "ambiguity_detection",
                "risk_assessment",
                "dependency_management",
                "plan_validation",
                "requirement_traceability",
            ],
        },
    )


def _build_response_data(state: object) -> dict:
    """Build a serializable response dict from workflow state."""
    from app.workflows.planning import PlanningWorkflowState

    if not isinstance(state, PlanningWorkflowState):
        return {"status": "unknown"}

    result: dict = {
        "status": state.status,
        "warnings": state.warnings,
        "errors": state.errors,
    }

    if state.requirements:
        result["requirements"] = state.requirements.model_dump()
    else:
        result["requirements"] = None

    if state.plan:
        plan_dict = state.plan.model_dump()
        # Avoid leaking sensitive plan details in error responses
        if plan_dict.get("error"):
            result["plan_error"] = plan_dict["error"]
        result["plan"] = plan_dict
    else:
        result["plan"] = None

    if state.validation:
        result["validation"] = state.validation.model_dump()

    return result
