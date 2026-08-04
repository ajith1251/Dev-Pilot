"""
LLM provider factory.

Resolves the configured provider name to an actual provider instance.
Makes it easy to swap providers without touching agent code.

Phase 19B: when routing is enabled (default), ``get_provider()`` with no
name returns a ``RoutedProvider`` — a ``BaseLLMProvider`` facade that
delegates through the ProviderRouter (failover, retries, circuit breakers,
health-aware selection). Agents therefore gain multi-provider resilience
without any agent-side changes. Explicit ``get_provider(name)`` calls keep
returning the raw provider instance.
"""

from __future__ import annotations

from typing import Dict, Optional, Type

from app.config import settings
from app.core.exceptions import LLMProviderNotFound
from app.core.logging import logger
from app.llm.base import BaseLLMProvider
from app.llm.providers.anthropic import AnthropicProvider
from app.llm.providers.fake import FakeProvider
from app.llm.providers.gemini import GeminiProvider
from app.llm.providers.ollama import OllamaProvider
from app.llm.providers.openai import OpenAIProvider
from app.llm.providers.openrouter import OpenRouterProvider


class LLMFactory:
    """Factory that creates LLM provider instances by name."""

    _providers: Dict[str, Type[BaseLLMProvider]] = {
        "openai": OpenAIProvider,
        "anthropic": AnthropicProvider,
        "gemini": GeminiProvider,
        "openrouter": OpenRouterProvider,
        "ollama": OllamaProvider,
        "fake": FakeProvider,
    }

    def __init__(self) -> None:
        self._instances: Dict[str, BaseLLMProvider] = {}

    def register_provider(
        self, name: str, provider_cls: Type[BaseLLMProvider]
    ) -> None:
        """Register a new provider type.

        Args:
            name: Identifier for the provider (e.g. 'ollama').
            provider_cls: The provider class.
        """
        self._providers[name] = provider_cls
        logger.debug("Registered LLM provider: %s", name)

    def get_provider(self, name: Optional[str] = None) -> BaseLLMProvider:
        """Get or create a provider instance.

        Args:
            name: Provider name. When None and routing is enabled, returns a
                RoutedProvider so calls get failover + circuit breaking. When
                None and routing is disabled, defaults to settings.LLM_PROVIDER.

        Returns:
            An LLM provider instance.

        Raises:
            LLMProviderNotFound: If the provider is not registered.
        """
        if name is None and settings.PROVIDER_ROUTING_ENABLED:
            from app.llm.router import get_routed_provider

            return get_routed_provider()

        provider_name = (name or settings.LLM_PROVIDER).lower()

        if provider_name in self._instances:
            return self._instances[provider_name]

        if provider_name not in self._providers:
            raise LLMProviderNotFound(
                f"Unknown LLM provider '{provider_name}'. "
                f"Available: {list(self._providers.keys())}"
            )

        try:
            instance = self._providers[provider_name]()
            self._instances[provider_name] = instance
            logger.info("Created LLM provider: %s", provider_name)
            return instance
        except Exception as exc:
            logger.error("Failed to create provider '%s': %s", provider_name, exc)
            raise


# Global singleton
factory = LLMFactory()
