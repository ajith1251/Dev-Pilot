"""Centralized LLM provider registry — the single place to add a provider.

Adding a provider to DevPilot is one spec entry here plus a config block in
``app/config.py`` and a ``BaseLLMProvider`` implementation in
``app/llm/providers/``. Everything else derives from this table:

- ``LLMFactory`` builds its provider map from ``provider_classes()``.
- ``ProviderRouter`` builds availability checks + the canonical (default
  priority) order from ``provider_availability()`` / ``provider_names()``.
- Health/metrics/config snapshots iterate the registry order automatically.

No agent code, factory code, or router routing logic needs to change to add
a provider.

Phase 20F: multi-provider configuration & backup provider integration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Type

from app.llm.base import BaseLLMProvider
from app.llm.providers.anthropic import AnthropicProvider
from app.llm.providers.cloudflare import CloudflareProvider
from app.llm.providers.fake import FakeProvider
from app.llm.providers.gemini import GeminiProvider
from app.llm.providers.nvidia import NvidiaProvider
from app.llm.providers.ollama import OllamaProvider
from app.llm.providers.ollama_cloud import OllamaCloudProvider
from app.llm.providers.opencode_zen import OpencodeZenProvider
from app.llm.providers.openai import OpenAIProvider
from app.llm.providers.openai_compatible import OpenAICompatibleProvider
from app.llm.providers.openrouter import OpenRouterProvider


@dataclass(frozen=True)
class ProviderSpec:
    """Static metadata for one registered provider."""

    name: str
    provider_class: Type[BaseLLMProvider]
    availability_attr: str
    """Settings attribute checked for "configured" (e.g. 'NVIDIA_API_KEY').

    An empty string means the provider has no config gate; the provider is
    available only when ``always_available`` is True (e.g. 'fake')."""

    always_available: bool = False
    description: str = ""


_PROVIDER_SPECS: Dict[str, ProviderSpec] = {}
_PROVIDER_ORDER: List[str] = []


def register_provider(
    name: str,
    provider_class: Type[BaseLLMProvider],
    availability_attr: str = "",
    always_available: bool = False,
    description: str = "",
) -> None:
    """Register (or replace) a provider spec, appending to canonical order."""
    spec = ProviderSpec(
        name=name,
        provider_class=provider_class,
        availability_attr=availability_attr,
        always_available=always_available,
        description=description,
    )
    if name not in _PROVIDER_SPECS:
        _PROVIDER_ORDER.append(name)
    _PROVIDER_SPECS[name] = spec


# ── Built-in providers, in canonical / default-priority order ──
register_provider(
    "nvidia",
    NvidiaProvider,
    "NVIDIA_API_KEY",
    description="NVIDIA NIM hosted inference microservices (default provider)",
)
register_provider(
    "gemini",
    GeminiProvider,
    "GEMINI_API_KEY",
    description="Google Gemini (Google AI Studio; free or paid tier)",
)
register_provider(
    "cloudflare",
    CloudflareProvider,
    "CLOUDFLARE_API_KEY",
    description="Cloudflare Workers AI (OpenAI-compatible)",
)
register_provider(
    "ollama_cloud",
    OllamaCloudProvider,
    "OLLAMA_CLOUD_API_KEY",
    description="Ollama Cloud remote inference (OpenAI-compatible)",
)
register_provider(
    "opencode_zen",
    OpencodeZenProvider,
    "OPENCODE_ZEN_API_KEY",
    description="OpenCode Zen curated AI gateway (OpenAI-compatible)",
)
register_provider(
    "openai",
    OpenAIProvider,
    "OPENAI_API_KEY",
    description="OpenAI (or a custom OPENAI_BASE_URL endpoint)",
)
register_provider(
    "anthropic",
    AnthropicProvider,
    "ANTHROPIC_API_KEY",
    description="Anthropic Claude",
)
register_provider(
    "openrouter",
    OpenRouterProvider,
    "OPENROUTER_API_KEY",
    description="OpenRouter multi-model router (OpenAI-compatible)",
)
register_provider(
    "ollama",
    OllamaProvider,
    "OLLAMA_BASE_URL",
    description="Local Ollama server (OpenAI-compatible)",
)
register_provider(
    "openai_compatible",
    OpenAICompatibleProvider,
    "OPENAI_COMPATIBLE_BASE_URL",
    description="Generic OpenAI-compatible endpoint (vLLM, TGI, llama.cpp, ...)",
)
register_provider(
    "fake",
    FakeProvider,
    "",
    always_available=True,
    description="Deterministic in-memory provider (tests / no-LLM fallback)",
)


def provider_names() -> Tuple[str, ...]:
    """Canonical provider names in registration (default priority) order."""
    return tuple(_PROVIDER_ORDER)


def provider_classes() -> Dict[str, Type[BaseLLMProvider]]:
    """Provider name → provider class, for LLMFactory registration."""
    return {
        name: spec.provider_class for name, spec in _PROVIDER_SPECS.items()
    }


def provider_availability() -> Dict[str, Tuple[str, bool]]:
    """Provider name → (availability settings attr, always-present flag)."""
    return {
        name: (spec.availability_attr, spec.always_available)
        for name, spec in _PROVIDER_SPECS.items()
    }


def get_spec(name: str) -> Optional[ProviderSpec]:
    """Look up a spec by name, or None when the provider is unknown."""
    return _PROVIDER_SPECS.get(name)
