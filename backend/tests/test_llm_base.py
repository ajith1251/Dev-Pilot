"""Tests for LLM base models."""

from __future__ import annotations

from app.llm.base import LLMConfig, LLMMessage, LLMResponse


class TestLLMMessage:
    """LLMMessage creation and attributes."""

    def test_create_system_message(self) -> None:
        msg = LLMMessage(role="system", content="You are a helpful assistant.")
        assert msg.role == "system"
        assert "helpful" in msg.content

    def test_create_user_message(self) -> None:
        msg = LLMMessage(role="user", content="Hello!")
        assert msg.role == "user"
        assert msg.content == "Hello!"


class TestLLMConfig:
    """LLMConfig defaults and overrides."""

    def test_defaults(self) -> None:
        cfg = LLMConfig()
        assert cfg.model == "gpt-4o-mini"
        assert cfg.temperature == 0.3
        assert cfg.max_tokens == 4096

    def test_override(self) -> None:
        cfg = LLMConfig(model="gpt-4o", temperature=0.7, max_tokens=2048)
        assert cfg.model == "gpt-4o"
        assert cfg.temperature == 0.7
        assert cfg.max_tokens == 2048


class TestLLMResponse:
    """LLMResponse creation."""

    def test_create_response(self) -> None:
        resp = LLMResponse(content="Hello!", finish_reason="stop")
        assert resp.content == "Hello!"
        assert resp.finish_reason == "stop"

    def test_with_usage(self) -> None:
        usage = {"prompt_tokens": 10, "completion_tokens": 20}
        resp = LLMResponse(content="Hi", usage=usage)
        assert resp.usage == usage
