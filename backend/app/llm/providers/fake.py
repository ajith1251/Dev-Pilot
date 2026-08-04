"""Deterministic fake LLM provider (no API key required).

Registered as "fake" so that the default `.env` value
(DEVPILOT_LLM_PROVIDER=fake) is a *valid* provider instead of raising
LLMProviderNotFound when any agent asks the factory for a provider.

Returns a fixed, deterministic response — this is the LLM-side analogue of
the FakeEmbeddingProvider used for vector retrieval. It lets the whole
pipeline run without any network access, and is intentionally *not*
accepted by the demo `--live` guards (those require a real provider).
"""

from __future__ import annotations

from typing import AsyncIterator, Optional

from app.llm.base import BaseLLMProvider, LLMConfig, LLMMessage, LLMResponse

# Deterministic canned payload. Kept as a minimal, self-contained response.
_FAKE_CONTENT = (
    "Deterministic fake provider response. "
    "Set DEVPILOT_LLM_PROVIDER and a real API key to run live agent reasoning."
)


class FakeProvider(BaseLLMProvider):
    """LLM provider that returns a fixed deterministic response."""

    @property
    def provider_name(self) -> str:
        return "fake"

    @property
    def default_model(self) -> str:
        return "fake-model"

    async def chat(
        self,
        messages: list[LLMMessage],
        config: Optional[LLMConfig] = None,
    ) -> LLMResponse:
        return LLMResponse(content=_FAKE_CONTENT, finish_reason="stop")

    async def chat_stream(
        self,
        messages: list[LLMMessage],
        config: Optional[LLMConfig] = None,
    ) -> AsyncIterator[str]:
        yield _FAKE_CONTENT
