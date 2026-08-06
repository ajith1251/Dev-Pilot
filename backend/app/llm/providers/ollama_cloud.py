"""Ollama Cloud provider (OpenAI-compatible remote inference).

Ollama's cloud (https://ollama.com/cloud) exposes hosted models — including
larger ones that need a GPU — through an OpenAI-compatible chat-completions
API at ``https://ollama.com/v1``. This provider is a thin OpenAI-compatible
wrapper (same code path as the generic ``OpenAICompatibleProvider``) so every
model an account serves works without provider-specific code.

Configuration (Phase 20F multi-provider configuration):
- OLLAMA_CLOUD_API_KEY (required) — API key from
  https://ollama.com/settings/keys.
- OLLAMA_CLOUD_BASE_URL (optional) — defaults to https://ollama.com/v1.
- DEVPILOT_OLLAMA_CLOUD_MODEL (optional) — model override, e.g.
  'gemma4:31b' or 'gpt-oss:120b'. Unset defaults to 'gemma4:31b' (live-verified
  to return content even at small max_tokens; gpt-oss/nemotron models on this
  endpoint can return empty content at max_tokens<64).
- DEVPILOT_OLLAMA_CLOUD_TIMEOUT_SECONDS / DEVPILOT_OLLAMA_CLOUD_MAX_RETRIES —
  per-request client timeout and transport-level retries.
"""

from __future__ import annotations

from openai import AsyncOpenAI

from app.config import settings
from app.core.exceptions import LLMConfigurationError
from app.llm.providers.openai_compatible import OpenAICompatibleProvider

_DEFAULT_BASE_URL = "https://ollama.com/v1"


class OllamaCloudProvider(OpenAICompatibleProvider):
    """LLM provider for Ollama's cloud API (OpenAI-compatible endpoint)."""

    def __init__(self) -> None:
        api_key = settings.OLLAMA_CLOUD_API_KEY
        if not api_key:
            raise LLMConfigurationError(
                "OLLAMA_CLOUD_API_KEY is not set. "
                "Get one at https://ollama.com/settings/keys."
            )
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=(settings.OLLAMA_CLOUD_BASE_URL or _DEFAULT_BASE_URL),
            timeout=settings.OLLAMA_CLOUD_TIMEOUT_SECONDS,
            max_retries=settings.OLLAMA_CLOUD_MAX_RETRIES,
        )

    @property
    def provider_name(self) -> str:
        return "ollama_cloud"

    @property
    def default_model(self) -> str:
        return settings.OLLAMA_CLOUD_MODEL or "gemma4:31b"
