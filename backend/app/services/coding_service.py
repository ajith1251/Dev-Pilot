"""
Coding Service — Phase 6 Orchestrator

Coordinates:
- Coding Agent (generates PatchSet from plan + context)
- PatchValidator (validates PatchSet)
- SafePatchEngine (dry-run, diff, apply)
- WorkspaceService (isolated writable copy)

Flow:
1. Accept plan + retrieved context + repository path
2. Prepare isolated workspace
3. Build coding context
4. Invoke Coding Agent → PatchSet
5. Validate PatchSet
6. Return PatchSet (and optionally apply)

Does NOT:
- Execute code
- Run tests
- Commit changes
"""

import time
from typing import List, Optional

from app.agents.coding_agent import CodingAgent
from app.core.exceptions import (
    CodingError,
    InsufficientContextError,
    PatchApplicationError,
    PatchValidationError,
    WorkspaceError,
)
from app.models.coding import (
    CodingCapabilities,
    CodingResult,
    FileChange,
    PatchApplicationResult,
    PatchSet,
    PatchStatus,
)

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from pathlib import Path as PathType
from app.models.issues import ImplementationPlan, StructuredRequirements
from app.models.rag import (
    PlanAwareRetrievalInput,
    PlanAwareRetrievalResult,
    RetrievedContext,
)
from app.services.patch_validator import PatchValidator
from app.services.safe_patch_engine import SafePatchEngine
from app.services.workspace_service import WorkspaceService


