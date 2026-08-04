"""
Remote Repository Analyzer — bridge between GitHub and the Phase 2 Repository Intelligence Engine.

Workflow:
1. Validate GitHub URL → owner/repo/ref
2. Fetch GitHub metadata
3. Resolve ref (use default branch if none specified)
4. Acquire repository snapshot
5. Run Phase 2 RepositoryAnalyzer on local snapshot
6. Combine metadata + profile → RemoteRepositoryProfile
7. Clean up workspace
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional

from app.core.logging import logger
from app.models.github import (
    AcquisitionMetadata,
    GitHubRepoMetadata,
    RemoteRepositoryProfile,
)
from app.services.acquisition import RepositoryAcquisitionService
from app.services.github import GitHubService
from app.services.repository_analyzer import RepositoryAnalyzer


class RemoteRepositoryAnalyzer:
    """Analyze a GitHub repository by acquiring it locally and running Phase 2 analysis.

    Usage:
        analyzer = RemoteRepositoryAnalyzer()
        result = await analyzer.analyze("https://github.com/owner/repo", ref="main")
        # result is a RemoteRepositoryProfile with metadata + analysis + cleanup
        # Note: cleanup happens automatically after analysis
    """

    def __init__(
        self,
        github: Optional[GitHubService] = None,
        acquisition: Optional[RepositoryAcquisitionService] = None,
        analyzer: Optional[RepositoryAnalyzer] = None,
    ) -> None:
        self._github = github or GitHubService()
        self._acquisition = acquisition or RepositoryAcquisitionService()
        self._analyzer = analyzer or RepositoryAnalyzer()
        self._active_acquisitions: list[AcquisitionMetadata] = []

    async def analyze(
        self,
        url: str,
        ref: Optional[str] = None,
        shallow: bool = True,
    ) -> RemoteRepositoryProfile:
        """Analyze a GitHub repository end-to-end.

        Args:
            url: GitHub repository URL (e.g. https://github.com/owner/repo).
            ref: Branch/tag to analyze (default: repository's default branch).
            shallow: Whether to use shallow clone.

        Returns:
            RemoteRepositoryProfile with GitHub metadata + local analysis.

        Raises:
            ValueError: If URL is invalid.
            AcquisitionError: If clone/checkout fails.
        """
        warnings: list[str] = []
        errors: list[str] = []

        # ── Step 1: Validate URL ─────────────────────────────────
        try:
            parsed = self._github.parse_any_url(url)
        except ValueError as exc:
            return RemoteRepositoryProfile(
                github=GitHubRepoMetadata(owner="", name="", full_name="", default_branch=""),
                ref=ref or "",
                acquisition=AcquisitionMetadata(
                    source_url=url, ref=ref or "", local_path="",
                    acquired_at=datetime.now(timezone.utc).isoformat(),
                ),
                errors=[str(exc)],
            )

        owner: str = parsed["owner"]
        repo_name: str = parsed["repo"]
        url_ref: Optional[str] = parsed.get("ref")
        final_ref = ref or url_ref

        logger.info(
            "Remote analysis started: %s/%s (ref=%s)", owner, repo_name, final_ref or "default"
        )

        # ── Step 2: Fetch metadata ───────────────────────────────
        try:
            metadata = await self._github.get_repo_metadata(owner, repo_name)
        except Exception as exc:
            return RemoteRepositoryProfile(
                github=GitHubRepoMetadata(
                    owner=owner, name=repo_name, full_name=f"{owner}/{repo_name}",
                ),
                ref=final_ref or "main",
                acquisition=AcquisitionMetadata(
                    source_url=f"https://github.com/{owner}/{repo_name}",
                    ref=final_ref or "main", local_path="",
                    acquired_at=datetime.now(timezone.utc).isoformat(),
                ),
                errors=[f"Failed to fetch repository metadata: {exc}"],
            )

        if metadata.archived:
            warnings.append("Repository is archived — analysis may be limited")

        # ── Step 3: Resolve ref ──────────────────────────────────
        resolved_ref = final_ref or metadata.default_branch

        # ── Step 4: Acquire repository ───────────────────────────
        try:
            acquisition_meta = await self._acquisition.acquire(
                owner=owner,
                repo=repo_name,
                ref=resolved_ref,
                shallow=shallow,
            )
            self._active_acquisitions.append(acquisition_meta)
        except Exception as exc:
            return RemoteRepositoryProfile(
                github=metadata,
                ref=resolved_ref,
                acquisition=AcquisitionMetadata(
                    source_url=f"https://github.com/{owner}/{repo_name}",
                    ref=resolved_ref, local_path="",
                    acquired_at=datetime.now(timezone.utc).isoformat(),
                ),
                errors=[f"Failed to acquire repository: {exc}"],
            )

        # ── Step 5: Analyze locally ──────────────────────────────
        try:
            profile = self._analyzer.analyze(acquisition_meta.local_path)
        except Exception as exc:
            errors.append(f"Local analysis failed: {exc}")
            profile = None
            logger.error("Local analysis failed for %s/%s: %s", owner, repo_name, exc)

        # ── Step 6: Collect warnings ─────────────────────────────
        if profile:
            if profile.warnings:
                warnings.extend(profile.warnings)
            if profile.scan.errors:
                errors.extend(profile.scan.errors)

        # Check rate limit
        rate_info = self._github.get_rate_limit_info()
        if rate_info and rate_info.remaining < 10:
            warnings.append(
                f"GitHub API rate limit low: {rate_info.remaining}/{rate_info.limit} remaining"
            )

        # ── Step 7: Build result ─────────────────────────────────
        result = RemoteRepositoryProfile(
            github=metadata,
            ref=resolved_ref,
            profile=profile.model_dump() if profile else None,
            acquisition=acquisition_meta,
            warnings=warnings,
            errors=errors,
        )

        # ── Step 8: Clean up ─────────────────────────────────────
        try:
            self._acquisition.cleanup(acquisition_meta.local_path)
            self._active_acquisitions = [
                a for a in self._active_acquisitions
                if a.local_path != acquisition_meta.local_path
            ]
        except Exception as exc:
            logger.warning("Cleanup warning for %s: %s", acquisition_meta.local_path, exc)

        logger.info(
            "Remote analysis complete: %s/%s (ref=%s, files=%d, langs=%d)",
            owner, repo_name, resolved_ref,
            profile.scan.total_files_scanned if profile else 0,
            len(profile.languages) if profile else 0,
        )

        return result

    def cleanup_all(self) -> None:
        """Clean up all active acquisitions."""
        self._acquisition.cleanup_all()
        self._active_acquisitions = []
