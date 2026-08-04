"""OpenRouter provider (multi-model routing via OpenAI-compatible API).

OpenRouter exposes an OpenAI-compatible API at https://openrouter.ai/api/v1.
This provider is a thin OpenAI-compatible wrapper that carries the
OpenRouter-specific headers, so the same code path used by OpenAI works for
every model OpenRouter aggregates.
"""

from __future__ import annotations

from typing import AsyncIterator, List, Optional

from openai import AsyncOpenAI

from app.config import settings
from app.core.exceptions import LLMConfigurationError, LLMError
from app.llm.base import BaseLLMProvider, LLMConfig, LLMMessage, LLMResponse


class OpenRouterProvider(BaseLLMProvider):
    """LLM provider for OpenRouter (OpenAI-compatible aggregator)."""

    def __init__(self) -> None:
        api_key = settings.OPENROUTER_API_KEY
        if not api_key:
            raise LLMConfigurationError(
                "OPENROUTER_API_KEY is not set. "
                "Set it in your .env file or environment."
            )
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=settings.OPENROUTER_BASE_URL,
            default_headers={
                "HTTP-Referer": "https://devpilot.local",
                "X-Title": "DevPilot",
            },
        )

    @property
    def provider_name(self) -> str:
        return "openrouter"

    @property
    def default_model(self) -> str:
        return "openrouter/auto"

    async def chat(
        self,
        messages: List[LLMMessage],
        config: Optional[LLMConfig] = None,
    ) -> LLMResponse:
        cfg = config or LLMConfig()
        openai_messages = [
            {"role": m.role, "content": m.content} for m in messages
        ]
        model = cfg.model if cfg.model and cfg.model != LLMConfig().model \
            else self.default_model
        try:
            response = await self._client.chat.completions.create(
                model=model,
                messages=openai_messages,
                temperature=cfg.temperature,
                max_tokens=cfg.max_tokens,
                top_p=cfg.top_p,
                stop=cfg.stop,
            )
        except Exception as exc:
            raise LLMError(f"OpenRouter chat call failed: {exc}") from exc

        choice = response.choices[0]
        return LLMResponse(
            content=choice.message.content or "",
            finish_reason=choice.finish_reason or "stop",
            usage={
                "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                "completion_tokens": response.usage.completion_tokens if response.usage else 0,
                "total_tokens": response.usage.total_tokens if response.usage else 0,
            }
            if response.usage
            else None,
            raw=response,
        )

    async def chat_stream(
        self,
        messages: List[LLMMessage],
        config: Optional[LLMConfig] = None,
    ) -> AsyncIterator[str]:
        cfg = config or LLMConfig()
        openai_messages = [
            {"role": m.role, "content": m.content} for m in messages
        ]
        model = cfg.model if cfg.model and cfg.model != LLMConfig().model \
            else self.default_model
        try:
            stream = await self._client.chat.completions.create(
                model=model,
                messages=openai_messages,
                temperature=cfg.temperature,
                max_tokens=cfg.max_tokens,
                top_p=cfg.top_p,
                stop=cfg.stop,
                stream=True,
            )
        except Exception as exc:
            raise LLMError(f"OpenRouter stream call failed: {exc}") from exc

        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                yield delta.content
