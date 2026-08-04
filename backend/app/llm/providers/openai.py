"""OpenAI / compatible API provider."""

from __future__ import annotations

from typing import Any, AsyncIterator, Dict, List, Optional

from openai import AsyncOpenAI

from app.config import settings
from app.core.exceptions import LLMConfigurationError, LLMError
from app.llm.base import BaseLLMProvider, LLMConfig, LLMMessage, LLMResponse


class OpenAIProvider(BaseLLMProvider):
    """LLM provider that uses the OpenAI (or compatible) API."""

    def __init__(self) -> None:
        api_key = settings.OPENAI_API_KEY
        if not api_key:
            raise LLMConfigurationError(
                "OPENAI_API_KEY is not set. "
                "Set it in your .env file or environment."
            )

        kwargs: Dict[str, Any] = {"api_key": api_key}
        if settings.OPENAI_BASE_URL:
            kwargs["base_url"] = settings.OPENAI_BASE_URL

        self._client = AsyncOpenAI(**kwargs)

    @property
    def provider_name(self) -> str:
        return "openai"

    @property
    def default_model(self) -> str:
        return settings.LLM_MODEL or "gpt-4o-mini"

    def _build_args(
        self,
        messages: List[LLMMessage],
        config: Optional[LLMConfig],
    ) -> tuple:
        cfg = config or LLMConfig()
        openai_messages = [
            {"role": m.role, "content": m.content} for m in messages
        ]
        return openai_messages, cfg

    async def chat(
        self,
        messages: List[LLMMessage],
        config: Optional[LLMConfig] = None,
    ) -> LLMResponse:
        openai_msgs, cfg = self._build_args(messages, config)

        try:
            response = await self._client.chat.completions.create(
                model=cfg.model,
                messages=openai_msgs,
                temperature=cfg.temperature,
                max_tokens=cfg.max_tokens,
                top_p=cfg.top_p,
                stop=cfg.stop,
            )
        except Exception as exc:
            raise LLMError(f"OpenAI chat call failed: {exc}") from exc

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
        openai_msgs, cfg = self._build_args(messages, config)

        try:
            stream = await self._client.chat.completions.create(
                model=cfg.model,
                messages=openai_msgs,
                temperature=cfg.temperature,
                max_tokens=cfg.max_tokens,
                top_p=cfg.top_p,
                stop=cfg.stop,
                stream=True,
            )
        except Exception as exc:
            raise LLMError(f"OpenAI stream call failed: {exc}") from exc

        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                yield delta.content
