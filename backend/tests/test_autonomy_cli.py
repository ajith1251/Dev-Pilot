"""
Tests for the Phase 16 CLI autonomy commands — existence, signatures, and
argparse wiring. Command execution is mocked (no LLM / live PostgreSQL).
"""

from __future__ import annotations

import argparse
import inspect
from unittest.mock import AsyncMock, patch

import pytest


class TestAutonomyCli:
    def test_cli_has_autonomy_functions(self) -> None:
        from app import cli_autonomy

        assert callable(cli_autonomy.run_autonomous_run)
        assert callable(cli_autonomy.run_autonomous_status)
        assert callable(cli_autonomy.run_autonomous_dry_run)
        assert callable(cli_autonomy.run_autonomous_control)
        assert callable(cli_autonomy.add_cli_commands)

    def test_run_autonomous_run_signature(self) -> None:
        from app.cli_autonomy import run_autonomous_run

        sig = inspect.signature(run_autonomous_run)
        params = list(sig.parameters.keys())
        assert "repo" in params
        assert "task" in params

    def test_add_cli_commands_registers_subparsers(self) -> None:
        from app.cli_autonomy import add_cli_commands

        parser = argparse.ArgumentParser(prog="devpilot")
        subparsers = parser.add_subparsers(dest="command")
        add_cli_commands(subparsers)
        args = parser.parse_args(["autonomous-run", "repo", "Fix tokens"])
        assert args.command == "autonomous-run"
        assert args.repo == "repo"

    def test_cli_main_wires_autonomy_commands(self) -> None:
        from app import cli

        assert callable(cli.main)
        # cli.py lazily imports autonomy command runners.
        assert "autonomy" in inspect.getsource(cli)

    @pytest.mark.asyncio
    async def test_run_autonomous_run_invokes_controller(self) -> None:
        from app import cli_autonomy

        # run_autonomous_run calls state.status_summary() in BOTH the
        # json_output branch and _print_status, so the fake needs it.
        fake_state = type("S", (), {
            "goal_id": "GOAL-ABC12345",
            "state": type("St", (), {"value": "completed"})(),
            "status_summary": lambda self: {"goal_id": "GOAL-ABC12345", "state": "completed"},
        })()
        # cli_autonomy imports the controller lazily inside the command, so
        # patch the service-module symbol (re-read on every call).
        with patch("app.services.autonomy_service.AutonomousExecutionController") as mock_cls:
            inst = AsyncMock()
            inst.create_goal = AsyncMock(return_value=fake_state)
            inst.start = AsyncMock(return_value=fake_state)
            mock_cls.return_value = inst
            # json_output=True: the JSON branch only calls status_summary()
            # (the fake provides it) and skips _print_status entirely.
            await cli_autonomy.run_autonomous_run(repo="repo", task="Fix tokens",
                                                  json_output=True)
        inst.create_goal.assert_awaited_once()
        inst.start.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_autonomous_control_actions(self) -> None:
        from app import cli_autonomy

        fake_state = type("S", (), {
            "state": type("St", (), {"value": "paused"})(),
            "escalations": [],  # run_autonomous_control reads state.escalations
        })()
        with patch("app.services.autonomy_service.AutonomousExecutionController") as mock_cls:
            inst = AsyncMock()
            inst.pause = AsyncMock(return_value=fake_state)
            mock_cls.return_value = inst
            await cli_autonomy.run_autonomous_control("pause", "GOAL-1")
        inst.pause.assert_awaited_once_with("GOAL-1")

    @pytest.mark.asyncio
    async def test_dry_run_invokes_controller(self) -> None:
        from app import cli_autonomy

        report = type("R", (), {"summary": lambda self: {"feasibility": "ok"}})()
        with patch("app.services.autonomy_service.AutonomousExecutionController") as mock_cls:
            inst = AsyncMock()
            inst.dry_run = AsyncMock(return_value=report)
            mock_cls.return_value = inst
            # json_output=True avoids printing the report fields.
            await cli_autonomy.run_autonomous_dry_run(repo="repo", task="Fix tokens",
                                                      json_output=True)
        inst.dry_run.assert_awaited_once()
