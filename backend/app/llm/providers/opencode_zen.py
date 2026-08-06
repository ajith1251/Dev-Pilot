"""OpenCode Zen provider (curated AI gateway, OpenAI-compatible).

OpenCode Zen (https://opencode.ai/docs/zen) is a curated gateway of tested
LLM models — GPT, Claude, Gemini and open-source — served through one
OpenAI-compatible API key at ``https://opencode.ai/zen/v1``. This provider is
a thin OpenAI-compatible wrapper (same code path as the generic
``OpenAICompatibleProvider``) so every model the gateway serves works without
provider-specific code.

Configuration (Phase 20F multi-provider configuration):
- OPENCODE_ZEN_API_KEY (required) — API key from https://opencode.ai/zen.
- OPENCODE_ZEN_BASE_URL (optional) — defaults to https://opencode.ai/zen/v1.
- DEVPILOT_OPENCODE_ZEN_MODEL (optional) — model override, e.g.
  'deepseek-v4-flash-free' or 'claude-sonnet-4-5'. Unset defaults to a
  curated fast (free-tier) model.
- DEVPILOT_OPENCODE_ZEN_TIMEOUT_SECONDS / DEVPILOT_OPENCODE_ZEN_MAX_RETRIES —
  per-request client timeout and transport-level retries.
"""

from __future__ import annotations

from openai import AsyncOpenAI

from app.config import settings
from app.core.exceptions import LLMConfigurationError
from app.llm.providers.openai_compatible import OpenAICompatibleProvider

_DEFAULT_BASE_URL = "https://opencode.ai/zen/v1"


class OpencodeZenProvider(OpenAICompatibleProvider):
    """LLM provider for the OpenCode Zen gateway (OpenAI-compatible endpoint)."""

    def __init__(self) -> None:
        api_key = settings.OPENCODE_ZEN_API_KEY
        if not api_key:
            raise LLMConfigurationError(
                "OPENCODE_ZEN_API_KEY is not set. "
                "Get one at https://opencode.ai/zen."
            )
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=(settings.OPENCODE_ZEN_BASE_URL or _DEFAULT_BASE_URL),
            timeout=settings.OPENCODE_ZEN_TIMEOUT_SECONDS,
            max_retries=settings.OPENCODE_ZEN_MAX_RETRIES,
        )

    @property
    def provider_name(self) -> str:
        return "opencode_zen"

    @property
    def default_model(self) -> str:
        return settings.OPENCODE_ZEN_MODEL or "deepseek-v4-flash-free"
