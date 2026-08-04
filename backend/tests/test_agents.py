"""Tests for agent base class and registry."""

from __future__ import annotations

import pytest

from app.agents.base import BaseAgent
from app.agents.registry import AgentRegistry
from app.core.exceptions import AgentNotFoundError
from app.models.base import AgentStatus


# ── Test agent implementation ───────────────────────────────────


class _EchoAgent(BaseAgent[str, str]):
    """Agent that echoes input for testing."""

    async def execute(self, inp: str) -> str:
        return f"echo: {inp}"


class _FailingAgent(BaseAgent[str, str]):
    """Agent that always fails for testing."""

    async def execute(self, inp: str) -> str:
        raise ValueError("Intentional failure")


# ── Tests ───────────────────────────────────────────────────────


class TestBaseAgent:
    """BaseAgent functionality."""

    async def test_execute_echo(self) -> None:
        agent = _EchoAgent(name="echobot")
        result = await agent.run("hello")
        assert result == "echo: hello"
        assert agent.status == AgentStatus.SUCCESS

    async def test_auto_name(self) -> None:
        agent = _EchoAgent()
        assert agent.name == "_EchoAgent"

    async def test_execute_failure_sets_status(self) -> None:
        agent = _FailingAgent()
        with pytest.raises(ValueError, match="Intentional failure"):
            await agent.run("fail")
        assert agent.status == AgentStatus.FAILED

    async def test_reset(self) -> None:
        agent = _FailingAgent()
        with pytest.raises(ValueError):
            await agent.run("fail")
        assert agent.status == AgentStatus.FAILED
        agent.reset()
        assert agent.status == AgentStatus.IDLE


class TestAgentRegistry:
    """AgentRegistry functionality."""

    def test_register_and_get(self) -> None:
        test_registry = AgentRegistry()
        test_registry.register(_EchoAgent, name="echo")
        cls = test_registry.get("echo")
        assert cls == _EchoAgent

    def test_get_unknown_raises(self) -> None:
        test_registry = AgentRegistry()
        with pytest.raises(AgentNotFoundError):
            test_registry.get("nonexistent")

    def test_list_agents(self) -> None:
        test_registry = AgentRegistry()
        test_registry.register(_EchoAgent, name="echo")
        test_registry.register(_FailingAgent, name="failer")
        listing = test_registry.list_agents()
        assert "echo" in listing
        assert "failer" in listing

    def test_contains(self) -> None:
        test_registry = AgentRegistry()
        test_registry.register(_EchoAgent, name="echo")
        assert "echo" in test_registry
        assert "nonexistent" not in test_registry
