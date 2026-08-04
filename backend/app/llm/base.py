"""
Provider-independent LLM abstraction.

DevPilot agents communicate with LLMs through this interface so
the provider (OpenAI, Anthropic, local, etc.) can be swapped
without changing agent code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional


@dataclass
class LLMMessage:
    """A single message in a chat conversation."""

    role: str  # "system" | "user" | "assistant" | "tool"
    content: str
    name: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None


@dataclass
class LLMConfig:
    """Configuration for an LLM call."""

    model: str = "gpt-4o-mini"
    temperature: float = 0.3
    max_tokens: int = 4096
    top_p: float = 1.0
    stop: Optional[List[str]] = None
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponse:
    """Structured response from an LLM call."""

    content: str
    finish_reason: str = "stop"
    usage: Optional[Dict[str, int]] = None
    raw: Optional[Any] = None


class BaseLLMProvider(ABC):
    """Abstract interface for LLM providers.

    All providers (OpenAI, Anthropic, Ollama, etc.) implement this.
    """

    @abstractmethod
    async def chat(
        self,
        messages: List[LLMMessage],
        config: Optional[LLMConfig] = None,
    ) -> LLMResponse:
        """Send a chat completion request.

        Args:
            messages: Conversation history.
            config: Override configuration for this call.

        Returns:
            LLMResponse with the model's reply.
        """
        ...

    @abstractmethod
    async def chat_stream(
        self,
        messages: List[LLMMessage],
        config: Optional[LLMConfig] = None,
    ) -> AsyncIterator[str]:
        """Stream a chat completion response.

        Args:
            messages: Conversation history.
            config: Override configuration for this call.

        Yields:
            Content tokens as they are produced.
        """
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Human-readable provider name (e.g. 'openai', 'anthropic')."""

    @property
    @abstractmethod
    def default_model(self) -> str:
        """The default model identifier for this provider."""
