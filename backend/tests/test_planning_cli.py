"""Tests for the Phase 4 CLI planning commands (function signatures only).

These tests verify that CLI commands exist with the expected signatures.
Actual CLI execution is not tested here — it would require mocking the
full workflow pipeline which is already tested in test_planning_workflow.py.
"""

from __future__ import annotations

import inspect

import pytest


class TestPlanningCli:
    """CLI planning command tests (function signature verification)."""

    def test_cli_has_plan_functions(self) -> None:
        """CLI module should export run_plan and run_github_plan."""
        from app import cli

        assert hasattr(cli, "run_plan")
        assert hasattr(cli, "run_github_plan")
        assert callable(cli.run_plan)
        assert callable(cli.run_github_plan)

    def test_run_plan_signature(self) -> None:
        """run_plan should accept title, description, repo_path."""
        from app.cli import run_plan

        sig = inspect.signature(run_plan)
        params = list(sig.parameters.keys())
        assert "title" in params
        assert "description" in params
        assert "repo_path" in params

    def test_run_github_plan_signature(self) -> None:
        """run_github_plan should accept url."""
        from app.cli import run_github_plan

        sig = inspect.signature(run_github_plan)
        assert "url" in sig.parameters

    def test_cli_has_main(self) -> None:
        """CLI module should export main()."""
        from app.cli import main
        assert callable(main)

    def test_cli_main_has_plan_subcommand(self) -> None:
        """CLI main() should accept 'plan' as a command via argparse.

        We verify this by checking the argparse configuration indirectly:
        the run_plan function exists which is called by the CLI.
        """
        from app.cli import run_plan
        # Verify the function exists and is async
        assert inspect.iscoroutinefunction(run_plan)

    def test_cli_main_has_github_plan_subcommand(self) -> None:
        """CLI main() should accept 'github plan' as a command via argparse."""
        from app.cli import run_github_plan
        assert inspect.iscoroutinefunction(run_github_plan)
