"""
Review API — Phase 9 endpoints.

POST /api/v1/review/run           — Execute full review pipeline
GET  /api/v1/review/capabilities  — List Phase 9 review capabilities
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException

from app.models.coding import PatchApplicationResult, PatchSet
from app.models.issues import ImplementationPlan, StructuredRequirements
from app.models.rag import RetrievedContext
from app.models.repair import RepairResult
from app.models.review import ReviewCapabilities
from app.models.testing import TestRunResult
from app.workflows.review import ReviewWorkflow

logger = logging.getLogger("devpilot")
router = APIRouter(prefix="/api/v1/review", tags=["review"])


@router.post("/run")
async def run_review(
    body: Dict[str, Any],
) -> Dict[str, Any]:
    """Execute full Phase 9 review workflow.

    Accepts requirements, plan, patch, test results, and repair results.
    Returns both the ReviewReport and QualityGateResult.
    """
    try:
        workflow = ReviewWorkflow()

        # Parse optional inputs from request body
        requirements = (
            StructuredRequirements.model_validate(body["requirements"])
            if body.get("requirements")
            else None
        )
        implementation_plan = (
            ImplementationPlan.model_validate(body["implementation_plan"])
            if body.get("implementation_plan")
            else None
        )
        original_patch = (
            PatchSet.model_validate(body["original_patch"])
            if body.get("original_patch")
            else None
        )
        patch_application = (
            PatchApplicationResult.model_validate(body["patch_application"])
            if body.get("patch_application")
            else None
        )
        repair_result = (
            RepairResult.model_validate(body["repair_result"])
            if body.get("repair_result")
            else None
        )
        test_result = (
            TestRunResult.model_validate(body["test_result"])
            if body.get("test_result")
            else None
        )
        retrieved_context = (
            RetrievedContext.model_validate(body["retrieved_context"])
            if body.get("retrieved_context")
            else None
        )

        report, gate = await workflow.run(
            workspace_id=body.get("workspace_id", ""),
            workspace_root=body.get("workspace_root", ""),
            requirements=requirements,
            implementation_plan=implementation_plan,
            original_patch=original_patch,
            patch_application=patch_application,
            repair_result=repair_result,
            test_result=test_result,
            retrieved_context=retrieved_context,
            changed_files=body.get("changed_files"),
            use_llm=body.get("use_llm"),
        )

        return {
            "report": report.model_dump(),
            "gate_result": gate.model_dump(),
        }

    except Exception as exc:
        logger.error("Review run failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/capabilities")
async def review_capabilities() -> Dict[str, Any]:
    """List Phase 9 review capabilities."""
    try:
        workflow = ReviewWorkflow()
        return workflow.get_capabilities().model_dump()
    except Exception as exc:
        logger.error("Review capabilities failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
