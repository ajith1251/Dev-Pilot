"""Tests for the IssueAnalyzerAgent (mocked dependencies)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.issue_analyzer import IssueAnalyzerAgent
from app.models.base import Severity
from app.models.issues import (
    EstimatedEffort,
    IssueAnalysisInput,
    IssueAnalysisOutput,
    IssueType,
    Requirement,
    RequirementType,
)


# ── Helpers ─────────────────────────────────────────────────────


def _make_mock_github() -> MagicMock:
    """Create a mocked GitHubService instance."""
    m = MagicMock()
    m.get_issue = AsyncMock(
        return_value={
            "title": "Login fails on mobile Safari",
            "body": (
                "## Steps to reproduce\n1. Open app in Safari on iPhone\n"
                "2. Tap login\n3. Page refreshes but no error shown\n\n"
                "Expected: User is logged in\n"
                "Actual: Page just refreshes\n\n"
                "Device: iPhone 14, iOS 17.2"
            ),
            "state": "open",
            "labels": ["bug", "mobile"],
            "url": "https://github.com/owner/repo/issues/42",
            "number": 42,
        }
    )
    m.get_repo_info = AsyncMock(
        return_value={"name": "repo", "default_branch": "main"}
    )
    return m


# ── Tests ───────────────────────────────────────────────────────


class TestIssueAnalyzerAgent:
    """IssueAnalyzerAgent tests."""

    @pytest.fixture
    def agent(self) -> IssueAnalyzerAgent:
        return IssueAnalyzerAgent()

    async def test_invalid_url_returns_error(self, agent: IssueAnalyzerAgent) -> None:
        """Invalid issue URL should return error."""
        inp = IssueAnalysisInput(issue_url="not-a-url")
        result = await agent.execute(inp)
        assert result.error is not None
        assert "Invalid" in result.error or "not-a-url" in str(result.error)

    async def test_no_input_returns_error(self, agent: IssueAnalyzerAgent) -> None:
        """No input should return error."""
        inp = IssueAnalysisInput()
        result = await agent.execute(inp)
        assert result.error is not None

    async def test_inline_title_only(self, agent: IssueAnalyzerAgent) -> None:
        """Title-only input should still produce a result."""
        inp = IssueAnalysisInput(
            title="Add dark mode support",
            body=(
                "The app should support a dark mode theme. "
                "Users have been requesting this feature."
            ),
        )
        # This will use LLM, but we'll mock the provider
        with patch("app.agents.issue_analyzer.llm_factory") as mock_factory:
            mock_provider = AsyncMock()
            mock_provider.chat = AsyncMock(
                return_value=MagicMock(
                    content=(
                        '{\n'
                        '  "summary": "Implement dark mode theme support.",\n'
                        '  "issue_type": "feature",\n'
                        '  "severity": "low",\n'
                        '  "priority_score": 3,\n'
                        '  "affected_components": ["UI", "Theming"],\n'
                        '  "requirements": [\n'
                        '    {\n'
                        '      "description": "Create dark mode color palette",\n'
                        '      "requirement_type": "ui_ux",\n'
                        '      "is_implied": false\n'
                        '    },\n'
                        '    {\n'
                        '      "description": "Add theme toggle button",\n'
                        '      "requirement_type": "functional",\n'
                        '      "is_implied": false\n'
                        '    }\n'
                        '  ],\n'
                        '  "acceptance_criteria": [\n'
                        '    "Dark mode toggle is visible in settings",\n'
                        '    "All UI components respect the theme"'
                        '  ],\n'
                        '  "suggested_labels": ["feature", "ui"],\n'
                        '  "estimated_effort": "medium",\n'
                        '  "related_files": ["src/theme/*", "src/components/*"],\n'
                        '  "needs_more_info": false,\n'
                        '  "missing_info_questions": []\n'
                        '}'
                    )
                )
            )
            mock_factory.get_provider = MagicMock(return_value=mock_provider)

            result = await agent.execute(inp)

        assert result.error is None
        assert "dark mode" in result.summary.lower()
        assert result.issue_type == IssueType.FEATURE
        assert result.severity == Severity.LOW
        assert result.priority_score == 3
        assert len(result.requirements) == 2
        assert len(result.acceptance_criteria) == 2
        assert "UI" in result.affected_components
        assert result.estimated_effort == EstimatedEffort.MEDIUM

    async def test_llm_failure_fallback(self, agent: IssueAnalyzerAgent) -> None:
        """LLM failure should return error gracefully."""
        inp = IssueAnalysisInput(
            title="Test issue",
            body="Test body",
        )

        with patch("app.agents.issue_analyzer.llm_factory") as mock_factory:
            mock_factory.get_provider = MagicMock(
                side_effect=Exception("LLM unavailable")
            )
            result = await agent.execute(inp)

        assert result.error is not None
        assert "LLM" in result.error or "unavailable" in result.error

    async def test_inline_analysis_with_github(
        self, agent: IssueAnalyzerAgent
    ) -> None:
        """Analysis with GitHub issue URL should fetch and analyse."""
        inp = IssueAnalysisInput(
            issue_url="https://github.com/owner/repo/issues/42",
        )

        agent._github = _make_mock_github()

        with patch("app.agents.issue_analyzer.llm_factory") as mock_factory:
            mock_provider = AsyncMock()
            mock_provider.chat = AsyncMock(
                return_value=MagicMock(
                    content=(
                        '{\n'
                        '  "summary": "Login flow broken on Safari mobile.",\n'
                        '  "issue_type": "bug",\n'
                        '  "severity": "critical",\n'
                        '  "priority_score": 9,\n'
                        '  "affected_components": ["Authentication", "Mobile UI"],\n'
                        '  "requirements": [\n'
                        '    {\n'
                        '      "description": "Fix login form submission on Safari",\n'
                        '      "requirement_type": "functional"\n'
                        '    }\n'
                        '  ],\n'
                        '  "acceptance_criteria": [\n'
                        '    "Login works on Safari iPhone 14 iOS 17.2"\n'
                        '  ],\n'
                        '  "suggested_labels": ["bug", "mobile", "authentication"],\n'
                        '  "estimated_effort": "small",\n'
                        '  "related_files": ["src/auth/*"],\n'
                        '  "needs_more_info": false,\n'
                        '  "missing_info_questions": []\n'
                        '}'
                    )
                )
            )
            mock_factory.get_provider = MagicMock(return_value=mock_provider)

            result = await agent.execute(inp)

        assert result.error is None
        assert result.title == "Login fails on mobile Safari"
        assert "login" in result.summary.lower()
        assert result.issue_type == IssueType.BUG
        assert result.severity == Severity.CRITICAL
        assert result.priority_score == 9
        assert "Authentication" in result.affected_components

    async def test_github_fetch_failure(self, agent: IssueAnalyzerAgent) -> None:
        """GitHub fetch failure should return error."""
        inp = IssueAnalysisInput(
            issue_url="https://github.com/owner/repo/issues/42",
        )

        mock_github = MagicMock()
        mock_github.get_issue = AsyncMock(
            side_effect=Exception("Network error")
        )
        agent._github = mock_github

        result = await agent.execute(inp)
        assert result.error is not None
        assert "Network" in result.error or "Failed" in result.error

    async def test_parse_json_with_fences(self, agent: IssueAnalyzerAgent) -> None:
        """JSON parsing should handle markdown code fences."""
        text = (
            "```json\n"
            '{"summary": "test", "issue_type": "bug"}\n'
            "```"
        )
        result = agent._parse_json_response(text)
        assert result.get("summary") == "test"
        assert result.get("issue_type") == "bug"

    async def test_parse_json_plain(self, agent: IssueAnalyzerAgent) -> None:
        """JSON parsing should work without fences."""
        text = '{"summary": "test", "issue_type": "bug"}'
        result = agent._parse_json_response(text)
        assert result.get("summary") == "test"

    async def test_parse_json_empty(self, agent: IssueAnalyzerAgent) -> None:
        """Empty response should return empty dict."""
        result = agent._parse_json_response("")
        assert result == {}

    async def test_run_wraps_execute(self, agent: IssueAnalyzerAgent) -> None:
        """Agent.run() should wrap execute() and set status."""
        inp = IssueAnalysisInput(
            title="Test",
            body="Test body",
        )

        with patch("app.agents.issue_analyzer.llm_factory") as mock_factory:
            mock_provider = AsyncMock()
            mock_provider.chat = AsyncMock(
                return_value=MagicMock(
                    content=(
                        '{"summary": "test", "issue_type": "feature", '
                        '"severity": "low", "priority_score": 1}'
                    )
                )
            )
            mock_factory.get_provider = MagicMock(return_value=mock_provider)

            await agent.run(inp)

        assert agent.status.value == "success"

    async def test_acceptance_criteria_properly_parsed(
        self, agent: IssueAnalyzerAgent
    ) -> None:
        """Acceptance criteria should be extracted as a list."""
        data = {
            "summary": "test",
            "issue_type": "feature",
            "severity": "low",
            "priority_score": 3,
            "acceptance_criteria": [
                "Criterion 1",
                "Criterion 2",
            ],
        }
        output = agent._build_output("Test", data)
        assert len(output.acceptance_criteria) == 2
        assert "Criterion 1" in output.acceptance_criteria

    async def test_requirements_properly_parsed(
        self, agent: IssueAnalyzerAgent
    ) -> None:
        """Requirements should be extracted as Requirement objects."""
        data = {
            "summary": "test",
            "issue_type": "feature",
            "severity": "low",
            "priority_score": 3,
            "requirements": [
                {
                    "description": "Do X",
                    "requirement_type": "functional",
                    "is_implied": False,
                },
                {
                    "description": "Do Y",
                    "requirement_type": "security",
                    "is_implied": True,
                    "acceptance_note": "Check with audit",
                },
            ],
        }
        output = agent._build_output("Test", data)
        assert len(output.requirements) == 2
        assert output.requirements[0].description == "Do X"
        assert output.requirements[0].requirement_type == RequirementType.FUNCTIONAL
        assert output.requirements[1].requirement_type == RequirementType.SECURITY
        assert output.requirements[1].is_implied is True
        assert output.requirements[1].acceptance_note == "Check with audit"


class TestIssueAnalyzerEdgeCases:
    """Edge cases for the Issue Analyzer."""

    async def test_vague_issue_sets_needs_more_info(self) -> None:
        """Vague issues should set needs_more_info."""
        agent = IssueAnalyzerAgent()
        inp = IssueAnalysisInput(
            title="Fix stuff",
            body="Something is broken, please fix",
        )

        with patch("app.agents.issue_analyzer.llm_factory") as mock_factory:
            mock_provider = AsyncMock()
            mock_provider.chat = AsyncMock(
                return_value=MagicMock(
                    content=(
                        '{\n'
                        '  "summary": "Unclear what needs fixing.",\n'
                        '  "issue_type": "bug",\n'
                        '  "severity": "medium",\n'
                        '  "priority_score": 5,\n'
                        '  "needs_more_info": true,\n'
                        '  "missing_info_questions": [\n'
                        '    "What exactly is broken?",\n'
                        '    "What are the steps to reproduce?"\n'
                        '  ],\n'
                        '  "estimated_effort": "uncertain"\n'
                        '}'
                    )
                )
            )
            mock_factory.get_provider = MagicMock(return_value=mock_provider)

            result = await agent.execute(inp)

        assert result.needs_more_info is True
        assert len(result.missing_info_questions) >= 1
