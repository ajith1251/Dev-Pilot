"""
Coding API endpoints — Phase 6.

POST /api/v1/coding/generate         — Generate a PatchSet (no filesystem mutation)
POST /api/v1/coding/dry-run          — Dry-run an existing PatchSet
POST /api/v1/coding/apply            — Apply a PatchSet to a workspace (requires explicit intent)
GET  /api/v1/coding/capabilities     — List coding capabilities
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.logging import logger
from app.models.base import Response
from app.workflows.coding import CodingWorkflow

router = APIRouter(prefix="/api/v1/coding", tags=["coding"])


class GenerateRequest(BaseModel):
    """Request to generate code changes from a plan and context."""

    plan: dict = Field(description="Serialized ImplementationPlan from Phase 4")
    requirements: dict = Field(description="Serialized StructuredRequirements")
    retrieved_context: dict = Field(
        description="Serialized RetrievedContext from Phase 5"
    )
    repository_path: str = Field(
        description="Path to the source repository for workspace preparation"
    )


class DryRunRequest(BaseModel):
    """Request to dry-run an existing PatchSet."""

    patch_set: dict = Field(description="Serialized PatchSet to dry-run")
    workspace_root: str = Field(
        description="Workspace root path (from a previous generate response)"
    )


class ApplyRequest(BaseModel):
    """Request to apply a PatchSet to a workspace.

    This requires explicit intent. Generation does NOT automatically mutate.
    """

    patch_set: dict = Field(description="Serialized PatchSet to apply")
    workspace_root: str = Field(
        description="Workspace root path (from a previous generate response)"
    )


@router.post("/generate", response_model=Response)
async def generate_changes(request: GenerateRequest) -> Response:
    """Generate a PatchSet from plan + context + requirements.

    Default behavior: generate + validate + dry-run.
    Does NOT modify any files by default.
    Returns the PatchSet, validation result, and dry-run diff.
    """
    logger.info(
        "API: Generating code changes for repo: %s",
        request.repository_path[:100],
    )

    try:
        # Deserialize models
        from app.models.issues import ImplementationPlan, StructuredRequirements
        from app.models.rag import RetrievedContext

        plan = ImplementationPlan(**request.plan)
        requirements = StructuredRequirements(**request.requirements)
        retrieved_context = RetrievedContext(**request.retrieved_context)

        workflow = CodingWorkflow()
        state = await workflow.run_generate(
            plan=plan,
            requirements=requirements,
            retrieved_context=retrieved_context,
            repository_path=request.repository_path,
        )

        return Response(
            success=state.status == "completed",
            data=_build_response_data(state),
            message=f"Coding generation {state.status}",
        )

    except Exception as exc:
        logger.error("API: Code generation failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Code generation failed: {exc}")


@router.post("/dry-run", response_model=Response)
async def dry_run_patch(request: DryRunRequest) -> Response:
    """Dry-run an existing PatchSet against a workspace.

    Returns diffs and validation results but makes NO filesystem changes.
    """
    logger.info("API: Dry-run patch against workspace")

    try:
        from app.models.coding import PatchSet
        from app.services.coding_service import CodingService

        patch_set = PatchSet(**request.patch_set)

        service = CodingService()
        result = await service.dry_run(patch_set, request.workspace_root)

        return Response(
            success=result.status in (PatchStatus.DRY_RUN, PatchStatus.REJECTED),
            data=result.model_dump(),
            message=f"Dry-run {result.status.value}",
        )

    except Exception as exc:
        logger.error("API: Dry-run failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Dry-run failed: {exc}")


@router.post("/apply", response_model=Response)
async def apply_patch(request: ApplyRequest) -> Response:
    """Apply a PatchSet to a workspace.

    Requires explicit apply request. Safety checks are performed:
    - Patch validation
    - Path safety
    - Hash verification
    - Protected file checks
    - Transactional rollback on failure
    """
    logger.info("API: Apply patch to workspace (EXPLICIT APPLY)")

    try:
        from app.models.coding import PatchSet, PatchStatus
        from app.services.coding_service import CodingService

        patch_set = PatchSet(**request.patch_set)

        service = CodingService()
        result = await service.apply(patch_set, request.workspace_root)

        return Response(
            success=result.status == PatchStatus.APPLIED,
            data=result.model_dump(),
            message=f"Apply {result.status.value}",
        )

    except Exception as exc:
        logger.error("API: Apply failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Apply failed: {exc}")


@router.get("/capabilities", response_model=Response)
async def get_coding_capabilities() -> Response:
    """List Phase 6 coding capabilities."""
    from app.services.coding_service import CodingService

    service = CodingService()
    caps = await service.get_capabilities()

    return Response(
        success=True,
        data=caps.model_dump(),
    )


def _build_response_data(state: object) -> dict:
    """Build a serializable response dict from workflow state."""
    from app.workflows.coding import CodingWorkflowState

    if not isinstance(state, CodingWorkflowState):
        return {"status": "unknown"}

    result: dict = {
        "status": state.status,
        "warnings": state.warnings,
        "errors": state.errors,
    }

    if state.patch_set:
        result["patch_set"] = state.patch_set.model_dump()
    else:
        result["patch_set"] = None

    if state.validation:
        result["validation"] = state.validation.model_dump()

    if state.dry_run_result:
        result["dry_run"] = state.dry_run_result.model_dump()

    if state.apply_result:
        result["apply"] = state.apply_result.model_dump()

    result["workspace_id"] = state.workspace_id
    result["workspace_root"] = state.workspace_root

    return result
