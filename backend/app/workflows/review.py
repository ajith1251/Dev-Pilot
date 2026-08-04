"""
Review Workflow — Phase 9 review pipeline.

Orchestrates the end-to-end review flow:
    collect_evidence → build_context → assess_requirements →
    deterministic_review → agent_review → validate_findings →
    build_report → quality_gate → END

Designed as a focused workflow that delegates to ReviewService,
ReviewContextBuilder, DeterministicReview, ReviewerAgent, and QualityGate.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.agents.reviewer import ReviewerAgent
from app.models.coding import PatchApplicationResult, PatchSet
from app.models.issues import ImplementationPlan, StructuredRequirements
from app.models.profile import RepositoryProfile
from app.models.rag import RetrievedContext
from app.models.repair import RepairResult
from app.models.review import (
    QualityGateResult,
    ReviewCapabilities,
    ReviewReport,
)
from app.models.testing import TestRunResult
from app.services.review_service import ReviewService


class ReviewWorkflow:
    """Review Workflow — places ReviewService into a workflow interface.

    Provides explicit stages for observability and CLI/API consumption.
    """

    def __init__(
        self,
        review_service: Optional[ReviewService] = None,
        use_llm: bool = False,
    ):
        self._review_service = review_service or ReviewService(use_llm=use_llm)

    async def run(
        self,
        workspace_id: str = "",
        workspace_root: str = "",
        requirements: Optional[StructuredRequirements] = None,
        implementation_plan: Optional[ImplementationPlan] = None,
        original_patch: Optional[PatchSet] = None,
        patch_application: Optional[PatchApplicationResult] = None,
        repair_result: Optional[RepairResult] = None,
        test_result: Optional[TestRunResult] = None,
        repository_profile: Optional[RepositoryProfile] = None,
        retrieved_context: Optional[RetrievedContext] = None,
        changed_files: Optional[List[str]] = None,
        final_workspace_metadata: Optional[Dict[str, Any]] = None,
        extra_context: Optional[Dict[str, Any]] = None,
        use_llm: Optional[bool] = None,
    ) -> Tuple[ReviewReport, QualityGateResult]:
        """Run the complete review workflow.

        This is the primary entry point for Phase 9.
        Delegates to ReviewService for the actual pipeline.
        """
        return await self._review_service.run_review(
            workspace_id=workspace_id,
            workspace_root=workspace_root,
            requirements=requirements,
            implementation_plan=implementation_plan,
            original_patch=original_patch,
            patch_application=patch_application,
            repair_result=repair_result,
            test_result=test_result,
            repository_profile=repository_profile,
            retrieved_context=retrieved_context,
            changed_files=changed_files,
            final_workspace_metadata=final_workspace_metadata,
            extra_context=extra_context,
            use_llm=use_llm,
        )

    def get_capabilities(self) -> ReviewCapabilities:
        """Return review capabilities."""
        return self._review_service.get_capabilities()
