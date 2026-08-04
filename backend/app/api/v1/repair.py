"""
Repair API — Phase 8 endpoints.

POST /api/v1/repair/diagnose     — Diagnose test failures (no repair)
POST /api/v1/repair/propose      — Generate a repair proposal (no apply)
POST /api/v1/repair/run           — Execute full bounded repair workflow
GET  /api/v1/repair/capabilities  — List Phase 8 repair capabilities
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException

from app.models.coding import PatchApplicationResult, PatchSet
from app.models.issues import ImplementationPlan
from app.models.rag import RetrievedContext
from app.models.repair import (
    FailureDiagnosis,
    RepairCapabilities,
    RepairProposal,
    RepairResult,
)
from app.models.testing import TestRunResult
from app.workflows.repair import RepairWorkflow

logger = logging.getLogger("devpilot")
router = APIRouter(prefix="/api/v1/repair", tags=["repair"])


# ── Request / Response Models ───────────────────────────────────


class DiagnoseRequest:
    """Request to diagnose test failures."""
    def __init__(
        self,
        test_result: TestRunResult,
        patch_set: Optional[PatchSet] = None,
        patch_result: Optional[PatchApplicationResult] = None,
        plan: Optional[ImplementationPlan] = None,
    ):
        self.test_result = test_result
        self.patch_set = patch_set
        self.patch_result = patch_result
        self.plan = plan


class RunRepairRequest:
    """Request to execute a full bounded repair run."""
    def __init__(
        self,
        workspace_root: str,
        workspace_id: str,
        test_result: TestRunResult,
        patch_set: Optional[PatchSet] = None,
        patch_result: Optional[PatchApplicationResult] = None,
        plan: Optional[ImplementationPlan] = None,
        retrieved_context: Optional[RetrievedContext] = None,
        changed_files: Optional[List[str]] = None,
        max_attempts: Optional[int] = None,
    ):
        self.workspace_root = workspace_root
        self.workspace_id = workspace_id
        self.test_result = test_result
        self.patch_set = patch_set
        self.patch_result = patch_result
        self.plan = plan
        self.retrieved_context = retrieved_context
        self.changed_files = changed_files
        self.max_attempts = max_attempts


# ── Endpoints ───────────────────────────────────────────────────


@router.post("/diagnose")
async def diagnose_failures(
    body: Dict[str, Any],
) -> List[FailureDiagnosis]:
    """Diagnose test failures without running any repair.

    Accepts a TestRunResult and optional patch/plan context.
    Returns structured FailureDiagnosis array for inspection.
    """
    try:
        workflow = RepairWorkflow()

        test_result = TestRunResult.model_validate(body.get("test_result", {}))
        patch_set = PatchSet.model_validate(body["patch_set"]) if body.get("patch_set") else None
        patch_result = PatchApplicationResult.model_validate(body["patch_result"]) if body.get("patch_result") else None
        plan = ImplementationPlan.model_validate(body["plan"]) if body.get("plan") else None

        diagnoses = await workflow.diagnose(
            test_result=test_result,
            patch_set=patch_set,
            patch_result=patch_result,
            plan=plan,
        )
        return [d.model_dump() for d in diagnoses]

    except Exception as exc:
        logger.error("Repair diagnose failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/run")
async def run_repair(
    body: Dict[str, Any],
) -> Dict[str, Any]:
    """Execute a full bounded repair workflow.

    Accepts workspace information, test results, and optional context.
    Returns a RepairResult with session history and final status.
    """
    try:
        workflow = RepairWorkflow()

        workspace_root = body.get("workspace_root", "")
        workspace_id = body.get("workspace_id", "")
        if not workspace_root or not workspace_id:
            raise HTTPException(
                status_code=400,
                detail="workspace_root and workspace_id are required",
            )

        test_result = TestRunResult.model_validate(body.get("test_result", {}))
        patch_set = PatchSet.model_validate(body["patch_set"]) if body.get("patch_set") else None
        patch_result = PatchApplicationResult.model_validate(body["patch_result"]) if body.get("patch_result") else None
        plan = ImplementationPlan.model_validate(body["plan"]) if body.get("plan") else None
        retrieved_context = RetrievedContext.model_validate(body["retrieved_context"]) if body.get("retrieved_context") else None
        changed_files = body.get("changed_files")
        max_attempts = body.get("max_attempts")

        result = await workflow.run(
            workspace_root=workspace_root,
            workspace_id=workspace_id,
            test_result=test_result,
            patch_set=patch_set,
            patch_result=patch_result,
            plan=plan,
            retrieved_context=retrieved_context,
            changed_files=changed_files,
            max_attempts=max_attempts,
        )
        return result.model_dump()

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Repair run failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/capabilities")
async def repair_capabilities() -> Dict[str, Any]:
    """List Phase 8 repair capabilities."""
    try:
        workflow = RepairWorkflow()
        return workflow.get_capabilities()
    except Exception as exc:
        logger.error("Repair capabilities failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
