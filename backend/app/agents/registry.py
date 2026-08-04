"""
Agent registry — a central registry for discovering and managing agents.

Agents are registered by name and can be looked up dynamically.
"""

from __future__ import annotations

from typing import Dict, Optional, Type

from app.agents.base import BaseAgent
from app.core.exceptions import AgentNotFoundError


class AgentRegistry:
    """Registry that maps agent names to agent classes."""

    def __init__(self) -> None:
        self._agents: Dict[str, Type[BaseAgent]] = {}

    def register(self, agent_cls: Type[BaseAgent], *, name: Optional[str] = None) -> None:
        """Register an agent class.

        Args:
            agent_cls: The agent class to register.
            name: Optional override name (defaults to class name).
        """
        key = name or agent_cls.__name__
        self._agents[key] = agent_cls

    def get(self, name: str) -> Type[BaseAgent]:
        """Look up an agent class by name.

        Args:
            name: Agent name.

        Returns:
            The registered agent class.

        Raises:
            AgentNotFoundError: If the agent is not registered.
        """
        if name not in self._agents:
            raise AgentNotFoundError(f"Agent '{name}' is not registered")
        return self._agents[name]

    def list_agents(self) -> Dict[str, str]:
        """Return a dict of {name: description} for all registered agents."""
        return {
            name: cls.description if hasattr(cls, "description") else ""
            for name, cls in self._agents.items()
        }

    def __contains__(self, name: str) -> bool:
        return name in self._agents


# ── Import agent implementations so they are registered on import ──
# (Lazy imports here to avoid circular dependencies)


def register_default_agents() -> None:
    """Register all built-in agents."""
    from app.agents.repo_analyzer import RepositoryAnalyzerAgent
    from app.agents.issue_analyzer import IssueAnalyzerAgent
    from app.agents.planner import PlannerAgent

    registry.register(RepositoryAnalyzerAgent, name="repository_analyzer")
    registry.register(IssueAnalyzerAgent, name="issue_analyzer")
    registry.register(PlannerAgent, name="planner")


# Global singleton
registry = AgentRegistry()

# Auto-register default agents on import
register_default_agents()
