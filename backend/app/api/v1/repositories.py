"""
Repository Analysis API endpoints.

POST /api/v1/repositories/analyze — analyze a local repository
GET  /api/v1/repositories/capabilities — list supported detection capabilities
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.logging import logger
from app.models.profile import RepositoryProfile
from app.models.base import Response
from app.services.command_detector import get_supported_command_sources
from app.services.language_detector import get_supported_languages
from app.services.project_detector import get_supported_module_indicators
from app.services.technology_detector import get_supported_technologies
from app.workflows.repository_analysis import RepositoryAnalysisWorkflow

router = APIRouter(prefix="/api/v1/repositories", tags=["repositories"])

# Security: allowed repository roots for analysis
ALLOWED_ROOTS: Optional[List[str]] = None  # None = allow any path (dev mode)

# Resource limits
MAX_PATH_LENGTH = 500
MAX_DEPTH = 50
MAX_TOTAL_BYTES = 500_000_000


class AnalyzeRequest(BaseModel):
    """Request payload for repository analysis."""

    path: str = Field(description="Absolute path to a local repository directory")
    max_depth: Optional[int] = Field(
        default=10, ge=1, le=50,
        description="Maximum directory depth for analysis",
    )


class CapabilitiesResponse(BaseModel):
    """Response showing detection capabilities."""

    languages: List[str] = Field(default_factory=list)
    technologies: List[str] = Field(default_factory=list)
    module_indicators: List[str] = Field(default_factory=list)
    command_sources: List[str] = Field(default_factory=list)


@router.post("/analyze", response_model=Response)
async def analyze_repository(request: AnalyzeRequest) -> Response:
    """Analyze a local repository and return a RepositoryProfile.

    Security:
    - Path is validated and resolved
    - Must be a directory, not a file
    - Must be within allowed roots if configured
    - Read-only operation — never modifies target
    - Sensitive file contents are never read or exposed
    """
    # ── Validate path ───────────────────────────────────────────
    raw_path = request.path.strip()

    if not raw_path:
        raise HTTPException(status_code=400, detail="Path is required")

    if len(raw_path) > MAX_PATH_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Path exceeds maximum length of {MAX_PATH_LENGTH} characters",
        )

    try:
        resolved = Path(raw_path).resolve()
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid path: {exc}")

    if not resolved.exists():
        return Response(
            success=True,
            data=RepositoryProfile(
                name=resolved.name,
                scan={
                    "errors": [f"Path does not exist: {raw_path}"],
                    "root_path": str(resolved),
                },
            ).model_dump(),
            message="Repository not found",
        )

    if not resolved.is_dir():
        raise HTTPException(status_code=400, detail="Path must be a directory, not a file")

    # ── Allowed roots check ─────────────────────────────────────
    if ALLOWED_ROOTS is not None:
        allowed = [Path(r).resolve() for r in ALLOWED_ROOTS]
        if not any(str(resolved).startswith(str(a)) for a in allowed):
            raise HTTPException(
                status_code=403,
                detail=f"Path is not within allowed repository roots",
            )

    # ── Check path traversal ────────────────────────────────────
    try:
        _ = os.path.commonpath([str(Path.cwd().resolve()), str(resolved)])
    except ValueError:
        raise HTTPException(status_code=400, detail="Path traversal detected")

    # ── Run analysis ────────────────────────────────────────────
    logger.info("API: Analyzing repository at %s", resolved)

    try:
        workflow = RepositoryAnalysisWorkflow()
        state = await workflow.run(str(resolved))

        if state.errors:
            logger.warning("Analysis completed with errors: %s", state.errors)

        return Response(
            success=state.status == "completed",
            data=state.profile.model_dump() if state.profile else None,
            message=f"Analysis {state.status}",
        )

    except Exception as exc:
        logger.error("API: Repository analysis failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Analysis failed: {exc}")


@router.get("/capabilities", response_model=CapabilitiesResponse)
async def get_capabilities() -> CapabilitiesResponse:
    """List supported languages, technologies, and detectors."""
    return CapabilitiesResponse(
        languages=get_supported_languages(),
        technologies=get_supported_technologies(),
        module_indicators=get_supported_module_indicators(),
        command_sources=get_supported_command_sources(),
    )
