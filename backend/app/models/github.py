"""GitHub data models for Phase 3 — typed representations of GitHub resources."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class GitHubRepositoryRef(BaseModel):
    """A parsed GitHub repository reference."""

    owner: str = Field(description="Repository owner (user or organization)")
    repo: str = Field(description="Repository name")
    ref: Optional[str] = Field(default=None, description="Branch, tag, or commit SHA")


class GitHubIssueRef(BaseModel):
    """A parsed GitHub issue reference."""

    owner: str
    repo: str
    number: int


class GitHubLabel(BaseModel):
    """A GitHub issue label."""

    name: str
    color: str = ""
    description: Optional[str] = None


class GitHubUser(BaseModel):
    """Minimal GitHub user/author representation."""

    login: str
    id: Optional[int] = None
    avatar_url: Optional[str] = None
    html_url: Optional[str] = None


class GitHubRepoMetadata(BaseModel):
    """Repository metadata from GitHub API."""

    owner: str = Field(description="Repository owner login")
    name: str = Field(description="Repository name")
    full_name: str = Field(description="owner/name")
    description: Optional[str] = None
    default_branch: str = "main"
    private: bool = False
    fork: bool = False
    archived: bool = False
    language: Optional[str] = None
    topics: List[str] = Field(default_factory=list)
    size: int = 0
    stargazers_count: int = 0
    forks_count: int = 0
    open_issues_count: int = 0
    html_url: str = ""
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    pushed_at: Optional[str] = None


class GitHubBranch(BaseModel):
    """A GitHub branch."""

    name: str
    sha: str = ""
    protected: bool = False


class GitHubIssue(BaseModel):
    """A GitHub issue (or pull request)."""

    number: int
    title: str
    body: str = ""
    state: str = "open"
    labels: List[GitHubLabel] = Field(default_factory=list)
    author: Optional[GitHubUser] = None
    assignees: List[GitHubUser] = Field(default_factory=list)
    is_pull_request: bool = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    closed_at: Optional[str] = None
    html_url: str = ""


class RateLimitInfo(BaseModel):
    """GitHub API rate limit information."""

    remaining: int = 0
    limit: int = 60
    reset: Optional[datetime] = None
    used: int = 0


class AcquisitionMetadata(BaseModel):
    """Metadata about a repository acquisition operation."""

    source_url: str = Field(description="Original GitHub URL")
    ref: str = Field(description="Checked out ref")
    local_path: str = Field(description="Temporary local path")
    acquired_at: str = Field(description="ISO timestamp of acquisition")
    duration_seconds: float = 0.0
    is_shallow: bool = True


class RemoteRepositoryProfile(BaseModel):
    """Combined result of GitHub metadata + local analysis."""

    github: GitHubRepoMetadata = Field(description="GitHub metadata")
    ref: str = Field(description="Analyzed ref (branch/commit)")
    profile: Optional[dict] = None  # RepositoryProfile as dict (serialized)
    acquisition: AcquisitionMetadata = Field(description="Acquisition info")
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
