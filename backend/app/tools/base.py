"""
Base tool abstraction.

Tools are discrete, reusable capabilities that agents can invoke
(e.g. read file, search code, run tests, call GitHub API).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from app.core.logging import logger
from app.models.base import new_id


class BaseTool(ABC):
    """Abstract base for all DevPilot tools."""

    def __init__(
        self,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> None:
        self.name = name or self.__class__.__name__
        self.description = description or ""
        self.id = new_id()

    @abstractmethod
    async def execute(self, **kwargs: Any) -> Any:
        """Execute the tool with the given keyword arguments.

        Args:
            **kwargs: Tool-specific parameters.

        Returns:
            Tool-specific result.
        """
        ...

    async def run(self, **kwargs: Any) -> Any:
        """Public entry-point with logging.

        Args:
            **kwargs: Tool-specific parameters.

        Returns:
            Tool-specific result.
        """
        logger.debug("Tool %s executing with args: %s", self.name, kwargs)
        try:
            result = await self.execute(**kwargs)
            logger.debug("Tool %s completed", self.name)
            return result
        except Exception as exc:
            logger.error("Tool %s failed: %s", self.name, exc)
            raise

    @property
    def tool_schema(self) -> Dict[str, Any]:
        """Return a JSON-schema-like description of this tool.

        Override in subclasses to provide structured schemas
        for LLM function-calling.
        """
        return {
            "name": self.name,
            "description": self.description,
        }
