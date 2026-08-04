"""
Coding Workflow — Phase 6.

Orchestrates the end-to-end coding pipeline:
    START → validate_input → prepare_workspace → retrieve_context
    → generate_patch → validate_patch → dry_run → optional_apply → END

Follows the same pattern as PlanningWorkflow (Phase 4).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.core.logging import logger
from app.models.coding import (
    CodingResult,
    PatchApplicationResult,
    PatchSet,
    PatchValidationResult,
    PatchStatus,
)
from app.models.issues import ImplementationPlan, StructuredRequirements
from app.models.rag import RetrievedContext
from app.services.coding_service import CodingService


@dataclass
class CodingWorkflowState:
    """State for the Phase 6 coding workflow."""

    plan: ImplementationPlan
    requirements: StructuredRequirements
    retrieved_context: RetrievedContext
    repository_path: str

    status: str = "pending"  # pending|running|completed|failed
    workspace_id: Optional[str] = None
    workspace_root: Optional[str] = None
    patch_set: Optional[PatchSet] = None
    validation: Optional[PatchValidationResult] = None
    dry_run_result: Optional[PatchApplicationResult] = None
    apply_result: Optional[PatchApplicationResult] = None
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    coding_result: Optional[CodingResult] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


class CodingWorkflow:
    """Workflow for the Phase 6 coding pipeline.

    Current graph (linear):
        START → generate_patch → validate_patch → dry_run → optional_apply → END

    Two modes:
    1. generate_only — produce PatchSet + dry-run (default)
    2. generate_and_apply — produce PatchSet + dry-run + apply to workspace
    """

    def __init__(
        self,
        coding_service: Optional[CodingService] = None,
    ):
        self._service = coding_service or CodingService(
            # Will be created with proper LLM provider by caller
        )

    async def run_generate(
        self,
        plan: ImplementationPlan,
        requirements: StructuredRequirements,
        retrieved_context: RetrievedContext,
        repository_path: str,
    ) -> CodingWorkflowState:
        """Generate a PatchSet and dry-run it. No workspace mutation.

        This is the default safe mode.
        """
        state = CodingWorkflowState(
            plan=plan,
            requirements=requirements,
            retrieved_context=retrieved_context,
            repository_path=repository_path,
            started_at=datetime.now(timezone.utc).isoformat(),
            status="running",
        )

        logger.info("Coding workflow started (generate): %s", plan.plan_id[:40])

        # Node: generate + validate + dry-run
        state = await self._run_generate_pipeline(state)

        return self._finalize(state)

    async def run_apply(
        self,
        plan: ImplementationPlan,
        requirements: StructuredRequirements,
        retrieved_context: RetrievedContext,
        repository_path: str,
    ) -> CodingWorkflowState:
        """Generate, validate, dry-run, then apply to workspace."""
        state = CodingWorkflowState(
            plan=plan,
            requirements=requirements,
            retrieved_context=retrieved_context,
            repository_path=repository_path,
            started_at=datetime.now(timezone.utc).isoformat(),
            status="running",
        )

        logger.info("Coding workflow started (apply): %s", plan.plan_id[:40])

        # Node: generate + validate + dry-run + apply
        state = await self._run_apply_pipeline(state)

        return self._finalize(state)

    async def _run_generate_pipeline(
        self, state: CodingWorkflowState
    ) -> CodingWorkflowState:
        """Generate and validate a patch (no filesystem mutation)."""
        try:
            result = await self._service.generate_and_dry_run(
                plan=state.plan,
                requirements=state.requirements,
                retrieved_context=state.retrieved_context,
                repository_path=state.repository_path,
            )

            state.coding_result = result
            state.patch_set = result.patch_set
            state.validation = result.validation
            state.dry_run_result = result.dry_run_result
            state.workspace_id = result.workspace_id
            state.workspace_root = result.workspace_root

            if result.errors:
                state.errors.extend(result.errors)

            if result.status in ("PROPOSED", "APPLIED"):
                state.status = "completed"
            elif result.status == "INSUFFICIENT_CONTEXT":
                state.status = "completed"  # Still a valid outcome
                state.warnings.append("Insufficient context for patch generation")
            else:
                state.status = "failed"

        except Exception as exc:
            state.status = "failed"
            state.errors.append(f"Generate pipeline failed: {exc}")
            logger.error("Coding workflow generate error: %s", exc)

        return state

    async def _run_apply_pipeline(
        self, state: CodingWorkflowState
    ) -> CodingWorkflowState:
        """Generate, validate, dry-run, and apply patch."""
        try:
            result = await self._service.generate_and_apply(
                plan=state.plan,
                requirements=state.requirements,
                retrieved_context=state.retrieved_context,
                repository_path=state.repository_path,
            )

            state.coding_result = result
            state.patch_set = result.patch_set
            state.validation = result.validation
            state.dry_run_result = result.dry_run_result
            state.apply_result = result.apply_result
            state.workspace_id = result.workspace_id
            state.workspace_root = result.workspace_root

            if result.errors:
                state.errors.extend(result.errors)

            if result.status in ("APPLIED", "PROPOSED"):
                state.status = "completed"
            elif result.status == "INSUFFICIENT_CONTEXT":
                state.status = "completed"
                state.warnings.append("Insufficient context for patch generation")
            else:
                state.status = "failed"

        except Exception as exc:
            state.status = "failed"
            state.errors.append(f"Apply pipeline failed: {exc}")
            logger.error("Coding workflow apply error: %s", exc)

        return state

    def _finalize(self, state: CodingWorkflowState) -> CodingWorkflowState:
        """Finalize workflow state."""
        state.completed_at = datetime.now(timezone.utc).isoformat()

        logger.info(
            "Coding workflow %s: plan=%s (errors=%d, changes=%d)",
            state.status,
            state.plan.plan_id[:40] if hasattr(state.plan, "plan_id") else "n/a",
            len(state.errors),
            len(state.patch_set.changes) if state.patch_set else 0,
        )

        return state
