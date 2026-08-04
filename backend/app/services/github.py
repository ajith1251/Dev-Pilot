"""
GitHub integration service.

Handles repository reads, issue fetching, branch listing, and
structured GitHub API interactions. All operations are READ-ONLY.

Enforces:
- Token protection (never logged, never returned)
- Rate-limit awareness
- Pagination with configurable limits
- Structured error mapping
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar

import httpx

from app.config import settings
from app.core.exceptions import GitHubAuthenticationError, GitHubError, GitHubRateLimitError
from app.core.logging import logger
from app.models.github import (
    GitHubBranch,
    GitHubIssue,
    GitHubLabel,
    GitHubRepoMetadata,
    GitHubUser,
    RateLimitInfo,
)

# Default pagination
DEFAULT_PER_PAGE = 30
MAX_PER_PAGE = 100
MAX_PAGINATION_PAGES = 10

T = TypeVar("T")


class GitHubService:
    """Service for interacting with the GitHub REST API (read-only)."""

    BASE_URL = "https://api.github.com"

    def __init__(
        self,
        token: Optional[str] = None,
        base_url: Optional[str] = None,
        request_timeout: float = 30.0,
        max_retries: int = 2,
    ) -> None:
        self._token = token or settings.GITHUB_TOKEN
        self._base_url = (base_url or self.BASE_URL).rstrip("/")
        self._timeout = request_timeout
        self._max_retries = max_retries
        self._headers: Dict[str, str] = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "DevPilot/0.1.0",
        }
        if self._token:
            self._headers["Authorization"] = f"Bearer {self._token}"

        self._last_rate_limit: Optional[RateLimitInfo] = None

    # ── URL Parsing ──────────────────────────────────────────────

    @staticmethod
    def parse_issue_url(url: str) -> Tuple[str, str, int]:
        """Parse a GitHub issue URL into (owner, repo, issue_number)."""
        match = re.match(
            r"https://github\.com/([^/]+)/([^/]+)/issues/(\d+)", url
        )
        if not match:
            raise ValueError(f"Invalid GitHub issue URL: {url}")
        return match.group(1), match.group(2), int(match.group(3))

    @staticmethod
    def parse_repo_url(url: str) -> Tuple[str, str]:
        """Parse a GitHub repo URL into (owner, repo)."""
        # Try exact match with optional .git suffix
        match = re.match(
            r"https://github\.com/([^/]+)/([^/]+?)(?:\.git)?(?:\/.*)?$", url
        )
        if not match:
            raise ValueError(f"Invalid GitHub repo URL: {url}")
        return match.group(1), match.group(2)

    @staticmethod
    def parse_any_url(url: str) -> Dict[str, Any]:
        """Parse any supported GitHub URL into a typed reference.

        Returns dict with keys: type, owner, repo, [number], [ref]
        """
        # Issue URL
        match = re.match(
            r"https://github\.com/([^/]+)/([^/]+)/issues/(\d+)", url
        )
        if match:
            return {
                "type": "issue",
                "owner": match.group(1),
                "repo": match.group(2),
                "number": int(match.group(3)),
            }

        # Tree/blob URL with ref
        match = re.match(
            r"https://github\.com/([^/]+)/([^/]+)/(?:tree|blob)/([^/]+)", url
        )
        if match:
            return {
                "type": "repo",
                "owner": match.group(1),
                "repo": match.group(2),
                "ref": match.group(3),
            }

        # Plain repo URL
        match = re.match(
            r"https://github\.com/([^/]+)/([^/]+?)(?:\.git)?(?:\/.*)?$", url
        )
        if match:
            return {
                "type": "repo",
                "owner": match.group(1),
                "repo": match.group(2),
            }

        raise ValueError(f"Unsupported GitHub URL format: {url}")

    # ── Core HTTP methods ────────────────────────────────────────

    def _redact_token(self, text: str) -> str:
        """Redact any Authorization header values from a string."""
        if self._token:
            return text.replace(self._token, "***")
        return text

    async def _get(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Make an authenticated GET request with retry support."""
        url = f"{self._base_url}{path}"

        for attempt in range(1 + self._max_retries):
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(
                        url,
                        headers=self._headers,
                        params=params,
                        timeout=self._timeout,
                    )

                    # Capture rate-limit info
                    self._update_rate_limit(resp)

                    if resp.status_code == 401:
                        raise GitHubAuthenticationError(
                            "GitHub token is invalid or missing"
                        )
                    if resp.status_code == 403:
                        # Check if rate-limited
                        if resp.headers.get("X-RateLimit-Remaining") == "0":
                            raise GitHubRateLimitError(
                                "GitHub API rate limit exceeded"
                            )
                        raise GitHubError(
                            f"Forbidden: {resp.status_code} — {self._redact_token(resp.text[:500])}"
                        )
                    if resp.status_code == 404:
                        raise GitHubError(
                            f"Not found: {path}"
                        )
                    if resp.status_code == 429:
                        raise GitHubRateLimitError(
                            "GitHub API rate limit exceeded (429)"
                        )
                    if resp.status_code >= 500:
                        if attempt < self._max_retries:
                            logger.warning(
                                "GitHub API 5xx (attempt %d/%d): %s",
                                attempt + 1, self._max_retries, path,
                            )
                            continue
                        raise GitHubError(
                            f"GitHub API server error ({resp.status_code})"
                        )

                    resp.raise_for_status()
                    return resp.json()

            except httpx.TimeoutException:
                if attempt < self._max_retries:
                    logger.warning(
                        "GitHub API timeout (attempt %d/%d): %s",
                        attempt + 1, self._max_retries, path,
                    )
                    continue
                raise GitHubError(f"GitHub API timeout: {path}")

            except (GitHubAuthenticationError, GitHubRateLimitError, GitHubError):
                raise

            except httpx.HTTPStatusError as exc:
                raise GitHubError(
                    f"GitHub API error {exc.response.status_code}: "
                    f"{self._redact_token(exc.response.text[:500])}"
                ) from exc

        raise GitHubError(f"Max retries exceeded: {path}")

    async def _get_paginated(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        max_pages: int = MAX_PAGINATION_PAGES,
        per_page: int = DEFAULT_PER_PAGE,
    ) -> List[Any]:
        """Fetch paginated results from the GitHub API.

        Args:
            path: API path (e.g. /repos/owner/repo/issues).
            params: Additional query parameters.
            max_pages: Maximum number of pages to fetch.
            per_page: Items per page (max 100).

        Returns:
            Combined list of all items from all fetched pages.
        """
        all_items: List[Any] = []
        page_params = dict(params or {})
        page_params.setdefault("per_page", min(per_page, MAX_PER_PAGE))

        for page in range(1, max_pages + 1):
            page_params["page"] = page
            try:
                data = await self._get(path, params=page_params)
            except GitHubError:
                break

            if not data:
                break

            all_items.extend(data)

            # If fewer items than per_page, we're on the last page
            if len(data) < per_page:
                break

        return all_items

    def _update_rate_limit(self, resp: httpx.Response) -> None:
        """Extract rate-limit info from response headers."""
        remaining = resp.headers.get("X-RateLimit-Remaining")
        limit = resp.headers.get("X-RateLimit-Limit")
        reset = resp.headers.get("X-RateLimit-Reset")

        if remaining is not None and limit is not None:
            reset_dt: Optional[datetime] = None
            if reset:
                try:
                    reset_dt = datetime.fromtimestamp(int(reset), tz=timezone.utc)
                except (ValueError, OSError):
                    pass

            self._last_rate_limit = RateLimitInfo(
                remaining=int(remaining),
                limit=int(limit),
                reset=reset_dt,
            )

    def get_rate_limit_info(self) -> Optional[RateLimitInfo]:
        """Return the last known rate-limit information."""
        return self._last_rate_limit

    # ── Repository Metadata ──────────────────────────────────────

    async def get_repo_info(self, owner: str, repo: str) -> Dict[str, Any]:
        """Fetch raw repository metadata from GitHub API."""
        return await self._get(f"/repos/{owner}/{repo}")

    async def get_repo_metadata(self, owner: str, repo: str) -> GitHubRepoMetadata:
        """Fetch and return typed repository metadata."""
        data = await self.get_repo_info(owner, repo)
        return GitHubRepoMetadata(
            owner=data.get("owner", {}).get("login", owner),
            name=data.get("name", repo),
            full_name=data.get("full_name", f"{owner}/{repo}"),
            description=data.get("description"),
            default_branch=data.get("default_branch", "main"),
            private=data.get("private", False),
            fork=data.get("fork", False),
            archived=data.get("archived", False),
            language=data.get("language"),
            topics=data.get("topics", []),
            size=data.get("size", 0),
            stargazers_count=data.get("stargazers_count", 0),
            forks_count=data.get("forks_count", 0),
            open_issues_count=data.get("open_issues_count", 0),
            html_url=data.get("html_url", ""),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            pushed_at=data.get("pushed_at"),
        )

    # ── Branch Support ───────────────────────────────────────────

    async def list_branches(
        self,
        owner: str,
        repo: str,
        max_pages: int = 3,
    ) -> List[GitHubBranch]:
        """List branches for a repository.

        Args:
            owner: Repository owner.
            repo: Repository name.
            max_pages: Max pagination pages (default 3 = ~90 branches).

        Returns:
            List of GitHubBranch.
        """
        data = await self._get_paginated(
            f"/repos/{owner}/{repo}/branches",
            max_pages=max_pages,
        )
        return [
            GitHubBranch(
                name=b.get("name", ""),
                sha=b.get("commit", {}).get("sha", ""),
                protected=b.get("protected", False),
            )
            for b in data
        ]

    async def get_default_branch(self, owner: str, repo: str) -> str:
        """Get the default branch name for a repository."""
        metadata = await self.get_repo_metadata(owner, repo)
        return metadata.default_branch

    async def branch_exists(self, owner: str, repo: str, branch: str) -> bool:
        """Check if a branch exists in the repository."""
        try:
            await self._get(f"/repos/{owner}/{repo}/branches/{branch}")
            return True
        except (GitHubError, Exception):
            return False

    # ── Issue Reading ────────────────────────────────────────────

    async def get_issue(self, owner: str, repo: str, number: int) -> GitHubIssue:
        """Fetch a single issue by number.

        Detects whether the item is an issue or pull request.
        """
        data = await self._get(f"/repos/{owner}/{repo}/issues/{number}")
        return self._parse_issue(data)

    async def list_issues(
        self,
        owner: str,
        repo: str,
        state: str = "open",
        max_pages: int = 3,
        per_page: int = 30,
    ) -> List[GitHubIssue]:
        """List repository issues (optionally filtered by state).

        Args:
            owner: Repository owner.
            repo: Repository name.
            state: 'open', 'closed', or 'all'.
            max_pages: Max pagination pages.
            per_page: Items per page (max 100).

        Returns:
            List of GitHubIssue (pull requests are included but marked).
        """
        data = await self._get_paginated(
            f"/repos/{owner}/{repo}/issues",
            params={"state": state, "per_page": min(per_page, MAX_PER_PAGE)},
            max_pages=max_pages,
        )
        return [self._parse_issue(item) for item in data]

    def _parse_issue(self, data: Dict[str, Any]) -> GitHubIssue:
        """Parse raw API response into a GitHubIssue model.

        Detects pull requests by checking for the 'pull_request' key.
        """
        # Detect PR
        is_pr = "pull_request" in data

        # Parse labels
        labels = []
        for label_data in data.get("labels", []):
            if isinstance(label_data, dict):
                labels.append(GitHubLabel(
                    name=label_data.get("name", ""),
                    color=label_data.get("color", ""),
                    description=label_data.get("description"),
                ))
            elif isinstance(label_data, str):
                labels.append(GitHubLabel(name=label_data))

        # Parse author
        author_data = data.get("user")
        author: Optional[GitHubUser] = None
        if author_data and isinstance(author_data, dict):
            author = GitHubUser(
                login=author_data.get("login", ""),
                id=author_data.get("id"),
                avatar_url=author_data.get("avatar_url"),
                html_url=author_data.get("html_url"),
            )

        # Parse assignees
        assignees = []
        for assignee_data in data.get("assignees", []):
            if isinstance(assignee_data, dict):
                assignees.append(GitHubUser(
                    login=assignee_data.get("login", ""),
                    id=assignee_data.get("id"),
                    avatar_url=assignee_data.get("avatar_url"),
                    html_url=assignee_data.get("html_url"),
                ))

        return GitHubIssue(
            number=data.get("number", 0),
            title=data.get("title", ""),
            body=data.get("body", "") or "",
            state=data.get("state", "open"),
            labels=labels,
            author=author,
            assignees=assignees,
            is_pull_request=is_pr,
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            closed_at=data.get("closed_at"),
            html_url=data.get("html_url", ""),
        )

    # ── Repository Contents ──────────────────────────────────────

    async def get_repo_contents(
        self, owner: str, repo: str, path: str = ""
    ) -> List[Dict[str, Any]]:
        """List contents of a repository directory."""
        return await self._get(f"/repos/{owner}/{repo}/contents/{path}")

    async def get_file_content(
        self, owner: str, repo: str, path: str
    ) -> Tuple[str, str]:
        """Fetch a single file's content from the repository."""
        import base64

        data = await self._get(f"/repos/{owner}/{repo}/contents/{path}")
        content = base64.b64decode(data["content"]).decode("utf-8")
        return content, data.get("download_url", "")

    async def get_archive_link(
        self, owner: str, repo: str, ref: str = "main"
    ) -> str:
        """Get the archive download URL for a repository at a given ref.

        Uses the GitHub API to resolve the archive URL without downloading it.
        """
        # The API redirects to the actual archive URL
        url = f"{self._base_url}/repos/{owner}/{repo}/zipball/{ref}"
        return url

    # ── Auth ─────────────────────────────────────────────────────

    @property
    def has_token(self) -> bool:
        """Whether a GitHub token is configured."""
        return bool(self._token)

    async def check_auth(self) -> bool:
        """Check whether the configured token is valid."""
        if not self._token:
            return False
        try:
            await self._get("/user")
            return True
        except GitHubAuthenticationError:
            return False

    def get_safe_token_preview(self) -> str:
        """Return a safe preview of the token (first 4 chars + '***')."""
        if not self._token:
            return "(none)"
        return f"{self._token[:4]}***"
