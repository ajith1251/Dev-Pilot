"""
Controlled Execution Engine — Phase 7

Safe, bounded subprocess execution with:
    - Argument-array execution (no shell=True)
    - Per-command timeouts with process tree termination
    - Bounded stdout/stderr capture
    - Sanitized environment (no DevPilot secrets leaked)
    - Workspace path validation
    - Structured result collection

This engine does NOT decide what to execute — it receives validated
ExecutionStep objects and returns ProcessExecutionResult.
"""

from __future__ import annotations

import asyncio
import os
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set

from app.config import settings
from app.models.testing import (
    CommandCategory,
    ExecutionStatus,
    ExecutionStep,
    ProcessExecutionResult,
)

# Environment variables that are safe to pass to child processes
SAFE_ENV_VARS: Set[str] = {
    # OS essentials
    "PATH",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "HOME",
    "HOMEDRIVE",
    "HOMEPATH",
    "COMSPEC",
    "PATHEXT",
    # Python
    "PYTHONIOENCODING",
    "PYTHONUNBUFFERED",
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONUTF8",
    # Node.js
    "NODE_PATH",
    # Generic
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
}

# Environment variables that must NEVER leak to child processes
BLOCKED_ENV_VARS: Set[str] = {
    # LLM Provider keys
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    # GitHub
    "GITHUB_TOKEN",
    # Database (future)
    "DATABASE_URL",
    # DevPilot internals
    "DEVPILOT_LLM_API_KEY",
    # Any other known secrets
}


