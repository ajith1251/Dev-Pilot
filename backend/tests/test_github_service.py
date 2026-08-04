"""Tests for the GitHub service (URL parsing, no network calls)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.github import GitHubService


class TestGitHubURLParsing:
    """GitHubService URL parsing (no network)."""

    def test_parse_issue_url_valid(self) -> None:
        owner, repo, number = GitHubService.parse_issue_url(
            "https://github.com/owner/repo/issues/42"
        )
        assert owner == "owner"
        assert repo == "repo"
        assert number == 42

    def test_parse_issue_url_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid GitHub issue URL"):
            GitHubService.parse_issue_url("https://example.com/foo")

    def test_parse_repo_url(self) -> None:
        owner, repo = GitHubService.parse_repo_url(
            "https://github.com/owner/repo"
        )
        assert owner == "owner"
        assert repo == "repo"

    def test_parse_repo_url_with_git_suffix(self) -> None:
        owner, repo = GitHubService.parse_repo_url(
            "https://github.com/owner/repo.git"
        )
        assert owner == "owner"
        assert repo == "repo"

    def test_has_token_true_when_set(self) -> None:
        """Token should be detected when explicitly passed."""
        service = GitHubService(token="ghp_test")
        assert service.has_token is True
        assert "***" in service.get_safe_token_preview()
        assert "ghp_test" not in service.get_safe_token_preview()
        # Preview should show first 4 chars
        assert service.get_safe_token_preview().startswith("ghp_")

    def test_token_preview_masks_sensitive_data(self) -> None:
        """Token preview should never expose the full token."""
        service = GitHubService(token="supersecret123")
        preview = service.get_safe_token_preview()
        assert "supersecret123" not in preview
        assert "***" in preview
        assert len(preview) > 4
