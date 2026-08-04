"""
Abstract base agent for DevPilot.

Every agent in the system — Repository Analyzer, Coding Agent,
Test Agent, etc. — inherits from BaseAgent.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Generic, TypeVar

from app.models.base import AgentStatus, new_id

logger = logging.getLogger("devpilot")

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


class BaseAgent(ABC, Generic[InputT, OutputT]):
    """Abstract base for all DevPilot agents.

    Type parameters:
        InputT:  The kind of input this agent accepts.
        OutputT: The kind of output this agent produces.
    """

    def __init__(
        self,
        name: str | None = None,
        description: str | None = None,
        max_retries: int = 3,
    ) -> None:
        self.name = name or self.__class__.__name__
        self.description = description or ""
        self.max_retries = max_retries
        self.status = AgentStatus.IDLE
        self.id = new_id()
        self.created_at = datetime.now(timezone.utc)
        self.updated_at = datetime.now(timezone.utc)

    @abstractmethod
    async def execute(self, inp: InputT) -> OutputT:
        """Execute the agent on the given input.

        Subclasses must implement this method.

        Args:
            inp: Agent-specific input payload.

        Returns:
            Agent-specific output payload.
        """
        ...

    async def run(self, inp: InputT) -> OutputT:
        """Public entry-point — wraps execute with logging & status tracking.

        Args:
            inp: Input for the agent.

        Returns:
            Agent output.
        """
        self.status = AgentStatus.RUNNING
        logger.info("Agent %s started | input: %s", self.name, type(inp).__name__)
        try:
            result = await self.execute(inp)
            self.status = AgentStatus.SUCCESS
            logger.info("Agent %s completed successfully", self.name)
            return result
        except Exception as exc:
            self.status = AgentStatus.FAILED
            logger.error("Agent %s failed: %s", self.name, exc)
            raise

    def reset(self) -> None:
        """Reset the agent to idle state."""
        self.status = AgentStatus.IDLE
        self.updated_at = datetime.now(timezone.utc)
