"""
GitHub API endpoints — Phase 3.

POST /api/v1/github/repositories/analyze — fetch + acquire + analyze a remote repo
GET  /api/v1/github/repositories/{owner}/{repo} — fetch repo metadata
GET  /api/v1/github/repositories/{owner}/{repo}/branches — list branches
GET  /api/v1/github/repositories/{owner}/{repo}/issues — list issues
GET  /api/v1/github/repositories/{owner}/{repo}/issues/{number} — get issue
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.logging import logger
from app.models.base import Response
from app.models.github import (
    GitHubBranch,
    GitHubIssue,
    GitHubRepoMetadata,
    RemoteRepositoryProfile,
)
from app.services.github import GitHubService
from app.workflows.remote_analysis import RemoteAnalysisWorkflow

router = APIRouter(prefix="/api/v1/github", tags=["github"])

# Resource limits
MAX_PAGINATION = 100
MAX_PAGES = 10


class AnalyzeRequest(BaseModel):
    """Request to analyze a remote GitHub repository."""

    repository: str = Field(
        description="GitHub repository URL (e.g. https://github.com/owner/repo)"
    )
    ref: Optional[str] = Field(
        default=None,
        description="Branch, tag, or commit to analyze (defaults to default branch)",
    )
    shallow: bool = Field(
        default=True,
        description="Use shallow clone for faster acquisition",
    )


@router.post("/repositories/analyze", response_model=Response)
async def analyze_github_repository(request: AnalyzeRequest) -> Response:
    """Analyze a remote GitHub repository.

    Fetches metadata, safely acquires a local snapshot, runs the
    Phase 2 Repository Intelligence Engine, then returns combined results.
    Temporary workspace is cleaned up automatically.
    """
    logger.info(
        "API: Remote analysis requested: %s (ref=%s)",
        request.repository, request.ref,
    )

    try:
        workflow = RemoteAnalysisWorkflow()
        state = await workflow.run(
            url=request.repository,
            ref=request.ref,
            shallow=request.shallow,
        )

        if state.errors:
            logger.warning(
                "Remote analysis completed with errors: %s", state.errors
            )

        return Response(
            success=state.status == "completed",
            data=state.result.model_dump() if state.result else None,
            message=f"Analysis {state.status}",
        )

    except Exception as exc:
        logger.error("API: Remote analysis failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Analysis failed: {exc}")


@router.get("/repositories/{owner}/{repo}", response_model=Response)
async def get_repository_metadata(
    owner: str, repo: str
) -> Response:
    """Fetch GitHub repository metadata (read-only)."""
    github = GitHubService()
    try:
        metadata = await github.get_repo_metadata(owner, repo)
        return Response(success=True, data=metadata.model_dump())
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/repositories/{owner}/{repo}/branches", response_model=Response)
async def list_branches(
    owner: str,
    repo: str,
    max_pages: int = Query(default=3, ge=1, le=MAX_PAGES),
) -> Response:
    """List branches for a repository."""
    github = GitHubService()
    try:
        branches = await github.list_branches(owner, repo, max_pages=max_pages)
        return Response(
            success=True,
            data=[b.model_dump() for b in branches],
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/repositories/{owner}/{repo}/issues", response_model=Response)
async def list_issues(
    owner: str,
    repo: str,
    state: str = Query(default="open", pattern="^(open|closed|all)$"),
    max_pages: int = Query(default=3, ge=1, le=MAX_PAGES),
    per_page: int = Query(default=30, ge=1, le=MAX_PAGINATION),
) -> Response:
    """List repository issues (read-only, no AI analysis)."""
    github = GitHubService()
    try:
        issues = await github.list_issues(
            owner, repo, state=state, max_pages=max_pages, per_page=per_page,
        )
        return Response(
            success=True,
            data=[i.model_dump() for i in issues],
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/repositories/{owner}/{repo}/issues/{number}", response_model=Response)
async def get_issue(
    owner: str, repo: str, number: int
) -> Response:
    """Fetch a single issue by number."""
    github = GitHubService()
    try:
        issue = await github.get_issue(owner, repo, number)
        return Response(success=True, data=issue.model_dump())
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
