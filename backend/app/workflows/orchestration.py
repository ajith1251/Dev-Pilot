"""
Orchestration Workflow — Phase 10/11 workflow entry point.

Wraps OrchestrationService with a standard workflow interface.
Phase 11: Uses PostgresRunStore when DATABASE_URL is configured,
integrates recovery/resume, and all store operations are async.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.core.logging import logger
from app.models.orchestration import (
    DevPilotRun,
    DevPilotRunResult,
    OrchestrationCapabilities,
    RepositoryPatchInput,
    RepositorySpec,
    RunSource,
    RunSourceType,
)
from app.services.orchestration_service import OrchestrationService
from app.config import settings
from app.services.run_store import InMemoryRunStore


class OrchestrationWorkflow:
    """Workflow entry point for Phase 10/11 orchestration.

    Uses PostgresRunStore when DATABASE_URL is configured,
    otherwise falls back to InMemoryRunStore.
    """

    def __init__(
        self,
        orchestration_service: Optional[OrchestrationService] = None,
    ) -> None:
        if orchestration_service is None:
            run_store = self._create_run_store()
            self._orchestrator = OrchestrationService(
                run_store=run_store,
            )
        else:
            self._orchestrator = orchestration_service

    @staticmethod
    def _create_run_store():
        """Create a run store based on configuration.

        Returns PostgresRunStore if DATABASE_URL is configured,
        otherwise InMemoryRunStore.
        """
        if settings.DATABASE_URL:
            try:
                from app.services.postgres_run_store import PostgresRunStore
                logger.info("Using PostgresRunStore for persistent storage")
                return PostgresRunStore()
            except Exception as exc:
                logger.warning("Failed to create PostgresRunStore: %s", exc)
                return InMemoryRunStore()
        return InMemoryRunStore()

    async def run_user_task(
        self,
        title: str,
        description: str = "",
        repository_path: Optional[str] = None,
        workspace_root: Optional[str] = None,
        repositories: Optional[List["RepositorySpec"]] = None,
        repo_patches: Optional[List["RepositoryPatchInput"]] = None,
    ) -> DevPilotRunResult:
        """Run end-to-end pipeline from a user task.

        ``repositories`` optionally declares auxiliary repositories to
        materialize + link via the org graph (Phase 20).
        ``repo_patches`` optionally seeds per-repository patches that are
        validated + applied against each repository's OWN checkout only
        (Phase 20A4).
        """
        source = RunSource(
            source_type=RunSourceType.USER_TASK,
            title=title,
            description=description,
            repository_path=repository_path,
            repositories=repositories,
            repo_patches=repo_patches,
        )
        run = await self._orchestrator.create_run(source)
        logger.info("Created run %s: %s", run.run_id, title[:100])
        return await self._orchestrator.execute_run(
            run_id=run.run_id,
            workspace_root=workspace_root or repository_path,
        )

    async def run_github_issue(
        self,
        repo_url: str,
        issue_number: int,
        title: str = "",
        description: str = "",
        repositories: Optional[List["RepositorySpec"]] = None,
        repo_patches: Optional[List["RepositoryPatchInput"]] = None,
    ) -> DevPilotRunResult:
        """Run end-to-end pipeline from a GitHub issue.

        ``repositories`` optionally declares auxiliary repositories to
        materialize + link via the org graph (Phase 20).
        ``repo_patches`` optionally seeds per-repository patches that are
        validated + applied against each repository's OWN checkout only
        (Phase 20A4).
        """
        source = RunSource(
            source_type=RunSourceType.GITHUB_ISSUE,
            title=title or f"Issue #{issue_number}",
            description=description,
            repository_path=repo_url,
            repositories=repositories,
            issue_number=issue_number,
            repo_patches=repo_patches,
        )
        run = await self._orchestrator.create_run(source)
        logger.info("Created run %s: %s #%d", run.run_id, repo_url, issue_number)
        return await self._orchestrator.execute_run(run_id=run.run_id)

    # ── Delegated methods (all async now) ──────────────────────

    async def get_run(self, run_id: str) -> Optional[DevPilotRun]:
        return await self._orchestrator.get_run(run_id)

    async def list_runs(
        self,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        sort_by: str = "newest",
        created_after: Optional[str] = None,
        created_before: Optional[str] = None,
    ) -> List[DevPilotRun]:
        return await self._orchestrator.list_runs(
            status=status, limit=limit, offset=offset, sort_by=sort_by,
            created_after=created_after, created_before=created_before,
        )

    async def list_runs_with_stats(
        self,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        sort_by: str = "newest",
        created_after: Optional[str] = None,
        created_before: Optional[str] = None,
    ) -> tuple[List[DevPilotRun], int, Dict[str, int]]:
        """List runs + total count + unfiltered stats — batched in one session."""
        return await self._orchestrator.list_runs_with_stats(
            status=status, limit=limit, offset=offset, sort_by=sort_by,
            created_after=created_after, created_before=created_before,
        )

    async def count_runs(self, status: Optional[str] = None, created_after: Optional[str] = None, created_before: Optional[str] = None) -> int:
        """Count runs, with optional status filter and date range."""
        return await self._orchestrator.count_runs(status=status, created_after=created_after, created_before=created_before)

    async def get_run_stats(self) -> Dict[str, int]:
        """Get aggregate run counts by status across all runs."""
        return await self._orchestrator.get_run_stats()

    async def get_events(self, run_id: str) -> List[Dict[str, Any]]:
        return await self._orchestrator.get_events(run_id)

    async def request_cancellation(self, run_id: str) -> bool:
        return await self._orchestrator.request_cancellation(run_id)

    def get_capabilities(self) -> Dict[str, Any]:
        caps = self._orchestrator.get_capabilities()
        # Update persistence mode based on store type
        caps_dict = caps.model_dump()
        caps_dict["persistence_mode"] = (
            "postgresql" if settings.DATABASE_URL else "in_memory"
        )
        return caps_dict

    # ── Phase 11: Recovery & Resume ─────────────────────────────

    async def check_recovery(self) -> Dict[str, Any]:
        """Check for recoverable runs on startup."""
        return await self._orchestrator.check_recovery()

    async def resume_run(
        self,
        run_id: str,
        workspace_root: Optional[str] = None,
    ) -> Optional[DevPilotRunResult]:
        """Resume a previously interrupted run."""
        return await self._orchestrator.resume_run(
            run_id=run_id,
            workspace_root=workspace_root,
        )
