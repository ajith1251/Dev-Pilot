"""
Repair Workflow — Phase 8 bounded repair loop.

Orchestrates the end-to-end repair flow:
    validate_input → diagnose → repairable? → (NO → STOP, YES → FixAgent)
    → validate_repair → apply → test → evaluate → (PASS → SUCCESS, FAIL → retry?)

Designed as a focused workflow that delegates to TestingService,
FailureDiagnosisService, FixAgent, and RepairService.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.agents.fix_agent import FixAgent
from app.models.coding import PatchApplicationResult, PatchSet
from app.models.issues import ImplementationPlan
from app.models.rag import RetrievedContext
from app.models.repair import (
    FailureDiagnosis,
    RepairResult,
    RepairSessionStatus,
    Repairability,
)
from app.models.testing import TestRunResult
from app.services.failure_diagnosis_service import FailureDiagnosisService
from app.services.repair_service import RepairService


class RepairWorkflow:
    """Repair Workflow — places RepairService into a workflow interface.

    Provides explicit stages for observability and CLI/API consumption.
    """

    def __init__(
        self,
        repair_service: Optional[RepairService] = None,
        diagnosis_service: Optional[FailureDiagnosisService] = None,
        fix_agent: Optional[FixAgent] = None,
    ):
        self._repair_service = repair_service or RepairService()
        self._diagnosis_service = diagnosis_service or FailureDiagnosisService()
        self._fix_agent = fix_agent or FixAgent()

    async def run(
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
    ) -> RepairResult:
        """Run the complete repair workflow.

        This is the primary entry point for Phase 8.
        Delegates to RepairService for the actual loop.
        """
        return await self._repair_service.run_repair(
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

    async def diagnose(
        self,
        test_result: TestRunResult,
        patch_set: Optional[PatchSet] = None,
        patch_result: Optional[PatchApplicationResult] = None,
        plan: Optional[ImplementationPlan] = None,
    ) -> List[FailureDiagnosis]:
        """Diagnose failures without running repair.

        CLI/API preview mode — returns diagnoses for user inspection.
        """
        return self._repair_service.diagnose_only(
            test_result=test_result,
            patch_set=patch_set,
            patch_result=patch_result,
            plan=plan,
        )

    def get_capabilities(self) -> Dict[str, Any]:
        """Return repair capabilities."""
        caps = self._repair_service.get_capabilities()
        return {
            "max_repair_attempts": caps.max_repair_attempts,
            "supported_frameworks": caps.supported_frameworks,
            "diagnosis_categories": caps.diagnosis_categories,
            "repairability_classes": caps.repairability_classes,
            "test_tampering_protection": caps.test_tampering_protection,
            "config_weakening_protection": caps.config_weakening_protection,
            "rollback_supported": caps.rollback_supported,
            "llm_required": caps.llm_required,
        }
