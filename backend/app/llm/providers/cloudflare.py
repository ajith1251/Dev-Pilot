"""Cloudflare Workers AI provider (OpenAI-compatible inference endpoint).

Workers AI exposes an OpenAI-compatible chat-completions API at
``https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/ai/v1``. This
provider is a thin OpenAI-compatible wrapper — the same code path used by
OpenAI/OpenRouter/NVIDIA — so every model an account serves works without
provider-specific code.

Configuration (Phase 20F multi-provider configuration):
- CLOUDFLARE_API_KEY (required) — API token from your Cloudflare dashboard.
- CLOUDFLARE_ACCOUNT_ID (required unless CLOUDFLARE_BASE_URL is set) — used
  to build the default base URL.
- CLOUDFLARE_BASE_URL (optional) — explicit override of the Workers AI
  OpenAI-compatible endpoint.
- DEVPILOT_CLOUDFLARE_MODEL (optional) — model override, e.g.
  '@cf/meta/llama-4-scout-17b-16e-instruct' (default — live-verified fastest
  Workers AI model, ~0.5s TTF, and a 17B MoE vs the 8B dense alternative; the
  previously-pinned '@cf/meta/llama-3.1-8b-instruct' was deprecated by
  Cloudflare in 2026) or '@cf/meta/llama-3.1-8b-instruct-fp8'.
- DEVPILOT_CLOUDFLARE_TIMEOUT_SECONDS / DEVPILOT_CLOUDFLARE_MAX_RETRIES —
  per-request client timeout and transport-level retries.
"""

from __future__ import annotations

from typing import AsyncIterator, List, Optional

from openai import AsyncOpenAI

from app.config import settings
from app.core.exceptions import LLMConfigurationError, LLMError
from app.llm.base import BaseLLMProvider, LLMConfig, LLMMessage, LLMResponse


class CloudflareProvider(BaseLLMProvider):
    """LLM provider for Cloudflare Workers AI (OpenAI-compatible endpoint)."""

    def __init__(self) -> None:
        api_key = settings.CLOUDFLARE_API_KEY
        if not api_key:
            raise LLMConfigurationError(
                "CLOUDFLARE_API_KEY is not set. "
                "Set it in your .env file or environment."
            )
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=self._resolve_base_url(),
            timeout=settings.CLOUDFLARE_TIMEOUT_SECONDS,
            max_retries=settings.CLOUDFLARE_MAX_RETRIES,
        )

    @staticmethod
    def _resolve_base_url() -> str:
        configured = (settings.CLOUDFLARE_BASE_URL or "").strip()
        if configured:
            return configured
        account_id = (settings.CLOUDFLARE_ACCOUNT_ID or "").strip()
        if not account_id:
            raise LLMConfigurationError(
                "CLOUDFLARE_ACCOUNT_ID is not set (and CLOUDFLARE_BASE_URL is "
                "empty). Workers AI needs your account id to build the "
                "OpenAI-compatible base URL, or set CLOUDFLARE_BASE_URL "
                "explicitly."
            )
        return (
            "https://api.cloudflare.com/client/v4/accounts/"
            f"{account_id}/ai/v1"
        )

    @property
    def provider_name(self) -> str:
        return "cloudflare"

    @property
    def default_model(self) -> str:
        return settings.CLOUDFLARE_MODEL or "@cf/meta/llama-4-scout-17b-16e-instruct"

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
            raise LLMError(f"Cloudflare chat call failed: {exc}") from exc

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
            raise LLMError(f"Cloudflare stream call failed: {exc}") from exc

        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            content = delta.content if delta else None
            # llama-4-scout's stream can emit non-str (int) content deltas —
            # skip them so only real text tokens reach the caller.
            if isinstance(content, str) and content:
                yield content