class ControlledExecutionEngine:
    """Safe bounded subprocess execution engine.

    Responsibilities:
    1. Validate workspace path
    2. Build sanitized environment
    3. Start subprocess with argument array
    4. Capture stdout/stderr with limits
    5. Enforce timeout with process tree cleanup
    6. Return structured ProcessExecutionResult
    """

    def __init__(
        self,
        default_timeout: int = settings.TEST_DEFAULT_TIMEOUT,
        max_output_bytes: int = settings.TEST_MAX_OUTPUT_BYTES,
    ):
        self._default_timeout = default_timeout
        self._max_output_bytes = max_output_bytes

    async def execute(
        self,
        step: ExecutionStep,
        workspace_root: str,
        extra_env: Optional[Dict[str, str]] = None,
    ) -> ProcessExecutionResult:
        """Execute a single validated execution step.

        Args:
            step: The validated execution step to run.
            workspace_root: Absolute path to workspace root (validated by policy).
            extra_env: Optional extra environment variables for the process.

        Returns:
            ProcessExecutionResult with captured output and status.
        """
        started_at = datetime.now(timezone.utc).isoformat()
        start_time = time.time()

        # Resolve working directory
        wd_path = self._resolve_working_directory(step.working_directory, workspace_root)

        # Build sanitized environment
        env = self._build_sanitized_env(extra_env)

        # Build command for display
        cmd_display = f"{step.executable} {' '.join(step.arguments)}"

        timeout = step.timeout_seconds or self._default_timeout

        # Execute
        try:
            result = await self._run_process(
                executable=step.executable,
                arguments=step.arguments,
                cwd=wd_path,
                env=env,
                timeout=timeout,
                step_id=step.step_id,
                category=step.category,
                cmd_display=cmd_display,
            )
        except Exception as exc:
            # Infrastructure error, not a test failure
            finished_at = datetime.now(timezone.utc).isoformat()
            return ProcessExecutionResult(
                step_id=step.step_id,
                command=cmd_display,
                category=step.category,
                status=ExecutionStatus.ERROR,
                stdout="",
                stderr=str(exc),
                started_at=started_at,
                finished_at=finished_at,
                duration_seconds=time.time() - start_time,
                timed_out=False,
            )

        # Update timestamps
        finished_at = datetime.now(timezone.utc).isoformat()
        result.started_at = started_at
        result.finished_at = finished_at
        result.duration_seconds = time.time() - start_time

        return result

    async def _run_process(
        self,
        executable: str,
        arguments: List[str],
        cwd: str,
        env: Dict[str, str],
        timeout: int,
        step_id: str,
        category: CommandCategory,
        cmd_display: str,
    ) -> ProcessExecutionResult:
        """Run a single subprocess with bounded capture and timeout."""
        # Build the full command as an argument list
        cmd = [executable] + arguments

        # Create subprocess
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=cwd,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            return ProcessExecutionResult(
                step_id=step_id,
                command=cmd_display,
                category=category,
                status=ExecutionStatus.ERROR,
                exit_code=None,
                stdout="",
                stderr=f"Executable not found: {executable}",
                timed_out=False,
            )
        except PermissionError:
            return ProcessExecutionResult(
                step_id=step_id,
                command=cmd_display,
                category=category,
                status=ExecutionStatus.ERROR,
                exit_code=None,
                stdout="",
                stderr=f"Permission denied: {executable}",
                timed_out=False,
            )

        # Read stdout/stderr with bounded capture
        stdout_bytes = bytearray()
        stderr_bytes = bytearray()
        stdout_truncated = False
        stderr_truncated = False

        async def read_stream(stream, buffer, max_bytes):
            """Read from a stream into a buffer up to max_bytes."""
            nonlocal stdout_truncated, stderr_truncated
            truncated = False
            try:
                while True:
                    chunk = await asyncio.wait_for(
                        stream.read(65536), timeout=max(timeout, 30)
                    )
                    if not chunk:
                        break
                    remaining = max_bytes - len(buffer)
                    if remaining <= 0:
                        truncated = True
                        break
                    buffer.extend(chunk[:remaining])
            except (asyncio.TimeoutError, asyncio.IncompleteReadError):
                truncated = True
            return truncated

        # Start reading both streams concurrently
        try:
            stdout_task = asyncio.create_task(
                read_stream(process.stdout, stdout_bytes, self._max_output_bytes)
            )
            stderr_task = asyncio.create_task(
                read_stream(process.stderr, stderr_bytes, self._max_output_bytes)
            )

            # Wait for process completion with timeout
            try:
                exit_code = await asyncio.wait_for(
                    process.wait(), timeout=timeout
                )
                timed_out = False
            except asyncio.TimeoutError:
                timed_out = True
                # Terminate the process tree
                await self._terminate_process_tree(process)
                exit_code = -1

            # Wait for stream readers to finish
            stdout_truncated = await stdout_task or stdout_truncated
            stderr_truncated = await stderr_task or stderr_truncated

        except Exception:
            # Ensure cleanup
            if process.returncode is None:
                await self._terminate_process_tree(process)
            exit_code = -1
            timed_out = True

        # Determine status
        if timed_out:
            status = ExecutionStatus.TIMEOUT
        elif exit_code == 0:
            status = ExecutionStatus.PASSED
        else:
            status = ExecutionStatus.FAILED

        return ProcessExecutionResult(
            step_id=step_id,
            command=cmd_display,
            category=category,
            status=status,
            exit_code=exit_code,
            stdout=stdout_bytes.decode("utf-8", errors="replace")[: self._max_output_bytes],
            stderr=stderr_bytes.decode("utf-8", errors="replace")[: self._max_output_bytes],
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
            timed_out=timed_out,
        )

    async def _terminate_process_tree(self, process) -> None:
        """Best-effort process tree termination compatible with Windows."""
        try:
            pid = process.pid
            if pid is None:
                return

            # First try graceful SIGTERM
            try:
                if hasattr(signal, "SIGTERM"):
                    os.kill(pid, signal.SIGTERM)
                    await asyncio.sleep(0.5)
            except (OSError, AttributeError):
                pass

            # If still running, force kill
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass  # Already exited
                except OSError:
                    pass

            # Wait briefly for cleanup
            try:
                await asyncio.wait_for(process.wait(), timeout=3.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                pass

        except Exception:
            pass  # Best-effort cleanup

    def _resolve_working_directory(self, working_directory: str, workspace_root: str) -> str:
        """Resolve working directory relative to workspace root."""
        wd = Path(working_directory)
        if wd.is_absolute():
            return str(wd.resolve())
        return str((Path(workspace_root) / wd).resolve())

    def _build_sanitized_env(
        self, extra_env: Optional[Dict[str, str]] = None
    ) -> Dict[str, str]:
        """Build a sanitized environment for child processes.

        Only passes safe environment variables. Never passes DevPilot secrets.
        """
        env: Dict[str, str] = {}

        # Copy safe variables from current environment
        for var in SAFE_ENV_VARS:
            value = os.environ.get(var)
            if value is not None:
                env[var] = value

        # Add canary indicator (for tests)
        env["DEVPILOT_CHILD_PROCESS"] = "1"

        # Add any extra safe variables
        if extra_env:
            for key, value in extra_env.items():
                if key not in BLOCKED_ENV_VARS:
                    env[key] = value

        return env
