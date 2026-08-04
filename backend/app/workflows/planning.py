"""
Planning Workflow — Phase 4.

Orchestrates the end-to-end planning pipeline:
    START → validate_input → analyze_issue → build_plan → validate_plan → END

Follows the same pattern as RepositoryAnalysisWorkflow and RemoteAnalysisWorkflow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.core.logging import logger
from app.models.issues import (
    ImplementationPlan,
    PlanValidationResult,
    StructuredRequirements,
    TaskInput,
)
from app.services.planning_service import PlanningResult, PlanningService


@dataclass
class PlanningWorkflowState:
    """State for the Phase 4 planning workflow."""

    task_title: str
    task_description: str = ""
    repo_path: Optional[str] = None
    github_url: Optional[str] = None
    github_issue_number: Optional[int] = None

    status: str = "pending"  # pending|running|completed|failed
    task: Optional[TaskInput] = None
    requirements: Optional[StructuredRequirements] = None
    plan: Optional[ImplementationPlan] = None
    validation: Optional[PlanValidationResult] = None
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


class PlanningWorkflow:
    """Workflow for the Phase 4 planning pipeline.

    Current graph (linear):
        START → validate_input → analyze_issue → build_plan → validate_plan → END

    Supports two input modes:
    1. User task (title + description + optional repo_path)
    2. GitHub issue (via url + optional issue_number)

    Future: migrate to langgraph.StateGraph nodes.
    """

    def __init__(
        self,
        planning_service: Optional[PlanningService] = None,
    ) -> None:
        self._service = planning_service or PlanningService()

    async def run_from_task(
        self,
        title: str,
        description: str = "",
        repo_path: Optional[str] = None,
    ) -> PlanningWorkflowState:
        """Run the planning workflow from a user-provided task.

        Args:
            title: Task title.
            description: Task description.
            repo_path: Optional local repository path.

        Returns:
            PlanningWorkflowState with full pipeline result.
        """
        state = PlanningWorkflowState(
            task_title=title,
            task_description=description,
            repo_path=repo_path,
            started_at=datetime.now(timezone.utc).isoformat(),
            status="running",
        )

        logger.info("Planning workflow started (task): %s", title[:80])

        # Node 1: validate_input
        state = self._validate_task_input(state)
        if state.status == "failed":
            return self._finalize(state)

        # Node 2-4: full pipeline
        state = await self._run_pipeline(state)

        return self._finalize(state)

    async def run_from_github(
        self,
        url: str,
        issue_number: Optional[int] = None,
    ) -> PlanningWorkflowState:
        """Run the planning workflow from a GitHub issue URL.

        Args:
            url: GitHub issue URL.
            issue_number: Optionally extract from URL.

        Returns:
            PlanningWorkflowState with full pipeline result.
        """
        from app.services.github import GitHubService

        # Parse URL
        try:
            parsed = GitHubService.parse_any_url(url)
        except Exception as exc:
            state = PlanningWorkflowState(
                task_title=url,
                started_at=datetime.now(timezone.utc).isoformat(),
                status="failed",
                errors=[f"Invalid GitHub URL: {exc}"],
            )
            return self._finalize(state)

        owner = parsed.get("owner", "")
        repo = parsed.get("repo", "")
        number = issue_number or parsed.get("number", 1)

        state = PlanningWorkflowState(
            task_title=f"#{number} from {owner}/{repo}",
            github_url=url,
            github_issue_number=number,
            started_at=datetime.now(timezone.utc).isoformat(),
            status="running",
        )

        logger.info(
            "Planning workflow started (github): %s/%s#%d",
            owner, repo, number,
        )

        # Run pipeline with GitHub issue
        try:
            result = await self._service.plan_from_github_issue(
                owner=owner,
                repo=repo,
                issue_number=number,
                acquire_locally=False,
            )

            state.task = result.task
            state.requirements = result.requirements
            state.plan = result.plan
            state.validation = result.validation

            if result.error:
                state.errors.append(result.error)
                state.status = "failed"
            else:
                state.status = "completed"

            return self._finalize(state)

        except Exception as exc:
            state.errors.append(f"Pipeline failed: {exc}")
            state.status = "failed"
            return self._finalize(state)

    def _validate_task_input(self, state: PlanningWorkflowState) -> PlanningWorkflowState:
        """Node 1: Validate task input."""
        if not state.task_title or not state.task_title.strip():
            state.status = "failed"
            state.errors.append("Task title is required")
            return state

        if len(state.task_title) > 1000:
            state.status = "failed"
            state.errors.append("Task title exceeds maximum length of 1000 characters")
            return state

        if state.task_description and len(state.task_description) > 50_000:
            state.status = "failed"
            state.errors.append("Task description exceeds maximum length of 50,000 characters")
            return state

        return state

    async def _run_pipeline(
        self, state: PlanningWorkflowState
    ) -> PlanningWorkflowState:
        """Run the full planning pipeline."""
        try:
            result = await self._service.plan_from_task(
                title=state.task_title,
                description=state.task_description,
                repo_path=state.repo_path,
            )

            state.task = result.task
            state.requirements = result.requirements
            state.plan = result.plan
            state.validation = result.validation

            if result.error:
                state.errors.append(result.error)
                state.status = "failed"
            else:
                state.status = "completed"

        except Exception as exc:
            state.status = "failed"
            state.errors.append(f"Workflow execution failed: {exc}")
            logger.error("Planning workflow error: %s", exc)

        return state

    def _finalize(self, state: PlanningWorkflowState) -> PlanningWorkflowState:
        """Finalize the workflow state."""
        state.completed_at = datetime.now(timezone.utc).isoformat()

        if state.validation and state.validation.warnings:
            state.warnings = state.validation.warnings

        logger.info(
            "Planning workflow %s: '%s' (errors=%d)",
            state.status, state.task_title[:60], len(state.errors),
        )

        return state
