"""NVIDIA NIM provider (OpenAI-compatible inference microservices).

NVIDIA NIM exposes an OpenAI-compatible chat-completions API. The hosted build
sits at ``https://integrate.api.nvidia.com/v1`` and serves NVIDIA-built and
partner open models. This provider is a thin OpenAI-compatible wrapper — the
same code path used by OpenAI/OpenRouter — so every model a NIM endpoint
serves works without provider-specific code. Point ``NVIDIA_BASE_URL`` at a
self-hosted NIM microservice for a private deployment.
"""

from __future__ import annotations

from typing import AsyncIterator, List, Optional

from openai import AsyncOpenAI

from app.config import settings
from app.core.exceptions import LLMConfigurationError, LLMError
from app.llm.base import BaseLLMProvider, LLMConfig, LLMMessage, LLMResponse


class NvidiaProvider(BaseLLMProvider):
    """LLM provider for NVIDIA NIM (OpenAI-compatible inference endpoint)."""

    def __init__(self) -> None:
        api_key = settings.NVIDIA_API_KEY
        if not api_key:
            raise LLMConfigurationError(
                "NVIDIA_API_KEY is not set. "
                "Set it in your .env file or environment."
            )
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=settings.NVIDIA_BASE_URL,
            timeout=settings.NVIDIA_TIMEOUT_SECONDS,
            max_retries=settings.NVIDIA_MAX_RETRIES,
        )

    @property
    def provider_name(self) -> str:
        return "nvidia"

    @property
    def default_model(self) -> str:
        # Independent of settings.LLM_MODEL (which is OpenAI-biased):
        # DEVPILOT_NVIDIA_MODEL pins the NIM model so all agent stages keep it,
        # falling back to the hosted NIM default.
        return settings.NVIDIA_MODEL or "meta/llama-3.1-8b-instruct"

    def _resolve_model(self, cfg: LLMConfig) -> str:
        """Pick the model for a call, ignoring the OpenAI-sentinel default.

        Agents call provider.chat(..., config=LLMConfig(temperature=...)) with
        no model, and LLMConfig() defaults to "gpt-4o-mini" (OpenAI-specific).
        Sending that to NIM would request a nonexistent model, so treat the
        sentinel value as "unset" and use the NVIDIA default.
        """
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
            raise LLMError(f"NVIDIA chat call failed: {exc}") from exc

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
            raise LLMError(f"NVIDIA stream call failed: {exc}") from exc

        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                yield delta.content