class CodingService:
    """Orchestrates the Phase 6 coding pipeline.

    Coordinates CodingAgent, PatchValidator, and SafePatchEngine
    to produce validated, traceable code changes.
    """

    def __init__(
        self,
        coding_agent: Optional[CodingAgent] = None,
        workspace_service: Optional[WorkspaceService] = None,
        patch_validator: Optional[PatchValidator] = None,
        patch_engine: Optional[SafePatchEngine] = None,
        max_files_per_patch: int = 20,
        max_file_size: int = 500_000,
    ):
        self._coding_agent = coding_agent
        self._workspace_service = workspace_service or WorkspaceService()
        self._patch_validator = patch_validator or PatchValidator(
            max_file_size=max_file_size,
        )
        self._patch_engine = patch_engine
        self._max_files_per_patch = max_files_per_patch
        self._max_file_size = max_file_size

    async def generate(
        self,
        plan: ImplementationPlan,
        requirements: StructuredRequirements,
        retrieved_context: RetrievedContext,
        repository_path: str,
    ) -> CodingResult:
        """Generate a validated PatchSet from plan + context.

        Steps:
        1. Prepare workspace
        2. Build workspace structure summary
        3. Invoke Coding Agent
        4. Validate resulting PatchSet
        5. Return structured result
        """
        start_time = time.time()

        # Step 1: Prepare workspace
        try:
            workspace = self._workspace_service.create_workspace(repository_path)
        except WorkspaceError as exc:
            return CodingResult(
                status="FAILED",
                plan_id=getattr(plan, "plan_id", ""),
                errors=[str(exc)],
            )

        # Step 2: Build workspace structure
        workspace_structure = self._build_structure_summary(workspace.root)

        # Step 3: Invoke Coding Agent
        if not self._coding_agent:
            return CodingResult(
                status="FAILED",
                plan_id=getattr(plan, "plan_id", ""),
                errors=["Coding Agent is not configured. Provide an LLM provider."],
            )
        try:
            patch_set = await self._coding_agent.generate_patch(
                plan=plan,
                retrieved_context=retrieved_context,
                requirements=requirements,
                workspace_structure=workspace_structure,
            )
        except InsufficientContextError as exc:
            return CodingResult(
                status="INSUFFICIENT_CONTEXT",
                plan_id=getattr(plan, "plan_id", ""),
                errors=[str(exc)],
                warnings=exc.details.get("warnings", []) if exc.details else [],
            )
        except CodingError as exc:
            return CodingResult(
                status="FAILED",
                plan_id=getattr(plan, "plan_id", ""),
                errors=[str(exc)],
            )

        # Step 4: Validate PatchSet
        validation = self._patch_validator.validate_with_workspace(
            patch_set, str(workspace.root)
        )

        if not validation.is_valid:
            return CodingResult(
                status="REJECTED",
                plan_id=getattr(plan, "plan_id", ""),
                patch_set=patch_set,
                validation=validation,
                errors=validation.errors,
                warnings=validation.warnings,
                duration=time.time() - start_time,
            )

        duration = time.time() - start_time
        return CodingResult(
            status="PROPOSED",
            plan_id=getattr(plan, "plan_id", ""),
            patch_set=patch_set,
            validation=validation,
            workspace_id=workspace.workspace_id,
            workspace_root=str(workspace.root),
            duration=duration,
        )

    async def dry_run(
        self,
        patch_set: PatchSet,
        workspace_root: str,
    ) -> PatchApplicationResult:
        """Perform a dry run of the patch — no filesystem changes."""
        engine = self._get_engine(workspace_root)
        return engine.dry_run(patch_set)

    async def apply(
        self,
        patch_set: PatchSet,
        workspace_root: str,
    ) -> PatchApplicationResult:
        """Apply a validated patch to a writable workspace.

        Requires explicit apply request (not automatic after generate).
        """
        engine = self._get_engine(workspace_root)
        return engine.apply(patch_set)

    async def generate_and_dry_run(
        self,
        plan: ImplementationPlan,
        requirements: StructuredRequirements,
        retrieved_context: RetrievedContext,
        repository_path: str,
    ) -> CodingResult:
        """Generate a PatchSet and immediately dry-run it. No mutations."""
        result = await self.generate(plan, requirements, retrieved_context, repository_path)

        if result.patch_set and result.validation and result.validation.is_valid:
            dry_result = await self.dry_run(
                result.patch_set,
                result.workspace_root or repository_path,
            )
            result.dry_run_result = dry_result

        return result

    async def generate_and_apply(
        self,
        plan: ImplementationPlan,
        requirements: StructuredRequirements,
        retrieved_context: RetrievedContext,
        repository_path: str,
    ) -> CodingResult:
        """Generate a PatchSet, validate, dry-run, then apply."""
        result = await self.generate(plan, requirements, retrieved_context, repository_path)

        if not result.workspace_root:
            return result

        if result.patch_set and result.validation and result.validation.is_valid:
            dry_result = await self.dry_run(
                result.patch_set,
                result.workspace_root,
            )
            result.dry_run_result = dry_result

            if dry_result.status in (PatchStatus.REJECTED, PatchStatus.ROLLED_BACK):
                result.status = "FAILED"
                result.errors = result.errors + dry_result.errors
                return result

            apply_result = await self.apply(
                result.patch_set,
                result.workspace_root,
            )
            result.apply_result = apply_result

            if apply_result.status == PatchStatus.APPLIED:
                result.status = "APPLIED"
            else:
                result.status = "FAILED"
                result.errors = result.errors + apply_result.errors

        return result

    def _get_engine(self, workspace_root: str) -> SafePatchEngine:
        """Get or create a SafePatchEngine for the given workspace."""
        if self._patch_engine:
            return SafePatchEngine(
                workspace_root=workspace_root,
                validator=self._patch_validator,
                max_file_size=self._max_file_size,
            )
        return SafePatchEngine(
            workspace_root=workspace_root,
            validator=self._patch_validator,
            max_file_size=self._max_file_size,
        )

    def _build_structure_summary(self, workspace_root) -> str:
        """Build a concise structural summary of the workspace."""
        import os
        from pathlib import Path

        root = Path(workspace_root)
        lines = []
        lines.append(f"Root: {root}")

        # Collect files by directory
        dirs = sorted(set(
            p.parent.relative_to(root)
            for p in root.rglob("*")
            if p.is_file() and not any(
                part.startswith(".") for part in p.relative_to(root).parts
            )
        ))

        for directory in dirs[:30]:
            dir_path = root / directory
            files = sorted(
                f.name for f in dir_path.iterdir() if f.is_file()
                and not f.name.startswith(".")
            )
            if files:
                prefix = f"  {directory}/" if str(directory) != "." else "  ./"
                for fname in files[:15]:
                    lines.append(f"{prefix}{fname}")

        lines.append("")
        return "\n".join(lines)

    async def get_capabilities(self) -> CodingCapabilities:
        """Return current Phase 6 capabilities."""
        return CodingCapabilities(
            supported_operations=["CREATE", "MODIFY", "DELETE"],
            max_files_per_patch=self._max_files_per_patch,
            max_file_size=self._max_file_size,
            dry_run_supported=True,
            diff_format="unified",
            rollback_supported=True,
            workspace_isolation=True,
            delete_enabled=False,
        )
