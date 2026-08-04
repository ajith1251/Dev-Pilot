"""
Execution Policy — Phase 7

Deterministic security gate for execution steps. Answers:
    "Is this execution step permitted?"

Policy considers:
    - Executable allowlist
    - Command category enablement
    - Argument safety
    - Working directory safety
    - Package script content inspection
    - Dangerous patterns

This is the trust boundary between the Test Agent's recommendations
and actual process execution.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from app.config import settings
from app.core.exceptions import ExecutionPolicyError, ExecutionRejectedError
from app.models.testing import (
    CommandCategory,
    CommandSource,
    ExecutionStep,
)


# ── Executable Allowlist ────────────────────────────────────────

# Conservative default allowlist: only explicit tools needed for testing
DEFAULT_ALLOWED_EXECUTABLES: Set[str] = {
    "python",
    "python3",
    "pytest",
    "node",
    "npm",
    "npx",
    "pnpm",
    "yarn",
    "make",
}

# Executables that are ALWAYS rejected regardless of context
BLOCKED_EXECUTABLES: Set[str] = {
    "powershell",
    "pwsh",
    "cmd",
    "bash",
    "sh",
    "zsh",
    "fish",
    "curl",
    "wget",
    "ssh",
    "scp",
    "sftp",
    "telnet",
    "nc",
    "netcat",
    "nmap",
    "chmod",
    "chown",
    "sudo",
    "su",
    "docker",
    "podman",
    "kubectl",
}

# ── Dangerous Argument Patterns ─────────────────────────────────

DANGEROUS_ARG_PATTERNS: List[str] = [
    r"rm\s+-rf",
    r"--rm",
    r">\s*/dev/",
    r"format\s+",
    r"mkfs\.",
    r"dd\s+if=",
    r":\(\)\s*\{",
    r"\$\{.*\}",
    r"`.*`",
    r"\|\s*sh",
    r"\|\s*bash",
]

# Dangerous npm script patterns
DANGEROUS_SCRIPT_PATTERNS: List[str] = [
    r"rm\s+-rf",
    r"rm\s+-r",
    r"format\s+(disk|drive|C:|D:)",
    r"shutdown",
    r"reboot",
    r"powershell",
    r"cmd\s+/",
    r"curl\s+",
    r"wget\s+",
    r"ssh\s+",
    r"chmod\s+",
]


class ExecutionPolicy:
    """Deterministic security policy for execution steps.

    A rejected command must never reach process execution.
    """

    def __init__(
        self,
        allowed_executables: Optional[Set[str]] = None,
        allow_build: bool = settings.TEST_ALLOW_BUILD,
        allow_lint: bool = settings.TEST_ALLOW_LINT,
        allow_typecheck: bool = settings.TEST_ALLOW_TYPECHECK,
        max_commands: int = settings.TEST_MAX_COMMANDS,
    ):
        self._allowed_executables = allowed_executables or DEFAULT_ALLOWED_EXECUTABLES
        self._allow_build = allow_build
        self._allow_lint = allow_lint
        self._allow_typecheck = allow_typecheck
        self._max_commands = max_commands

    def validate(
        self,
        step: ExecutionStep,
        workspace_root: str,
        package_scripts: Optional[Dict[str, str]] = None,
        plan_step_index: int = 0,
    ) -> Tuple[bool, str]:
        """Validate whether an execution step is permitted.

        Args:
            step: The execution step to validate.
            workspace_root: Absolute path to workspace root.
            package_scripts: Dict of {script_name: script_content} from package.json.
            plan_step_index: Index of this step in the plan.

        Returns:
            Tuple of (is_allowed, reason).
        """
        # 1. Category-based filtering
        cat_ok, cat_reason = self._check_category(step)
        if not cat_ok:
            return False, cat_reason

        # 2. Executable allowlist
        exe_ok, exe_reason = self._check_executable(step)
        if not exe_ok:
            return False, exe_reason

        # 3. Working directory safety
        wd_ok, wd_reason = self._check_working_directory(step, workspace_root)
        if not wd_ok:
            return False, wd_reason

        # 4. Argument safety
        arg_ok, arg_reason = self._check_arguments(step)
        if not arg_ok:
            return False, arg_reason

        # 5. Package script safety (when source is package.json)
        if (
            step.source == CommandSource.PACKAGE_JSON
            and package_scripts
        ):
            script_ok, script_reason = self._check_package_script(
                step, package_scripts
            )
            if not script_ok:
                return False, script_reason

        # 6. Command count limit
        if plan_step_index >= self._max_commands:
            return (
                False,
                f"Command index {plan_step_index} exceeds max commands ({self._max_commands})",
            )

        return True, "ALLOWED"

    def _check_category(self, step: ExecutionStep) -> Tuple[bool, str]:
        """Check if the command's category is enabled."""
        if step.category == CommandCategory.BUILD and not self._allow_build:
            return False, "BUILD commands are disabled by policy"
        if step.category == CommandCategory.LINT and not self._allow_lint:
            return False, "LINT commands are disabled by policy"
        if step.category == CommandCategory.TYPECHECK and not self._allow_typecheck:
            return False, "TYPECHECK commands are disabled by policy"
        if step.category == CommandCategory.TEST:
            pass  # Always allowed
        return True, ""

    def _check_executable(self, step: ExecutionStep) -> Tuple[bool, str]:
        """Check if the executable is allowed."""
        exe = step.executable.lower()

        # Check blocked list first
        if exe in BLOCKED_EXECUTABLES:
            return False, f"Executable '{step.executable}' is blocked by security policy"

        # Check allowlist
        if exe not in self._allowed_executables:
            return (
                False,
                f"Executable '{step.executable}' is not in the allowed list. "
                f"Allowed: {sorted(self._allowed_executables)}",
            )

        return True, ""

    def _check_working_directory(
        self, step: ExecutionStep, workspace_root: str
    ) -> Tuple[bool, str]:
        """Verify the working directory is inside the workspace root."""
        try:
            wd = Path(step.working_directory)
            if wd.is_absolute():
                # Reject absolute paths for safety
                resolved_wd = wd.resolve()
            else:
                resolved_wd = (Path(workspace_root) / wd).resolve()

            resolved_root = Path(workspace_root).resolve()

            # Check the resolved working directory is within workspace root
            str_wd = str(resolved_wd)
            str_root = str(resolved_root)

            # Must start with the workspace root path
            if not str_wd.startswith(str_root):
                return (
                    False,
                    f"Working directory '{step.working_directory}' resolves to "
                    f"'{resolved_wd}' which is outside workspace root '{resolved_root}'",
                )

        except (ValueError, OSError) as exc:
            return (
                False,
                f"Working directory validation failed: {exc}",
            )

        return True, ""

    def _check_arguments(self, step: ExecutionStep) -> Tuple[bool, str]:
        """Check for dangerous argument patterns."""
        full_command = f"{step.executable} {' '.join(step.arguments)}".lower()

        # Reject empty executable
        if not step.executable.strip():
            return False, "Executable is empty"

        # Restrict 'python' to -m module invocations only (no arbitrary scripts)
        if step.executable.lower() in ("python", "python3"):
            if not step.arguments:
                return False, "python requires arguments"
            if step.arguments[0] != "-m":
                return (
                    False,
                    f"python must use '-m <module>' pattern. "
                    f"Arbitrary script execution is not permitted: "
                    f"{' '.join(step.arguments)}",
                )
            if len(step.arguments) < 2:
                return False, "'-m' requires a module name"

        for pattern in DANGEROUS_ARG_PATTERNS:
            if re.search(pattern, full_command):
                return (
                    False,
                    f"Command contains dangerous pattern: {pattern}",
                )

        # Check for shell metacharacters in arguments
        dangerous_chars = {";", "&", "|", "`", "$", "(", ")", "{", "}"}
        for arg in step.arguments:
            for char in dangerous_chars:
                if char in arg:
                    return (
                        False,
                        f"Argument '{arg}' contains shell metacharacter '{char}'",
                    )

        return True, ""

    def _check_package_script(
        self,
        step: ExecutionStep,
        package_scripts: Dict[str, str],
    ) -> Tuple[bool, str]:
        """Check if a package.json script is safe to execute.

        This inspects the actual script content, not just the name.
        """
        # Find which script name maps to this step
        script_name = self._find_script_name(step, package_scripts)
        if not script_name:
            # If we can't identify a specific script, allow (already validated by executable)
            return True, ""

        script_content = package_scripts.get(script_name, "")
        if not script_content:
            return True, ""

        # Check for dangerous patterns in script content
        script_lower = script_content.lower()
        for pattern in DANGEROUS_SCRIPT_PATTERNS:
            if re.search(pattern, script_lower):
                return (
                    False,
                    f"Package script '{script_name}' contains dangerous pattern: "
                    f"safe execution cannot be guaranteed",
                )

        # Flag scripts that invoke shell or network tools
        suspicious_triggers = ["&&", "||", ">", ">>", "<", "|"]
        for trigger in suspicious_triggers:
            if trigger in script_content:
                return (
                    False,
                    f"Package script '{script_name}' uses shell operator '{trigger}'",
                )

        return True, ""

    @staticmethod
    def _find_script_name(
        step: ExecutionStep, package_scripts: Dict[str, str]
    ) -> Optional[str]:
        """Try to find which package script name corresponds to this step."""
        # If arguments contain the script name
        if step.arguments:
            for arg in step.arguments:
                if arg in package_scripts:
                    return arg
                # Check if it's a "run script_name" pattern
                if arg.startswith("run ") or arg == "run":
                    idx = step.arguments.index(arg)
                    if idx + 1 < len(step.arguments):
                        candidate = step.arguments[idx + 1]
                        if candidate in package_scripts:
                            return candidate
        return None

    @staticmethod
    def is_safe_npm_command(arguments: List[str]) -> Tuple[bool, str]:
        """Special check for npm/pnpm/yarn commands.

        npm test is generally safer than npm run arbitrary-script.
        """
        if not arguments:
            return True, ""

        # npm test, npm run test — generally safe
        if "test" in arguments:
            return True, ""

        # npm run <something> — check if it's a standard script name
        known_safe_scripts = {
            "test", "build", "lint", "format", "typecheck",
            "type-check", "typescript", "dev", "start", "check",
        }

        for arg in arguments:
            if arg in known_safe_scripts:
                return True, ""
            # Detect npm run <script> pattern
            if arg == "run":
                idx = arguments.index(arg)
                if idx + 1 < len(arguments):
                    script = arguments[idx + 1]
                    if script in known_safe_scripts:
                        return True, ""
                    return (
                        False,
                        f"npm script '{script}' is not in the known safe list",
                    )

        return True, ""


def create_default_policy() -> ExecutionPolicy:
    """Create an ExecutionPolicy with conservative defaults."""
    return ExecutionPolicy(
        allowed_executables=DEFAULT_ALLOWED_EXECUTABLES,
        allow_build=settings.TEST_ALLOW_BUILD,
        allow_lint=settings.TEST_ALLOW_LINT,
        allow_typecheck=settings.TEST_ALLOW_TYPECHECK,
        max_commands=settings.TEST_MAX_COMMANDS,
    )
