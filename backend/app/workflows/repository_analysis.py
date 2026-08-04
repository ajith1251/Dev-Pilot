"""
Repository Analysis Workflow.

Establishes the LangGraph-ready workflow architecture for repository analysis.
Uses a state-based pattern that can be migrated to actual LangGraph StateGraph
when orchestration requires conditional routing and loops.

Current implementation: lightweight async pipeline (no LangGraph runtime dependency).
Future: Replace with actual langgraph.StateGraph when multi-agent orchestration is added.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

from app.core.logging import logger
from app.models.profile import RepositoryProfile
from app.services.repository_analyzer import RepositoryAnalyzer


@dataclass
class AnalysisState:
    """State for the repository analysis workflow.

    Follows the TypedDict pattern used in parent repo LangGraph agents
    (01-web-research, 13-customer-support, 19-competitive-analysis).
    """

    repository_path: str
    status: str = "pending"  # pending|running|completed|failed
    profile: Optional[RepositoryProfile] = None
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


class RepositoryAnalysisWorkflow:
    """Workflow for repository analysis.

    Current graph (linear):
        START → validate_repository → analyze_repository → validate_profile → END

    Future LangGraph extension:
        Replace this class with langgraph.StateGraph nodes while keeping
        the same state model and service calls.
    """

    def __init__(self, analyzer: Optional[RepositoryAnalyzer] = None) -> None:
        self._analyzer = analyzer or RepositoryAnalyzer()

    async def run(self, repo_path: str) -> AnalysisState:
        """Execute the repository analysis workflow.

        Args:
            repo_path: Path to the local repository.

        Returns:
            AnalysisState with the full workflow result.
        """
        state = AnalysisState(
            repository_path=repo_path,
            started_at=datetime.now(timezone.utc).isoformat(),
            status="running",
        )

        logger.info("Workflow started for: %s", repo_path)

        # Node 1: validate_repository
        state = self._validate_repository(state)
        if state.status == "failed":
            state.completed_at = datetime.now(timezone.utc).isoformat()
            return state

        # Node 2: analyze_repository
        state = await self._analyze_repository(state)
        if state.status == "failed":
            state.completed_at = datetime.now(timezone.utc).isoformat()
            return state

        # Node 3: validate_profile
        state = self._validate_profile(state)

        state.status = "completed"
        state.completed_at = datetime.now(timezone.utc).isoformat()

        logger.info(
            "Workflow completed for: %s (%d files, %d langs)",
            repo_path,
            state.profile.scan.total_files_scanned if state.profile else 0,
            len(state.profile.languages) if state.profile else 0,
        )

        return state

    def _validate_repository(self, state: AnalysisState) -> AnalysisState:
        """Node 1: Validate that the repository exists and is accessible."""
        import os
        from pathlib import Path

        path = Path(state.repository_path).resolve()

        if not path.exists():
            state.status = "failed"
            state.errors.append(f"Path does not exist: {state.repository_path}")
            return state

        if not path.is_dir():
            state.status = "failed"
            state.errors.append(f"Path is not a directory: {state.repository_path}")
            return state

        if not os.access(str(path), os.R_OK):
            state.status = "failed"
            state.errors.append(f"Permission denied: {state.repository_path}")
            return state

        # Check if directory appears to be a valid repo (has at least some files)
        try:
            entries = list(path.iterdir())
            if not entries:
                state.warnings.append("Repository appears to be empty")
        except PermissionError:
            state.status = "failed"
            state.errors.append(f"Cannot list directory: {state.repository_path}")
            return state

        return state

    async def _analyze_repository(self, state: AnalysisState) -> AnalysisState:
        """Node 2: Run the full repository analysis."""
        try:
            profile = self._analyzer.analyze(state.repository_path)
            state.profile = profile

            if profile.scan.errors:
                state.errors.extend(profile.scan.errors)

            if profile.scan.warnings:
                state.warnings.extend(profile.scan.warnings)

        except Exception as exc:
            state.status = "failed"
            state.errors.append(f"Analysis failed: {exc}")
            logger.error("Repository analysis failed for %s: %s", state.repository_path, exc)

        return state

    def _validate_profile(self, state: AnalysisState) -> AnalysisState:
        """Node 3: Validate the generated profile has reasonable data."""
        if not state.profile:
            state.warnings.append("No profile was generated")
            return state

        if state.profile.scan.total_files_scanned == 0:
            state.warnings.append("No files were scanned — repository may be empty or inaccessible")

        if not state.profile.languages:
            state.warnings.append("No programming languages detected")

        return state
