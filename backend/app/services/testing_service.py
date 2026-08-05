"""
Testing Service — Phase 7 Orchestrator

Coordinates:
    - Command discovery (reuses Phase 2 intelligence where available)
    - Execution planning (Test Agent or deterministic builder)
    - Execution policy validation
    - Controlled execution (ControlledExecutionEngine)
    - Result parsing and normalization
    - Phase 6 integration (workspaces, patches)

Flow:
    1. Accept workspace + optional patch information
    2. Discover candidate commands from workspace
    3. Build execution plan (Test Agent or deterministic)
    4. Validate each step against execution policy
    5. Execute approved steps via ControlledExecutionEngine
    6. Parse each result with appropriate parser
    7. Normalize into TestRunResult
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from app.config import settings
from app.core.exceptions import (
    ExecutionRejectedError,
    EnvironmentNotReadyError,
)
from app.models.base import new_id
from app.models.coding import PatchApplicationResult
from app.models.testing import (
    CommandCandidate,
    CommandCategory,
    CommandSource,
    ExecutionPlan,
    ExecutionStatus,
    ExecutionStep,
    ProcessExecutionResult,
    TestFailure,
    TestRunResult,
    TestingCapabilities,
)
from app.services.controlled_execution_engine import ControlledExecutionEngine
from app.services.execution_policy import ExecutionPolicy, create_default_policy
from app.testing.parsers.base import TestResultParser
from app.testing.parsers.generic_parser import GenericResultParser
from app.testing.parsers.jest_json_parser import JestJsonParser
from app.testing.parsers.pytest_parser import PytestResultParser
from app.testing.parsers.unittest_xml_parser import UnittestXMLParser
from app.testing.parsers.vitest_json_parser import VitestJsonParser


class TestingService:
    """Orchestrates the Phase 7 testing pipeline.

    Does NOT automatically fix failures (Phase 8 boundary).

    Workspace tracking supports two modes:
    - In-memory: using `_active_workspaces` dict (default, dev/CI)
    - Persistent: using a RunStore that supports workspace methods (when available)

    When a persistent RunStore is provided, workspace registrations are
    written to the database so they survive backend restarts.
    """

    def __init__(
        self,
        execution_engine: Optional[ControlledExecutionEngine] = None,
        execution_policy: Optional[ExecutionPolicy] = None,
        parsers: Optional[List[TestResultParser]] = None,
        run_store: Optional[Any] = None,
        test_selector: Optional[Any] = None,
    ):
        self._engine = execution_engine or ControlledExecutionEngine(
            default_timeout=settings.TEST_DEFAULT_TIMEOUT,
            max_output_bytes=settings.TEST_MAX_OUTPUT_BYTES,
        )
        self._policy = execution_policy or create_default_policy()

        # Register parsers in priority order (most specific first)
        self._parsers = parsers or [
            PytestResultParser(),
            UnittestXMLParser(),
            VitestJsonParser(),
            JestJsonParser(),
            GenericResultParser(),
        ]

        # Track active workspaces (in-memory fallback, persistent when run_store available)
        self._active_workspaces: Dict[str, str] = {}  # workspace_id -> root_path
        self._run_store = run_store

        # Phase 15 (12d): impact-driven test selection. When provided, used
        # to target tests that cover changed code via the semantic graph.
        self._test_selector = test_selector

    # ── Command Discovery ────────────────────────────────────────

    def discover_commands(self, workspace_root: str) -> List[CommandCandidate]:
        """Discover candidate test/build/lint commands from workspace.

        Inspects configuration files and known patterns.
        """
        candidates: List[CommandCandidate] = []
        ws = Path(workspace_root)
        if not ws.is_dir():
            return candidates

        # 1. Check pyproject.toml for pytest
        pyproject_path = ws / "pyproject.toml"
        if pyproject_path.exists():
            content = self._safe_read(pyproject_path)
            if content and ("[tool.pytest" in content or "pytest" in content):
                candidates.append(CommandCandidate(
                    command_id=f"cmd-{new_id()[:8]}",
                    category=CommandCategory.TEST,
                    executable="python",
                    arguments=["-m", "pytest", "-q"],
                    source=CommandSource.PYPROJECT,
                    confidence=0.9,
                    reason="pyproject.toml contains pytest configuration",
                ))

        # 2. Check pytest.ini
        if (ws / "pytest.ini").exists():
            if not any(c.executable == "python" and "-m" in c.arguments for c in candidates):
                candidates.append(CommandCandidate(
                    command_id=f"cmd-{new_id()[:8]}",
                    category=CommandCategory.TEST,
                    executable="python",
                    arguments=["-m", "pytest", "-q"],
                    source=CommandSource.CONFIG,
                    confidence=0.9,
                    reason="pytest.ini detected",
                ))

        # 3. Check setup.cfg for pytest
        setup_cfg = ws / "setup.cfg"
        if setup_cfg.exists():
            content = self._safe_read(setup_cfg)
            if content and "[tool:pytest]" in content:
                if not any(c.executable == "python" and "-m" in c.arguments for c in candidates):
                    candidates.append(CommandCandidate(
                        command_id=f"cmd-{new_id()[:8]}",
                        category=CommandCategory.TEST,
                        executable="python",
                        arguments=["-m", "pytest", "-q"],
                        source=CommandSource.CONFIG,
                        confidence=0.8,
                        reason="setup.cfg contains [tool:pytest]",
                    ))

        # 4. Check package.json for test/lint/build scripts
        pkg_json_path = ws / "package.json"
        package_scripts: Dict[str, str] = {}
        if pkg_json_path.exists():
            content = self._safe_read(pkg_json_path)
            if content:
                try:
                    data = json.loads(content)
                    scripts = data.get("scripts", {})
                    package_scripts = scripts
                    for name, script in scripts.items():
                        if name == "test":
                            candidates.append(CommandCandidate(
                                command_id=f"cmd-{new_id()[:8]}",
                                category=CommandCategory.TEST,
                                executable="npm",
                                arguments=["test"],
                                source=CommandSource.PACKAGE_JSON,
                                confidence=0.9,
                                reason=f"package.json script: test",
                                metadata={"script_content": script},
                            ))
                        elif name.startswith("test:"):
                            candidates.append(CommandCandidate(
                                command_id=f"cmd-{new_id()[:8]}",
                                category=CommandCategory.TEST,
                                executable="npm",
                                arguments=["run", name],
                                source=CommandSource.PACKAGE_JSON,
                                confidence=0.8,
                                reason=f"package.json script: {name}",
                                metadata={"script_content": script},
                            ))
                        elif name in ("lint", "format") and settings.TEST_ALLOW_LINT:
                            candidates.append(CommandCandidate(
                                command_id=f"cmd-{new_id()[:8]}",
                                category=CommandCategory.LINT,
                                executable="npm",
                                arguments=["run", name],
                                source=CommandSource.PACKAGE_JSON,
                                confidence=0.8,
                                reason=f"package.json script: {name}",
                                metadata={"script_content": script},
                            ))
                        elif name in ("typecheck", "type-check") and settings.TEST_ALLOW_TYPECHECK:
                            candidates.append(CommandCandidate(
                                command_id=f"cmd-{new_id()[:8]}",
                                category=CommandCategory.TYPECHECK,
                                executable="npm",
                                arguments=["run", name],
                                source=CommandSource.PACKAGE_JSON,
                                confidence=0.8,
                                reason=f"package.json script: {name}",
                                metadata={"script_content": script},
                            ))
                        elif name in ("build", "compile") and settings.TEST_ALLOW_BUILD:
                            candidates.append(CommandCandidate(
                                command_id=f"cmd-{new_id()[:8]}",
                                category=CommandCategory.BUILD,
                                executable="npm",
                                arguments=["run", name],
                                source=CommandSource.PACKAGE_JSON,
                                confidence=0.7,
                                reason=f"package.json script: {name}",
                                metadata={"script_content": script},
                            ))
                except json.JSONDecodeError:
                    pass

        # 5. Default Python fallback (if Python detected but no pytest config)
        if not any(c.executable == "python" and "-m" in c.arguments for c in candidates):
            has_python = self._detect_python(ws)
            if has_python:
                candidates.append(CommandCandidate(
                    command_id=f"cmd-{new_id()[:8]}",
                    category=CommandCategory.TEST,
                    executable="python",
                    arguments=["-m", "pytest", "-q"],
                    source=CommandSource.DEFAULT_FRAMEWORK_RULE,
                    confidence=0.5,
                    reason="Python project detected — default pytest suggestion",
                ))

        return candidates

    # ── Execution Plan Building ──────────────────────────────────

    def build_plan(
        self,
        workspace_id: str,
        workspace_root: str,
        candidates: List[CommandCandidate],
        changed_files: Optional[List[str]] = None,
    ) -> ExecutionPlan:
        """Build a deterministic execution plan from candidates.

        Orders commands, applies limits, and returns a validated plan.
        """
        steps: List[ExecutionStep] = []
        step_index = 0

        # Track what we've already added to avoid duplicates
        added_commands: Set[str] = set()

        for candidate in candidates:
            # Skip if we've already added this command
            cmd_key = f"{candidate.executable}:{' '.join(candidate.arguments)}"
            if cmd_key in added_commands:
                continue

            # Skip low-confidence candidates when higher-confidence ones exist
            if candidate.confidence < 0.4:
                continue

            # Check command limit
            if step_index >= settings.TEST_MAX_COMMANDS:
                break

            step = ExecutionStep(
                step_id=f"STEP-{step_index + 1:03d}",
                category=candidate.category,
                executable=candidate.executable,
                arguments=candidate.arguments,
                working_directory=candidate.working_directory,
                timeout_seconds=settings.TEST_DEFAULT_TIMEOUT,
                required=(candidate.category == CommandCategory.TEST),
                source=candidate.source,
                reason=candidate.reason,
                metadata=candidate.metadata,
            )

            steps.append(step)
            added_commands.add(cmd_key)
            step_index += 1

        return ExecutionPlan(
            plan_id=f"plan-{new_id()[:8]}",
            workspace_id=workspace_id,
            workspace_root=workspace_root,
            steps=steps,
            max_total_timeout_seconds=settings.TEST_DEFAULT_TIMEOUT * min(
                len(steps) + 1, 10
            ),
        )

    # ── Plan Validation ──────────────────────────────────────────

    def validate_plan(
        self,
        plan: ExecutionPlan,
        package_scripts: Optional[Dict[str, str]] = None,
    ) -> Tuple[bool, List[str]]:
        """Validate all steps in a plan against execution policy.

        Returns:
            Tuple of (is_valid, reasons list). Empty reasons = valid.
        """
        reasons: List[str] = []

        for i, step in enumerate(plan.steps):
            is_allowed, reason = self._policy.validate(
                step=step,
                workspace_root=plan.workspace_root,
                package_scripts=package_scripts,
                plan_step_index=i,
            )
            if not is_allowed:
                reasons.append(f"[{step.step_id}] {reason}")

        return len(reasons) == 0, reasons

    # ── Full Test Run ────────────────────────────────────────────

    async def run_tests(
        self,
        plan: ExecutionPlan,
        extra_env: Optional[Dict[str, str]] = None,
    ) -> TestRunResult:
        """Execute all steps in a plan and return normalized results.

        This is the main entry point for Phase 7 execution.
        """
        run_id = f"run-{new_id()[:8]}"
        start_time = time.time()
        process_results: List[ProcessExecutionResult] = []
        all_failures: List[TestFailure] = []
        warnings: List[str] = []

        total_passed = 0
        total_failed = 0
        total_skipped = 0
        overall_status = ExecutionStatus.PASSED

        # Validate workspace
        ws = Path(plan.workspace_root)
        if not ws.is_dir():
            return TestRunResult(
                run_id=run_id,
                workspace_id=plan.workspace_id,
                status=ExecutionStatus.ENVIRONMENT_NOT_READY,
                summary=f"Workspace not found: {plan.workspace_root}",
            )

        # Check for missing dependencies (no automatic install)
        has_python_test_deps = self._check_python_test_deps(ws)
        if not has_python_test_deps:
            warnings.append(
                "pytest may not be installed. "
                "Dependencies must be installed before running tests."
            )

        # Load package scripts for policy validation
        package_scripts = self.load_package_scripts(ws)

        for i, step in enumerate(plan.steps):
            # Validate step against policy
            is_allowed, reason = self._policy.validate(
                step=step,
                workspace_root=plan.workspace_root,
                package_scripts=package_scripts,
                plan_step_index=i,
            )
            if not is_allowed:
                total_skipped += 1
                process_results.append(ProcessExecutionResult(
                    step_id=step.step_id,
                    command=f"{step.executable} {' '.join(step.arguments)}",
                    category=step.category,
                    status=ExecutionStatus.REJECTED,
                    exit_code=None,
                    stdout="",
                    stderr=reason,
                ))
                if step.required:
                    overall_status = ExecutionStatus.FAILED
                continue

            # Execute step
            result = await self._engine.execute(
                step=step,
                workspace_root=plan.workspace_root,
                extra_env=extra_env,
            )
            process_results.append(result)

            # Parse result
            status, tests_total, tests_passed, tests_failed, tests_skipped, failures = (
                self._parse_result(result)
            )

            all_failures.extend(failures)

            if status == ExecutionStatus.PASSED:
                total_passed += 1
            else:
                total_failed += 1
                if step.required:
                    overall_status = ExecutionStatus.FAILED

        # Determine overall status
        if any(p.status == ExecutionStatus.TIMEOUT for p in process_results):
            overall_status = ExecutionStatus.TIMEOUT
        elif any(p.status == ExecutionStatus.ERROR for p in process_results):
            overall_status = ExecutionStatus.ERROR
        elif total_failed > 0:
            overall_status = ExecutionStatus.FAILED
        elif total_skipped > 0 and total_passed == 0:
            overall_status = ExecutionStatus.SKIPPED

        duration = time.time() - start_time

        # Build summary
        parts = []
        if total_passed > 0:
            parts.append(f"{total_passed} passed")
        if total_failed > 0:
            parts.append(f"{total_failed} failed")
        if total_skipped > 0:
            parts.append(f"{total_skipped} skipped")
        if all_failures:
            parts.append(f"{len(all_failures)} failures")

        summary_parts = []
        if parts:
            summary_parts.append(f"Commands: {', '.join(parts)}")
        summary_parts.append(f"Duration: {duration:.2f}s")
        if warnings:
            summary_parts.append(f"Warnings: {len(warnings)}")

        return TestRunResult(
            run_id=run_id,
            workspace_id=plan.workspace_id,
            status=overall_status,
            commands_total=len(process_results),
            commands_passed=total_passed,
            commands_failed=total_failed,
            commands_skipped=total_skipped,
            failures=all_failures,
            process_results=process_results,
            duration_seconds=duration,
            summary=" | ".join(summary_parts),
            warnings=warnings,
        )

    # ── Result Parsing ───────────────────────────────────────────

    def _parse_result(
        self, result: ProcessExecutionResult
    ) -> Tuple[ExecutionStatus, Optional[int], Optional[int], Optional[int], Optional[int], List[TestFailure]]:
        """Parse a process result using the best matching parser."""
        for parser in self._parsers:
            if parser.can_parse(result):
                return parser.parse(result)

        # Fallback (should not reach here since GenericResultParser always matches)
        return result.status, None, None, None, None, []

    # ── Workspace Tracking ──────────────────────────────────────
    # Supports dual mode: in-memory (fallback) and persistent (via run_store)

    def _has_persistent_workspace_store(self) -> bool:
        """Check if the run_store supports workspace persistence."""
        return (
            self._run_store is not None
            and hasattr(self._run_store, "save_workspace")
        )

    def register_workspace(self, workspace_id: str, root_path: str) -> None:
        """Register a workspace for tracking.

        When a persistent RunStore is available, the workspace is also
        persisted to the database for cross-session continuity.
        """
        self._active_workspaces[workspace_id] = root_path
        if self._has_persistent_workspace_store():
            import asyncio
            try:
                asyncio.ensure_future(
                    self._run_store.save_workspace(
                        workspace_id=workspace_id,
                        root_path=root_path,
                        workspace_type="testing",
                        writable=False,
                    )
                )
            except Exception:
                pass  # Non-critical; in-memory fallback still works

    def get_workspace_root(self, workspace_id: str) -> Optional[str]:
        """Get the root path for a registered workspace.

        Checks the in-memory dict first (fast path), then falls back
        to the persistent store for workspaces from a previous session.
        """
        # Fast path: check in-memory dict
        root = self._active_workspaces.get(workspace_id)
        if root is not None:
            return root
        # Fallback: check persistent store (for cross-session recovery)
        if self._has_persistent_workspace_store():
            import asyncio
            try:
                loop = asyncio.get_running_loop()
                future = asyncio.run_coroutine_threadsafe(
                    self._run_store.get_workspace(workspace_id),
                    loop,
                )
                result = future.result(timeout=5)
                if result:
                    # Cache in memory for fast access
                    self._active_workspaces[workspace_id] = result["root_path"]
                    return result["root_path"]
            except Exception:
                pass
        return None

    def unregister_workspace(self, workspace_id: str) -> None:
        """Remove a workspace from tracking when it's no longer needed.

        Also removes from persistent store if available.
        """
        self._active_workspaces.pop(workspace_id, None)
        if self._has_persistent_workspace_store():
            import asyncio
            try:
                asyncio.ensure_future(
                    self._run_store.delete_workspace(workspace_id)
                )
            except Exception:
                pass

    @property
    def workspace_count(self) -> int:
        """Return the number of currently tracked workspaces."""
        return len(self._active_workspaces)

    @staticmethod
    def _safe_read(path: Path) -> Optional[str]:
        """Safely read a file, returning None on failure."""
        try:
            return path.read_text("utf-8", errors="replace")
        except (OSError, PermissionError):
            return None

    @staticmethod
    def _detect_python(ws: Path) -> bool:
        """Check if the workspace contains Python files."""
        for ext in [".py", ".pyx", ".pyi"]:
            if list(ws.rglob(f"*{ext}"))[:1]:
                return True
        return False

    @staticmethod
    def _check_python_test_deps(ws: Path) -> bool:
        """Simple check if pytest might be available (not exhaustive)."""
        # In a real sandbox, dependencies would be pre-installed.
        # This just checks if pytest config exists.
        if (ws / "pytest.ini").exists():
            return True
        content = TestingService._safe_read(ws / "pyproject.toml")
        if content and "[tool.pytest" in content:
            return True
        return False  # We'll try anyway and let the result tell us

    @staticmethod
    def load_package_scripts(ws: Path) -> Dict[str, str]:
        """Load package.json scripts from a workspace path."""
        pkg_path = ws / "package.json"
        content = TestingService._safe_read(pkg_path)
        if content:
            try:
                data = json.loads(content)
                return data.get("scripts", {})
            except json.JSONDecodeError:
                pass
        return {}

    # ── Phase 6 Integration ──────────────────────────────────────

    def discover_from_patch(
        self, workspace_root: str, patch_result: PatchApplicationResult
    ) -> List[CommandCandidate]:
        """Discover commands considering Phase 6 patch information.

        Uses the same discovery but may prioritize tests related to changed files.
        """
        candidates = self.discover_commands(workspace_root)

        changed_files = (
            patch_result.files_created
            + patch_result.files_modified
            + patch_result.files_deleted
        )

        # If pytest commands found, add relevant test targeting
        for candidate in candidates:
            if candidate.executable == "python" and "-m" in candidate.arguments:
                # Phase 15 (12d): prefer impact-driven selection when a graph
                # selector is available; fall back to filename heuristics.
                test_files = self.select_tests_for_changes(workspace_root, changed_files)
                if test_files:
                    candidate.arguments.extend(test_files)
                    candidate.reason += f" | Targeting tests related to {len(changed_files)} changed files"

        return candidates

    def select_tests_for_changes(
        self,
        workspace_root: str,
        changed_files: List[str],
    ) -> List[str]:
        """Select test files covering changed code.

        Uses the impact-driven TestSelectionService when a selector is
        configured; otherwise falls back to filename heuristics.
        Returns repository-relative paths suitable for pytest targeting.
        """
        if self._test_selector is not None:
            try:
                result = self._test_selector.select_for_changed_files(
                    changed_files=changed_files,
                )
                selected = result.file_paths
                if selected:
                    return selected[:5]
            except Exception:
                pass  # Fall back to heuristic targeting

        return self.find_related_tests(workspace_root, changed_files)

    def find_related_tests(
        self, workspace_root: str, changed_files: List[str]
    ) -> List[str]:
        """Find test files related to changed source files.

        Uses simple heuristics: tests/test_X.py for src/X.py etc.
        """
        test_files: List[str] = []
        ws = Path(workspace_root)

        for changed in changed_files:
            changed_path = Path(changed)
            stem = changed_path.stem

            # Look for test_<name>.py or <name>_test.py
            patterns = [
                f"**/test_{stem}.py",
                f"**/{stem}_test.py",
                f"**/tests/test_{stem}.py",
                f"**/tests/{stem}_test.py",
            ]

            for pattern in patterns:
                matches = list(ws.glob(pattern))
                for match in matches:
                    try:
                        rel = match.relative_to(ws)
                        test_files.append(str(rel))
                    except ValueError:
                        pass

        return test_files[:5]  # Limit to 5 test files

    async def get_capabilities(self) -> TestingCapabilities:
        """Return current Phase 7 capabilities."""
        return TestingCapabilities(
            supported_categories=["test", "lint", "typecheck", "build"],
            supported_frameworks=["pytest", "unittest", "vitest", "jest", "generic"],
            max_commands_per_run=settings.TEST_MAX_COMMANDS,
            default_timeout_seconds=settings.TEST_DEFAULT_TIMEOUT,
            max_output_bytes=settings.TEST_MAX_OUTPUT_BYTES,
            environment_sanitization=True,
            workspace_isolation=True,
            llm_required=False,
        )
