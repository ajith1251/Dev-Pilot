"""Tests for the RepositoryAnalyzerAgent (mocked dependencies)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.repo_analyzer import RepositoryAnalyzerAgent
from app.models.analysis import (
    DirectoryNode,
    FileInfo,
    RepositoryAnalysisInput,
    RepositoryAnalysisOutput,
    RepositoryStructure,
)


# ── Mocks ───────────────────────────────────────────────────────


def _mock_repo_info() -> dict:
    return {
        "name": "test-repo",
        "full_name": "owner/test-repo",
        "default_branch": "main",
        "description": "A test repository",
    }


def _mock_contents(owner: str, repo: str, path: str = "") -> list:
    """Return mock GitHub API contents response.

    Must accept (owner, repo, path) to match GitHubService.get_repo_contents.
    """
    if path == "":
        return [
            {"name": "src", "path": "src", "type": "dir", "size": 0},
            {"name": "README.md", "path": "README.md", "type": "file", "size": 100},
            {"name": "main.py", "path": "main.py", "type": "file", "size": 200},
            {"name": "requirements.txt", "path": "requirements.txt", "type": "file", "size": 50},
        ]
    if path == "src":
        return [
            {"name": "utils.py", "path": "src/utils.py", "type": "file", "size": 300},
            {"name": "__init__.py", "path": "src/__init__.py", "type": "file", "size": 0},
        ]
    return []


def _mock_file_content(owner: str, repo: str, path: str) -> tuple:
    return ("print('hello')", f"https://raw.github.com/{owner}/{repo}/{path}")


def _make_mock_github() -> MagicMock:
    """Create a mocked GitHubService instance with controlled async methods."""
    m = MagicMock()
    m.get_repo_info = AsyncMock(return_value=_mock_repo_info())
    m.get_repo_contents = AsyncMock(side_effect=_mock_contents)
    m.get_file_content = AsyncMock(side_effect=_mock_file_content)
    return m


# ── Tests ───────────────────────────────────────────────────────


class TestRepositoryAnalyzerAgent:
    """RepositoryAnalyzerAgent tests with mocked GitHub API."""

    @pytest.fixture
    def agent(self) -> RepositoryAnalyzerAgent:
        return RepositoryAnalyzerAgent()

    async def test_invalid_url_returns_error(self, agent: RepositoryAnalyzerAgent) -> None:
        inp = RepositoryAnalysisInput(repo_url="not-a-url")
        result = await agent.execute(inp)
        assert result.error is not None
        assert "Invalid" in result.error or "not-a-url" in result.repo_name

    async def test_successful_analysis_basic(self, agent: RepositoryAnalyzerAgent) -> None:
        """Test basic analysis with mocked GitHub."""
        inp = RepositoryAnalysisInput(
            repo_url="https://github.com/owner/test-repo",
            include_llm_summary=False,
        )

        agent._github = _make_mock_github()
        result = await agent.execute(inp)

        assert result.error is None
        assert result.repo_name == "owner/test-repo"
        assert result.default_branch == "main"
        assert result.structure.total_files >= 4
        assert result.structure.total_dirs >= 1

    async def test_languages_detected(self, agent: RepositoryAnalyzerAgent) -> None:
        """Python files should be detected."""
        inp = RepositoryAnalysisInput(
            repo_url="https://github.com/owner/test-repo",
            include_llm_summary=False,
        )

        agent._github = _make_mock_github()
        result = await agent.execute(inp)

        lang_names = {l.name for l in result.languages}
        assert "Python" in lang_names

    async def test_frameworks_detected(self, agent: RepositoryAnalyzerAgent) -> None:
        """pip should be detected from requirements.txt."""
        inp = RepositoryAnalysisInput(
            repo_url="https://github.com/owner/test-repo",
            include_llm_summary=False,
        )

        agent._github = _make_mock_github()
        result = await agent.execute(inp)

        fw_names = {f.name for f in result.frameworks}
        assert "pip" in fw_names

    async def test_entry_points_detected(self, agent: RepositoryAnalyzerAgent) -> None:
        """main.py should be detected as entry point."""
        inp = RepositoryAnalysisInput(
            repo_url="https://github.com/owner/test-repo",
            include_llm_summary=False,
        )

        agent._github = _make_mock_github()
        result = await agent.execute(inp)

        names = {f.name for f in result.entry_points}
        assert "main.py" in names

    async def test_config_files_detected(self, agent: RepositoryAnalyzerAgent) -> None:
        """requirements.txt should be detected as config."""
        inp = RepositoryAnalysisInput(
            repo_url="https://github.com/owner/test-repo",
            include_llm_summary=False,
        )

        agent._github = _make_mock_github()
        result = await agent.execute(inp)

        config_names = {f.name for f in result.config_files}
        assert "requirements.txt" in config_names

    async def test_github_error_handling(self, agent: RepositoryAnalyzerAgent) -> None:
        """If GitHub API fails, an error should be returned."""
        inp = RepositoryAnalysisInput(
            repo_url="https://github.com/owner/test-repo",
            include_llm_summary=False,
        )

        agent._github = _make_mock_github()
        agent._github.get_repo_info = AsyncMock(side_effect=Exception("API unavailable"))
        result = await agent.execute(inp)

        assert result.error is not None
        assert "API" in result.error or "Failed" in result.error

    async def test_run_wraps_execute(self, agent: RepositoryAnalyzerAgent) -> None:
        """Agent.run() should wrap execute() and set success status."""
        inp = RepositoryAnalysisInput(
            repo_url="https://github.com/owner/test-repo",
            include_llm_summary=False,
        )

        agent._github = _make_mock_github()
        result = await agent.run(inp)

        assert agent.status.value == "success"
        assert result.repo_name == "owner/test-repo"


# ── Edge case tests ─────────────────────────────────────────────


class TestAnalyzerEdgeCases:
    """Edge cases for the analyzer."""

    async def test_max_depth_limit(self) -> None:
        """Very deep repos should stop at max_depth."""
        agent = RepositoryAnalyzerAgent()
        inp = RepositoryAnalysisInput(
            repo_url="https://github.com/owner/deep-repo",
            max_depth=3,
            include_llm_summary=False,
        )

        def deep_contents(owner: str, repo: str, path: str = "") -> list:
            if path.count("/") < 4:
                return [
                    {
                        "name": "subdir",
                        "path": f"{path}/subdir" if path else "subdir",
                        "type": "dir",
                        "size": 0,
                    }
                ]
            return []

        agent._github = _make_mock_github()
        agent._github.get_repo_contents = AsyncMock(side_effect=deep_contents)
        result = await agent.execute(inp)

        assert result.error is None

    async def test_empty_repository(self) -> None:
        """Empty repo should return empty structure."""
        agent = RepositoryAnalyzerAgent()
        inp = RepositoryAnalysisInput(
            repo_url="https://github.com/owner/empty-repo",
            include_llm_summary=False,
        )

        agent._github = _make_mock_github()
        agent._github.get_repo_contents = AsyncMock(return_value=[])
        result = await agent.execute(inp)

        assert result.error is None
        assert result.structure.total_files == 0
        assert len(result.languages) == 0
