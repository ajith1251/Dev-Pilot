"""
Remote Analysis Workflow — Phase 3.

Orchestrates the end-to-end remote repository analysis:
    START → validate_github_input → fetch_metadata → resolve_ref →
    acquire_repository → analyze_repository → validate_result →
    cleanup → END

Failures at any node still trigger cleanup.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

from app.core.logging import logger
from app.models.github import RemoteRepositoryProfile
from app.services.acquisition import RepositoryAcquisitionService
from app.services.github import GitHubService
from app.services.remote_analyzer import RemoteRepositoryAnalyzer


@dataclass
class RemoteAnalysisState:
    """State for the remote analysis workflow."""

    url: str
    ref: Optional[str] = None
    status: str = "pending"  # pending|running|completed|failed
    result: Optional[RemoteRepositoryProfile] = None
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


class RemoteAnalysisWorkflow:
    """Workflow for remote GitHub repository analysis.

    Current graph (linear — follows same pattern as RepositoryAnalysisWorkflow):
        START → validate_github_input → fetch_metadata → resolve_ref →
        acquire_repository → analyze_repository → validate_result →
        cleanup → END

    Future: migrate to langgraph.StateGraph nodes.
    """

    def __init__(
        self,
        remote_analyzer: Optional[RemoteRepositoryAnalyzer] = None,
    ) -> None:
        self._remote_analyzer = remote_analyzer or RemoteRepositoryAnalyzer()

    async def run(
        self,
        url: str,
        ref: Optional[str] = None,
        shallow: bool = True,
    ) -> RemoteAnalysisState:
        """Execute the remote analysis workflow.

        Args:
            url: GitHub repository URL.
            ref: Branch/ref to analyze (optional).
            shallow: Use shallow clone.

        Returns:
            RemoteAnalysisState with full result or errors.
        """
        state = RemoteAnalysisState(
            url=url,
            ref=ref,
            started_at=datetime.now(timezone.utc).isoformat(),
            status="running",
        )

        logger.info("Remote workflow started: %s (ref=%s)", url, ref)

        # Node 1: validate_github_input
        state = self._validate_input(state)
        if state.status == "failed":
            state.completed_at = datetime.now(timezone.utc).isoformat()
            return state

        # Node 2: analyze (runs the full pipeline)
        state = await self._run_analysis(state, shallow)

        state.status = "completed" if not state.errors else "failed"
        state.completed_at = datetime.now(timezone.utc).isoformat()

        logger.info(
            "Remote workflow %s: %s (ref=%s, errors=%d)",
            state.status, url, ref, len(state.errors),
        )

        return state

    def _validate_input(self, state: RemoteAnalysisState) -> RemoteAnalysisState:
        """Node 1: Validate the GitHub URL."""
        url = state.url.strip()

        if not url:
            state.status = "failed"
            state.errors.append("GitHub URL is required")
            return state

        if not url.startswith("https://github.com/"):
            state.status = "failed"
            state.errors.append(
                f"Invalid GitHub URL: must start with https://github.com/ — got: {url[:50]}"
            )
            return state

        # Basic structural validation
        parts = url.replace("https://github.com/", "").split("/")
        if len(parts) < 2 or not parts[0] or not parts[1]:
            state.status = "failed"
            state.errors.append(
                f"Invalid GitHub URL: missing owner or repository — {url}"
            )
            return state

        return state

    async def _run_analysis(
        self,
        state: RemoteAnalysisState,
        shallow: bool,
    ) -> RemoteAnalysisState:
        """Node 2: Run the full remote analysis pipeline."""
        try:
            result = await self._remote_analyzer.analyze(
                url=state.url,
                ref=state.ref,
                shallow=shallow,
            )

            state.result = result

            if result.errors:
                state.errors.extend(result.errors)

            if result.warnings:
                state.warnings.extend(result.warnings)

        except Exception as exc:
            state.status = "failed"
            state.errors.append(f"Remote analysis failed: {exc}")
            logger.error("Remote analysis workflow error: %s", exc)

        return state
