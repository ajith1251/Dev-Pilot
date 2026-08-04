"""Anthropic Claude provider."""

from __future__ import annotations

from typing import Any, AsyncIterator, Dict, List, Optional

from anthropic import AsyncAnthropic

from app.config import settings
from app.core.exceptions import LLMConfigurationError, LLMError
from app.llm.base import BaseLLMProvider, LLMConfig, LLMMessage, LLMResponse


class AnthropicProvider(BaseLLMProvider):
    """LLM provider for Anthropic's Claude models."""

    def __init__(self) -> None:
        api_key = settings.ANTHROPIC_API_KEY
        if not api_key:
            raise LLMConfigurationError(
                "ANTHROPIC_API_KEY is not set. "
                "Set it in your .env file or environment."
            )

        self._client = AsyncAnthropic(api_key=api_key)

    @property
    def provider_name(self) -> str:
        return "anthropic"

    @property
    def default_model(self) -> str:
        return "claude-sonnet-4-20250514"

    def _resolve_model(self, cfg: LLMConfig) -> str:
        """Pick the model for a call, ignoring the OpenAI-sentinel default.

        Agents call provider.chat(..., config=LLMConfig(temperature=...)) with
        no model, and LLMConfig() defaults to "gpt-4o-mini" (OpenAI-specific).
        Sending that to the Anthropic API would fail, so treat the sentinel
        value as "unset" and fall back to the Claude default.
        """
        model = (cfg.model or "").strip()
        if not model or model == LLMConfig().model:
            return self.default_model
        return model

    def _to_anthropic_messages(self, messages: List[LLMMessage]) -> tuple:
        """Convert DevPilot messages to Anthropic format."""
        system_msg = None
        anthropic_msgs = []

        for m in messages:
            if m.role == "system":
                system_msg = m.content
            else:
                role = "assistant" if m.role == "assistant" else "user"
                anthropic_msgs.append({"role": role, "content": m.content})

        return system_msg, anthropic_msgs

    async def chat(
        self,
        messages: List[LLMMessage],
        config: Optional[LLMConfig] = None,
    ) -> LLMResponse:
        cfg = config or LLMConfig()
        system_msg, anthropic_msgs = self._to_anthropic_messages(messages)

        try:
            response = await self._client.messages.create(
                model=self._resolve_model(cfg),
                messages=anthropic_msgs,
                system=system_msg or "",
                max_tokens=cfg.max_tokens,
                temperature=cfg.temperature,
                top_p=cfg.top_p,
            )
        except Exception as exc:
            raise LLMError(f"Anthropic chat call failed: {exc}") from exc

        content = "".join(
            block.text for block in response.content if hasattr(block, "text")
        )

        return LLMResponse(
            content=content,
            finish_reason=response.stop_reason or "stop",
            usage={
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
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
        system_msg, anthropic_msgs = self._to_anthropic_messages(messages)

        try:
            async with self._client.messages.stream(
                model=self._resolve_model(cfg),
                messages=anthropic_msgs,
                system=system_msg or "",
                max_tokens=cfg.max_tokens,
                temperature=cfg.temperature,
                top_p=cfg.top_p,
            ) as stream:
                async for text in stream.text_stream:
                    yield text
        except Exception as exc:
            raise LLMError(f"Anthropic stream call failed: {exc}") from exc
