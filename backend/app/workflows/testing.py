"""
Testing Workflow — Phase 7.

Orchestrates the end-to-end testing pipeline:

    START → validate_workspace → discover_commands
    → build_execution_plan → validate_policy
    → execute → parse_results → normalize → END

Follows the same pattern as CodingWorkflow (Phase 6).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.logging import logger
from app.models.coding import PatchApplicationResult
from app.models.testing import (
    CommandCandidate,
    ExecutionPlan,
    ExecutionStatus,
    ProcessExecutionResult,
    TestFailure,
    TestRunResult,
)
from app.services.testing_service import TestingService


@dataclass
class TestingWorkflowState:
    """State for the Phase 7 testing workflow."""

    workspace_id: str
    workspace_root: str

    status: str = "pending"
    candidates: List[CommandCandidate] = field(default_factory=list)
    plan: Optional[ExecutionPlan] = None
    test_result: Optional[TestRunResult] = None
    process_results: List[ProcessExecutionResult] = field(default_factory=list)
    failures: List[TestFailure] = field(default_factory=list)

    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    changed_files: List[str] = field(default_factory=list)
    patch_result: Optional[PatchApplicationResult] = None

    started_at: Optional[str] = None
    completed_at: Optional[str] = None


class TestingWorkflow:
    """Workflow for the Phase 7 testing pipeline.

    Linear graph:
        validate_workspace → discover_commands → build_plan
        → validate_policy → execute → parse_results → normalize → END
    """

    def __init__(
        self,
        testing_service: Optional[TestingService] = None,
    ):
        self._service = testing_service or TestingService()

    async def run(
        self,
        workspace_id: str,
        workspace_root: str,
        changed_files: Optional[List[str]] = None,
        patch_result: Optional[PatchApplicationResult] = None,
    ) -> TestingWorkflowState:
        """Run the full testing workflow.

        Steps:
        1. Validate workspace
        2. Discover commands
        3. Build execution plan
        4. Validate against policy
        5. Execute
        6. Parse and normalize results
        """
        state = TestingWorkflowState(
            workspace_id=workspace_id,
            workspace_root=workspace_root,
            changed_files=changed_files or [],
            patch_result=patch_result,
            started_at=datetime.now(timezone.utc).isoformat(),
            status="running",
        )

        logger.info(
            "Testing workflow started: ws=%s root=%s",
            workspace_id,
            workspace_root[:80],
        )

        # Node 1: Validate workspace
        if not self._validate_workspace(state):
            return self._finalize(state)

        # Node 2: Discover commands
        if not self._discover_commands(state):
            return self._finalize(state)

        # Node 3: Build execution plan
        if not self._build_plan(state):
            return self._finalize(state)

        # Node 4: Validate policy
        if not self._validate_policy(state):
            return self._finalize(state)

        # Node 5: Execute
        if not await self._execute(state):
            return self._finalize(state)

        # Node 6: Finalize (parse + normalize already happened in _execute)
        state.status = "completed"

        return self._finalize(state)

    def _validate_workspace(self, state: TestingWorkflowState) -> bool:
        """Node 1: Validate the workspace exists and is accessible."""
        ws = Path(state.workspace_root)
        if not ws.exists():
            state.errors.append(f"Workspace does not exist: {state.workspace_root}")
            state.status = "failed"
            return False
        if not ws.is_dir():
            state.errors.append(f"Workspace is not a directory: {state.workspace_root}")
            state.status = "failed"
            return False

        logger.info("Testing workflow: workspace validated")
        return True

    def _discover_commands(self, state: TestingWorkflowState) -> bool:
        """Node 2: Discover candidate commands from workspace."""
        try:
            if state.patch_result:
                candidates = self._service.discover_from_patch(
                    state.workspace_root, state.patch_result
                )
            else:
                candidates = self._service.discover_commands(state.workspace_root)

            state.candidates = candidates

            if not candidates:
                state.warnings.append("No test commands discovered")
                state.status = "completed"
                return False

            logger.info(
                "Testing workflow: discovered %d commands",
                len(candidates),
            )
            return True

        except Exception as exc:
            state.errors.append(f"Command discovery failed: {exc}")
            state.status = "failed"
            return False

    def _build_plan(self, state: TestingWorkflowState) -> bool:
        """Node 3: Build execution plan from candidates."""
        try:
            plan = self._service.build_plan(
                workspace_id=state.workspace_id,
                workspace_root=state.workspace_root,
                candidates=state.candidates,
                changed_files=state.changed_files,
            )
            state.plan = plan

            logger.info(
                "Testing workflow: plan built with %d steps",
                len(plan.steps),
            )
            return True

        except Exception as exc:
            state.errors.append(f"Plan building failed: {exc}")
            state.status = "failed"
            return False

    def _validate_policy(self, state: TestingWorkflowState) -> bool:
        """Node 4: Validate plan steps against execution policy."""
        if not state.plan:
            state.errors.append("No plan to validate")
            state.status = "failed"
            return False

        try:
            package_scripts = TestingService.load_package_scripts(
                Path(state.workspace_root)
            )
            is_valid, reasons = self._service.validate_plan(
                state.plan, package_scripts
            )

            if not is_valid:
                for reason in reasons:
                    state.warnings.append(reason)
                state.status = "failed"
                return False

            logger.info("Testing workflow: policy validation passed")
            return True

        except Exception as exc:
            state.errors.append(f"Policy validation failed: {exc}")
            state.status = "failed"
            return False

    async def _execute(self, state: TestingWorkflowState) -> bool:
        """Node 5: Execute the plan and collect results."""
        if not state.plan:
            state.errors.append("No plan to execute")
            state.status = "failed"
            return False

        try:
            # Add the secret canary for testing
            extra_env = {
                "DEVPILOT_SECRET_CANARY": "phase7-test-secret-do-not-expose",
            }

            test_result = await self._service.run_tests(
                plan=state.plan,
                extra_env=extra_env,
            )

            state.test_result = test_result
            state.process_results = test_result.process_results
            state.failures = test_result.failures

            logger.info(
                "Testing workflow: execution complete — status=%s "
                "commands=%d failures=%d duration=%.2fs",
                test_result.status.value,
                test_result.commands_total,
                len(test_result.failures),
                test_result.duration_seconds,
            )

            if test_result.warnings:
                state.warnings.extend(test_result.warnings)

            # Don't fail the workflow if tests fail — that's expected
            return True

        except Exception as exc:
            state.errors.append(f"Execution failed: {exc}")
            state.status = "failed"
            return False

    def _finalize(self, state: TestingWorkflowState) -> TestingWorkflowState:
        """Finalize workflow state with timing info."""
        state.completed_at = datetime.now(timezone.utc).isoformat()

        if state.status == "running":
            state.status = "completed"

        logger.info(
            "Testing workflow %s: ws=%s commands=%d errors=%d",
            state.status,
            state.workspace_id,
            len(state.candidates),
            len(state.errors),
        )

        return state
