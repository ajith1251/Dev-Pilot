"""Ollama provider (local OpenAI-compatible endpoint).

Ollama exposes an OpenAI-compatible API at `/v1`, so this provider reuses the
AsyncOpenAI client pointed at the local server. It requires no API key (the
client needs *some* key string; Ollama ignores it) and is always available
when OLLAMA_BASE_URL is configured.

This is the reference for how a "future" provider is added:
1. Implement BaseLLMProvider in app/llm/providers/.
2. Register it in LLMFactory._providers.
3. Add its availability check to ProviderRouter.
"""

from __future__ import annotations

from typing import AsyncIterator, List, Optional

from openai import AsyncOpenAI

from app.config import settings
from app.core.exceptions import LLMConfigurationError, LLMError
from app.llm.base import BaseLLMProvider, LLMConfig, LLMMessage, LLMResponse


class OllamaProvider(BaseLLMProvider):
    """LLM provider for a local Ollama server (OpenAI-compatible)."""

    def __init__(self) -> None:
        base_url = settings.OLLAMA_BASE_URL
        if not base_url:
            raise LLMConfigurationError(
                "OLLAMA_BASE_URL is not set. Point it at your local Ollama "
                "OpenAI-compatible endpoint (e.g. http://localhost:11434/v1)."
            )
        self._base_url = base_url
        # Ollama's OpenAI shim requires an Authorization header but ignores
        # its value; a non-empty placeholder is standard practice.
        self._client = AsyncOpenAI(base_url=base_url, api_key="ollama")

    @property
    def provider_name(self) -> str:
        return "ollama"

    @property
    def default_model(self) -> str:
        return "llama3.2"

    def _build_args(
        self,
        messages: List[LLMMessage],
        config: Optional[LLMConfig],
    ) -> tuple:
        cfg = config or LLMConfig()
        openai_messages = [
            {"role": m.role, "content": m.content} for m in messages
        ]
        # LLMConfig() defaults to the OpenAI sentinel 'gpt-4o-mini'; treat it
        # as "unset" so a local Ollama server gets this provider's model.
        model = cfg.model if cfg.model and cfg.model != LLMConfig().model \
            else self.default_model
        return openai_messages, cfg, model

    async def chat(
        self,
        messages: List[LLMMessage],
        config: Optional[LLMConfig] = None,
    ) -> LLMResponse:
        openai_msgs, cfg, model = self._build_args(messages, config)
        try:
            response = await self._client.chat.completions.create(
                model=model,
                messages=openai_msgs,
                temperature=cfg.temperature,
                max_tokens=cfg.max_tokens,
                top_p=cfg.top_p,
                stop=cfg.stop,
            )
        except Exception as exc:
            raise LLMError(f"Ollama chat call failed: {exc}") from exc

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
        openai_msgs, cfg, model = self._build_args(messages, config)
        try:
            stream = await self._client.chat.completions.create(
                model=model,
                messages=openai_msgs,
                temperature=cfg.temperature,
                max_tokens=cfg.max_tokens,
                top_p=cfg.top_p,
                stop=cfg.stop,
                stream=True,
            )
        except Exception as exc:
            raise LLMError(f"Ollama stream call failed: {exc}") from exc

        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                yield delta.content
