"""
Repository Acquisition Service — safely clone GitHub repositories to temporary workspaces.

Uses Git CLI with subprocess (not shell=True) for safety.
Always cleans up temporary directories. Never executes repository code.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Optional, Set

import re

from app.core.logging import logger
from app.models.github import AcquisitionMetadata


# Directories that should never be used as workspace
FORBIDDEN_WORKSPACE_PARENTS: Set[str] = {
    "/etc", "/bin", "/sbin", "/usr", "/boot", "/dev", "/proc", "/sys",
}

# Default workspace base directory
DEFAULT_WORKSPACE = os.path.join(tempfile.gettempdir(), "devpilot-acquisitions")

# Timeouts
CLONE_TIMEOUT_SECONDS = 120
GIT_OPERATION_TIMEOUT = 30


class AcquisitionError(Exception):
    """Raised when repository acquisition fails."""


class RepositoryAcquisitionService:
    """Safely acquire a local working copy of a GitHub repository.

    Lifecycle:
        1. acquire(owner, repo, ref) → local path
        2. analyze (external — uses existing RepositoryAnalyzer)
        3. cleanup(path) → removes temporary files

    Never executes repository code. Never modifies DevPilot source.
    For private repos, pass the GitHub token — it is injected into the
    clone URL securely and never logged.
    """

    def __init__(
        self,
        workspace_base: Optional[str] = None,
        clone_timeout: int = CLONE_TIMEOUT_SECONDS,
        skip_hooks: bool = True,
        token: Optional[str] = None,
    ) -> None:
        self._workspace_base = workspace_base or DEFAULT_WORKSPACE
        self._clone_timeout = clone_timeout
        self._skip_hooks = skip_hooks
        self._active_paths: Set[str] = set()
        self._token = token

    def _validate_repo_url(self, owner: str, repo: str) -> str:
        """Validate and construct a safe clone URL.

        Uses the HTTPS URL format to avoid shell injection.
        If a GitHub token is configured, it is injected as x-access-token
        for private repository access. The token is never logged.
        The URL is constructed from validated parameters — not user input directly.
        """
        # Validate owner and repo contain only safe characters
        if not re.match(r"^[a-zA-Z0-9_.-]+$", owner):
            raise AcquisitionError(f"Invalid repository owner: {owner}")
        if not re.match(r"^[a-zA-Z0-9_.-]+$", repo):
            raise AcquisitionError(f"Invalid repository name: {repo}")

        if self._token:
            # Authenticated clone: https://x-access-token:{token}@github.com/owner/repo.git
            return f"https://x-access-token:{self._token}@github.com/{owner}/{repo}.git"
        return f"https://github.com/{owner}/{repo}.git"

    def _create_workspace(self, owner: str, repo: str) -> str:
        """Create a unique temporary workspace directory.

        Returns:
            Absolute path to the workspace.
        """
        # Ensure workspace base exists
        base = Path(self._workspace_base)
        try:
            base.mkdir(parents=True, exist_ok=True)
        except PermissionError as exc:
            raise AcquisitionError(
                f"Cannot create workspace directory: {exc}"
            ) from exc

        # Verify workspace is in a safe location
        resolved_base = base.resolve()
        for forbidden in FORBIDDEN_WORKSPACE_PARENTS:
            if str(resolved_base).startswith(forbidden + os.sep) or str(resolved_base) == forbidden:
                raise AcquisitionError(
                    f"Workspace base is in a forbidden location: {resolved_base}"
                )

        # Create unique directory
        unique_id = uuid.uuid4().hex[:12]
        workspace = base / f"{owner}-{repo}-{unique_id}"

        try:
            workspace.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            # Extremely unlikely, but handle gracefully
            unique_id = uuid.uuid4().hex[:12]
            workspace = base / f"{owner}-{repo}-{unique_id}"
            workspace.mkdir(parents=True, exist_ok=False)

        self._active_paths.add(str(workspace))
        return str(workspace)

    async def acquire(
        self,
        owner: str,
        repo: str,
        ref: Optional[str] = None,
        shallow: bool = True,
        depth: int = 1,
    ) -> AcquisitionMetadata:
        """Acquire a repository snapshot.

        Args:
            owner: Repository owner.
            repo: Repository name.
            ref: Branch/tag/commit to checkout (default: resolves to default branch).
            shallow: Whether to use shallow clone.
            depth: Shallow clone depth (ignored if shallow=False).

        Returns:
            AcquisitionMetadata with local path and details.

        Raises:
            AcquisitionError: If clone/checkout fails.
        """
        start_time = time.time()
        clone_url = self._validate_repo_url(owner, repo)
        workspace = self._create_workspace(owner, repo)
        resolved_ref = ref or "HEAD"

        logger.info(
            "Acquiring %s/%s (ref=%s, shallow=%s) → %s",
            owner, repo, resolved_ref, shallow, workspace,
        )

        try:
            # Step 1: Initialize a minimal git repo
            await self._run_git(["init"], workspace, "Git init failed")

            # Step 2: Add remote origin (never embed credentials)
            await self._run_git(
                ["remote", "add", "origin", clone_url],
                workspace,
                "Failed to add remote",
            )

            # Step 3: Fetch (shallow or full)
            fetch_args = ["fetch", "origin"]
            if shallow:
                fetch_args.extend(["--depth", str(depth)])
            # Don't fetch tags to minimize data
            fetch_args.append("--no-tags")
            fetch_args.append(resolved_ref)

            await self._run_git(
                fetch_args,
                workspace,
                f"Failed to fetch ref '{resolved_ref}'",
                timeout=self._clone_timeout,
            )

            # Step 4: Checkout
            await self._run_git(
                ["checkout", "-f", "FETCH_HEAD"],
                workspace,
                "Failed to checkout",
            )

            # Step 5: Disable hooks for safety
            if self._skip_hooks:
                hooks_dir = Path(workspace) / ".git" / "hooks"
                if hooks_dir.exists():
                    for hook_file in hooks_dir.iterdir():
                        if hook_file.is_file() and not hook_file.name.endswith(".sample"):
                            try:
                                os.chmod(str(hook_file), 0o644)  # Remove execute
                            except OSError:
                                pass

            duration = round(time.time() - start_time, 3)
            logger.info(
                "Acquisition complete: %s/%s → %s (%.1fs)",
                owner, repo, workspace, duration,
            )

            return AcquisitionMetadata(
                source_url=f"https://github.com/{owner}/{repo}",
                ref=resolved_ref,
                local_path=workspace,
                acquired_at=__import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc
                ).isoformat(),
                duration_seconds=duration,
                is_shallow=shallow,
            )

        except AcquisitionError:
            # Clean up on failure
            self.cleanup(workspace)
            raise
        except Exception as exc:
            self.cleanup(workspace)
            raise AcquisitionError(f"Acquisition failed: {exc}") from exc

    async def _run_git(
        self,
        args: list[str],
        cwd: str,
        error_message: str,
        timeout: int = GIT_OPERATION_TIMEOUT,
    ) -> str:
        """Run a git command safely using subprocess with argument array.

        Args:
            args: Git arguments (not including 'git' prefix).
            cwd: Working directory.
            error_message: Error message prefix on failure.
            timeout: Command timeout in seconds.

        Returns:
            stdout of the command.

        Raises:
            AcquisitionError: If the command fails.
        """
        cmd = ["git"] + args

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                raise AcquisitionError(
                    f"Git command timed out after {timeout}s: {' '.join(cmd[:3])}..."
                )

            if proc.returncode != 0:
                stderr_text = stderr.decode("utf-8", errors="replace")[:500]
                # Redact sensitive info from stderr
                stderr_text = stderr_text.replace(self._workspace_base, "***")
                if self._token:
                    stderr_text = stderr_text.replace(self._token, "***")
                raise AcquisitionError(
                    f"{error_message}: {stderr_text}"
                )

            return stdout.decode("utf-8", errors="replace")

        except AcquisitionError:
            raise
        except FileNotFoundError as exc:
            raise AcquisitionError(
                "Git is not installed or not found in PATH"
            ) from exc
        except OSError as exc:
            raise AcquisitionError(f"Git operation failed: {exc}") from exc

    def cleanup(self, path: str) -> None:
        """Remove an acquired repository workspace.

        Safe to call multiple times. Never raises (logs errors instead).
        """
        if path not in self._active_paths:
            logger.warning("Cleanup called for unknown path: %s", path)
            return

        try:
            if os.path.exists(path):
                shutil.rmtree(path, ignore_errors=True)
                logger.info("Cleaned up workspace: %s", path)
        except Exception as exc:
            logger.error("Failed to clean up workspace %s: %s", path, exc)
        finally:
            self._active_paths.discard(path)

    def cleanup_all(self) -> None:
        """Clean up all active acquisition workspaces."""
        for path in list(self._active_paths):
            self.cleanup(path)

    @property
    def active_paths(self) -> Set[str]:
        """Return the set of active (not yet cleaned) workspace paths."""
        return set(self._active_paths)

