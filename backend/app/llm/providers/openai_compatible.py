"""Generic OpenAI-compatible provider (vLLM, TGI, llama.cpp, LM Studio, ...).

Any server that implements the OpenAI chat-completions API can be a backup
provider: self-hosted vLLM/TGI, llama.cpp/llama-server, LM Studio, Ollama
remotes, OpenAI-compatible cloud gateways, etc. Point
``OPENAI_COMPATIBLE_BASE_URL`` at the endpoint and optionally set an API key
and model — no provider-specific code is needed.

Configuration (Phase 20F multi-provider configuration):
- OPENAI_COMPATIBLE_BASE_URL (required) — base URL of the OpenAI-compatible
  endpoint, e.g. 'http://localhost:8000/v1' (vLLM) or a cloud gateway.
- OPENAI_COMPATIBLE_API_KEY (optional) — most cloud gateways require one;
  local servers usually ignore it, so it may stay unset.
- DEVPILOT_OPENAI_COMPATIBLE_MODEL (optional) — model served by the endpoint,
  e.g. 'meta-llama/Meta-Llama-3.1-8B-Instruct'. Set this for real use; the
  built-in fallback is the OpenAI-sentinel default.
- DEVPILOT_OPENAI_COMPATIBLE_TIMEOUT_SECONDS / _MAX_RETRIES — per-request
  client timeout and transport-level retries.
"""

from __future__ import annotations

from typing import AsyncIterator, List, Optional

from openai import AsyncOpenAI

from app.config import settings
from app.core.exceptions import LLMConfigurationError, LLMError
from app.llm.base import BaseLLMProvider, LLMConfig, LLMMessage, LLMResponse


class OpenAICompatibleProvider(BaseLLMProvider):
    """LLM provider for an arbitrary OpenAI-compatible chat-completions endpoint."""

    def __init__(self) -> None:
        base_url = settings.OPENAI_COMPATIBLE_BASE_URL
        if not base_url:
            raise LLMConfigurationError(
                "OPENAI_COMPATIBLE_BASE_URL is not set. "
                "Point it at an OpenAI-compatible chat-completions endpoint "
                "(e.g. a self-hosted vLLM/TGI server or an OpenAI-compatible "
                "cloud gateway)."
            )
        # The OpenAI client needs a key string; keyless local endpoints ignore
        # its value, so a placeholder is standard practice (like Ollama).
        api_key = settings.OPENAI_COMPATIBLE_API_KEY or "openai-compatible"
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=settings.OPENAI_COMPATIBLE_TIMEOUT_SECONDS,
            max_retries=settings.OPENAI_COMPATIBLE_MAX_RETRIES,
        )

    @property
    def provider_name(self) -> str:
        return "openai_compatible"

    @property
    def default_model(self) -> str:
        # Set DEVPILOT_OPENAI_COMPATIBLE_MODEL to the model your endpoint
        # serves; the fallback mirrors the OpenAI sentinel so an unset value
        # still produces a valid (if generic) request.
        return settings.OPENAI_COMPATIBLE_MODEL or LLMConfig().model

    def _resolve_model(self, cfg: LLMConfig) -> str:
        """Pick the model for a call, ignoring the OpenAI-sentinel default."""
        model = (cfg.model or "").strip()
        if not model or model == LLMConfig().model:
            return self.default_model
        return model

    async def chat(
        self,
        messages: List[LLMMessage],
        config: Optional[LLMConfig] = None,
    ) -> LLMResponse:
        cfg = config or LLMConfig()
        openai_messages = [
            {"role": m.role, "content": m.content} for m in messages
        ]
        model = self._resolve_model(cfg)
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
            raise LLMError(
                f"OpenAI-compatible chat call failed: {exc}"
            ) from exc

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
        model = self._resolve_model(cfg)
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
            raise LLMError(
                f"OpenAI-compatible stream call failed: {exc}"
            ) from exc

        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                yield delta.content
