"""Tests for LLM provider registry, Gemini + Fake providers, and config."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.llm.base import LLMConfig, LLMMessage
from app.llm.factory import LLMFactory, factory


class TestFactoryRegistry:
    """LLMFactory provider registration."""

    def test_known_providers_registered(self) -> None:
        assert "openai" in factory._providers
        assert "anthropic" in factory._providers
        assert "gemini" in factory._providers
        assert "fake" in factory._providers

    def test_factory_can_instantiate_fake(self) -> None:
        provider = factory.get_provider("fake")
        assert provider.provider_name == "fake"
        assert provider.default_model == "fake-model"

    def test_factory_unknown_provider_raises(self) -> None:
        from app.core.exceptions import LLMProviderNotFound

        fresh = LLMFactory()
        with pytest.raises(LLMProviderNotFound):
            fresh.get_provider("not-a-provider")

    def test_register_provider_extends_registry(self) -> None:
        from app.llm.base import BaseLLMProvider

        class Dummy(BaseLLMProvider):
            async def chat(self, messages, config=None):  # pragma: no cover
                raise NotImplementedError

            async def chat_stream(self, messages, config=None):  # pragma: no cover
                raise NotImplementedError

            @property
            def provider_name(self) -> str:
                return "dummy"

            @property
            def default_model(self) -> str:
                return "dummy-model"

        fresh = LLMFactory()
        fresh.register_provider("dummy", Dummy)
        assert "dummy" in fresh._providers


class TestFakeProvider:
    """Deterministic fake provider."""

    @pytest.mark.asyncio
    async def test_chat_returns_deterministic_content(self) -> None:
        provider = factory.get_provider("fake")
        r1 = await provider.chat([LLMMessage(role="user", content="hi")])
        r2 = await provider.chat([LLMMessage(role="user", content="bye")])
        assert r1.content == r2.content
        assert "Deterministic fake provider" in r1.content
        assert r1.finish_reason == "stop"

    @pytest.mark.asyncio
    async def test_chat_stream_yields_content(self) -> None:
        from app.llm.providers.fake import _FAKE_CONTENT

        provider = factory.get_provider("fake")
        chunks = [c async for c in provider.chat_stream(
            [LLMMessage(role="user", content="hi")])]
        assert chunks == [_FAKE_CONTENT]


class TestGeminiProvider:
    """Gemini provider configuration + call translation."""

    def test_requires_api_key(self) -> None:
        from app.config import settings
        from app.core.exceptions import LLMConfigurationError
        from app.llm.providers.gemini import GeminiProvider

        with patch.object(settings, "GEMINI_API_KEY", None):
            with pytest.raises(LLMConfigurationError):
                GeminiProvider()

    def test_provider_name_and_default_model(self) -> None:
        from app.config import settings
        from app.llm.providers.gemini import GeminiProvider

        with patch.object(settings, "GEMINI_API_KEY", "test-key"), \
             patch.object(settings, "LLM_MODEL", "gpt-4o-mini"):
            provider = GeminiProvider()
            assert provider.provider_name == "gemini"
            # default_model is independent of the OpenAI-biased LLM_MODEL
            assert provider.default_model == "gemini-3.6-flash"

    def test_resolve_model_ignores_openai_sentinel(self) -> None:
        from app.config import settings
        from app.llm.providers.gemini import GeminiProvider

        with patch.object(settings, "GEMINI_API_KEY", "test-key"):
            provider = GeminiProvider()
        # LLMConfig() defaults to "gpt-4o-mini" — must be treated as "unset"
        assert provider._resolve_model(LLMConfig()) == "gemini-3.6-flash"
        assert provider._resolve_model(LLMConfig(model="")) == "gemini-3.6-flash"
        assert provider._resolve_model(LLMConfig(model="gpt-4o-mini")) == "gemini-3.6-flash"
        # an explicit Gemini model is honored
        assert provider._resolve_model(
            LLMConfig(model="gemini-3.6-pro-preview")) == "gemini-3.6-pro-preview"

    @pytest.mark.asyncio
    async def test_chat_translates_messages_and_returns_response(self) -> None:
        from app.config import settings
        from app.llm.providers.gemini import GeminiProvider

        fake_client = MagicMock()
        fake_response = MagicMock()
        fake_response.text = "hello from gemini"
        fake_response.candidates = []
        fake_response.usage_metadata = None
        fake_client.aio.models.generate_content = AsyncMock(
            return_value=fake_response)

        with patch.object(settings, "GEMINI_API_KEY", "test-key"), patch(
            "app.llm.providers.gemini.genai.Client",
            return_value=fake_client,
        ):
            provider = GeminiProvider()
            result = await provider.chat(
                [
                    LLMMessage(role="system", content="be brief"),
                    LLMMessage(role="user", content="hello"),
                ],
                config=LLMConfig(model="gemini-2.5-flash", temperature=0.1),
            )

        assert result.content == "hello from gemini"
        assert result.finish_reason == "stop"
        call = fake_client.aio.models.generate_content.await_args
        assert call is not None
        kwargs = call.kwargs
        assert kwargs["model"] == "gemini-2.5-flash"
        assert kwargs["config"]["temperature"] == 0.1
        assert kwargs["config"]["system_instruction"] == "be brief"
        assert kwargs["contents"][0]["role"] == "user"

    @pytest.mark.asyncio
    async def test_chat_retries_on_rate_limit_then_succeeds(self) -> None:
        from app.config import settings
        from app.llm.providers.gemini import GeminiProvider

        class _RateLimited(Exception):
            code = 429
            retry_delay = None

        good_response = MagicMock()
        good_response.text = "ok"
        good_response.candidates = []
        good_response.usage_metadata = None

        fake_client = MagicMock()
        call = AsyncMock(side_effect=[
            _RateLimited("RESOURCE_EXHAUSTED: quota exceeded"),
            good_response,
        ])
        fake_client.aio.models.generate_content = call

        with patch.object(settings, "GEMINI_API_KEY", "test-key"), patch(
            "app.llm.providers.gemini.genai.Client", return_value=fake_client,
        ), patch("app.llm.providers.gemini.asyncio.sleep", new=AsyncMock()):
            provider = GeminiProvider()
            result = await provider.chat([LLMMessage(role="user", content="hi")])

        assert result.content == "ok"
        assert call.await_count == 2  # one retry after the 429

    @pytest.mark.asyncio
    async def test_chat_gives_up_after_max_retries(self) -> None:
        from app.config import settings
        from app.core.exceptions import LLMError
        from app.llm.providers.gemini import GeminiProvider

        class _RateLimited(Exception):
            code = 429
            retry_delay = None

        fake_client = MagicMock()
        call = AsyncMock(side_effect=_RateLimited("RESOURCE_EXHAUSTED: nope"))
        fake_client.aio.models.generate_content = call

        with patch.object(settings, "GEMINI_API_KEY", "test-key"), patch(
            "app.llm.providers.gemini.genai.Client", return_value=fake_client,
        ), patch("app.llm.providers.gemini.asyncio.sleep", new=AsyncMock()):
            provider = GeminiProvider()
            with pytest.raises(LLMError):
                await provider.chat([LLMMessage(role="user", content="hi")])

        assert call.await_count == 7  # 1 initial + 6 retries

    def test_permanent_daily_quota_is_not_retryable(self) -> None:
        from app.llm.providers.gemini import _is_permanent_quota, _is_rate_limited

        class _DailyCap(Exception):
            code = 429

        exc = _DailyCap(
            "429 RESOURCE_EXHAUSTED. You exceeded your current quota, "
            "please check your plan and billing details.")
        assert _is_permanent_quota(exc) is True
        assert _is_rate_limited(exc) is False

        # The per-minute free-tier message stays retryable.
        class _PerMinute(Exception):
            code = 429

        exc2 = _PerMinute(
            "429 RESOURCE_EXHAUSTED: generate_content_free_tier_requests, "
            "limit: 5/min")
        assert _is_permanent_quota(exc2) is False
        assert _is_rate_limited(exc2) is True

    @pytest.mark.asyncio
    async def test_chat_fails_fast_when_all_candidates_exhausted(self) -> None:
        from app.config import settings
        from app.core.exceptions import LLMError
        from app.llm.providers.gemini import GeminiProvider

        class _DailyCap(Exception):
            code = 429

        fake_client = MagicMock()
        call = AsyncMock(side_effect=_DailyCap(
            "You exceeded your current quota, please check your plan and "
            "billing details."))
        fake_client.aio.models.generate_content = call

        with patch.object(settings, "GEMINI_API_KEY", "test-key"), patch(
            "app.llm.providers.gemini.genai.Client", return_value=fake_client,
        ), patch("app.llm.providers.gemini.asyncio.sleep", new=AsyncMock()):
            provider = GeminiProvider()
            with pytest.raises(LLMError, match="daily quota exhausted"):
                await provider.chat([LLMMessage(role="user", content="hi")])

        # One probe per candidate model (3.6-flash, flash-lite, 3.5-flash),
        # no backoff — permanent caps are never retried with sleeps.
        assert call.await_count == 3
        used = [c.kwargs["model"] for c in call.await_args_list]
        assert used == ["gemini-3.6-flash", "gemini-3.5-flash-lite",
                        "gemini-3.5-flash"]

    @pytest.mark.asyncio
    async def test_chat_fails_over_to_next_model_on_daily_quota(self) -> None:
        from app.config import settings
        from app.llm.providers.gemini import GeminiProvider

        class _DailyCap(Exception):
            code = 429

        good_response = MagicMock()
        good_response.text = "ok"
        good_response.candidates = []
        good_response.usage_metadata = None

        fake_client = MagicMock()
        call = AsyncMock(side_effect=[
            _DailyCap(
                "You exceeded your current quota, please check your plan "
                "and billing details."),
            good_response,
        ])
        fake_client.aio.models.generate_content = call

        with patch.object(settings, "GEMINI_API_KEY", "test-key"), patch(
            "app.llm.providers.gemini.genai.Client", return_value=fake_client,
        ), patch("app.llm.providers.gemini.asyncio.sleep", new=AsyncMock()):
            provider = GeminiProvider()
            result = await provider.chat([LLMMessage(role="user", content="hi")])

        assert result.content == "ok"
        assert call.await_count == 2
        # default gemini-3.6-flash hit the daily cap → fail over to flash-lite
        used = [c.kwargs["model"] for c in call.await_args_list]
        assert used == ["gemini-3.6-flash", "gemini-3.5-flash-lite"]

    def test_resolve_model_skips_exhausted_models(self) -> None:
        from app.config import settings
        from app.llm.providers.gemini import GeminiProvider, time as _time

        with patch.object(settings, "GEMINI_API_KEY", "test-key"):
            provider = GeminiProvider()
        assert provider._resolve_model(LLMConfig()) == "gemini-3.6-flash"
        provider._exhausted_at["gemini-3.6-flash"] = _time.monotonic()
        assert provider._resolve_model(LLMConfig()) == "gemini-3.5-flash-lite"
        provider._exhausted_at["gemini-3.5-flash-lite"] = _time.monotonic()
        assert provider._resolve_model(LLMConfig()) == "gemini-3.5-flash"
        provider._exhausted_at["gemini-3.5-flash"] = _time.monotonic()
        # all exhausted → falls back to the (exhausted) preferred model; the
        # caller's _with_retry turns that into the clear all-exhausted error
        assert provider._resolve_model(LLMConfig()) == "gemini-3.6-flash"
        # backward-compatible set view stays in sync with the dict
        assert provider._exhausted_models == {
            "gemini-3.6-flash", "gemini-3.5-flash-lite", "gemini-3.5-flash"}

    def test_exhausted_marker_expires_after_ttl(self) -> None:
        """A long-lived process recovers a model once the TTL elapses.

        This is the regression for the pre-TTL behavior where _exhausted_models
        was never cleared — after a daily-cap hit the model was skipped forever
        until process restart.
        """
        from app.config import settings
        from app.llm.providers.gemini import GeminiProvider, time as _time

        with patch.object(settings, "GEMINI_API_KEY", "test-key"):
            provider = GeminiProvider()
        provider._exhaustion_ttl_seconds = 3600.0  # 1h (override for the test)

        # Mark the preferred model exhausted at the current time.
        marked_at = _time.monotonic()
        provider._exhausted_at["gemini-3.6-flash"] = marked_at
        # Within the TTL the model stays skipped → fail over to flash-lite.
        assert provider._first_available("gemini-3.6-flash") == \
            "gemini-3.5-flash-lite"

        # After the TTL (midnight reset) the marker is pruned and the
        # preferred model is tried again — no restart required.
        # Simulate a full second PAST the TTL so float rounding at the exact
        # `now - ts == ttl` boundary can never flip the `>=` prune.
        with patch("app.llm.providers.gemini.time.monotonic",
                    return_value=marked_at + 3601.0):
            assert provider._first_available("gemini-3.6-flash") == \
                "gemini-3.6-flash"
        assert "gemini-3.6-flash" not in provider._exhausted_at
        assert provider._exhausted_models == set()

    def test_exhausted_marker_persists_within_ttl(self) -> None:
        """Fresh markers are NOT pruned — failover still happens mid-day."""
        from app.config import settings
        from app.llm.providers.gemini import GeminiProvider, time as _time

        with patch.object(settings, "GEMINI_API_KEY", "test-key"):
            provider = GeminiProvider()
        provider._exhaustion_ttl_seconds = 24 * 60 * 60
        marked_at = _time.monotonic()
        provider._exhausted_at["gemini-3.6-flash"] = marked_at

        with patch("app.llm.providers.gemini.time.monotonic",
                    return_value=marked_at + 3600.0):
            assert provider._first_available("gemini-3.6-flash") == \
                "gemini-3.5-flash-lite"
        assert "gemini-3.6-flash" in provider._exhausted_at

    @pytest.mark.asyncio
    async def test_chat_recovers_preferred_model_after_ttl(self) -> None:
        """End-to-end: after the TTL, the default model is used again."""
        from app.config import settings
        from app.llm.providers.gemini import GeminiProvider, time as _time

        with patch.object(settings, "GEMINI_API_KEY", "test-key"):
            provider = GeminiProvider()
        provider._exhaustion_ttl_seconds = 3600.0

        # Simulate a daily-cap hit earlier in the day: marker with an old
        # timestamp (not via _with_retry, which is the live path).
        marked_at = _time.monotonic()
        provider._exhausted_at["gemini-3.6-flash"] = marked_at

        # Midnight reset: _resolve_model prunes and picks the default again.
        # Simulate a full second PAST the TTL so float rounding at the exact
        # `now - ts == ttl` boundary can never flip the `>=` prune.
        with patch("app.llm.providers.gemini.time.monotonic",
                    return_value=marked_at + 3601.0):
            assert provider._resolve_model(LLMConfig()) == "gemini-3.6-flash"
        assert provider._exhausted_at == {}
        assert provider._exhausted_models == set()

    @pytest.mark.asyncio
    async def test_chat_stream_yields_chunks(self) -> None:
        from app.config import settings
        from app.llm.providers.gemini import GeminiProvider

        class _Chunk:
            text = "tok"

        fake_client = MagicMock()
        # generate_content_stream is an async-generator method in the SDK
        # (returns an async iterator, not a coroutine) — so we mock the
        # call to RETURN an async generator rather than await it.
        fake_client.aio.models.generate_content_stream = MagicMock(
            return_value=_async_gen([_Chunk(), _Chunk()]))

        with patch.object(settings, "GEMINI_API_KEY", "test-key"), patch(
            "app.llm.providers.gemini.genai.Client",
            return_value=fake_client,
        ):
            provider = GeminiProvider()
            chunks = [c async for c in provider.chat_stream(
                [LLMMessage(role="user", content="hi")])]
        assert chunks == ["tok", "tok"]


def _async_gen(items):
    async def _gen():
        for item in items:
            yield item
    return _gen()


class TestAnthropicProvider:
    """Anthropic provider — model sentinel regression (same latent bug as Gemini)."""

    def test_resolve_model_ignores_openai_sentinel(self) -> None:
        from app.llm.providers.anthropic import AnthropicProvider

        provider = AnthropicProvider.__new__(AnthropicProvider)  # no __init__
        assert provider._resolve_model(LLMConfig()) == "claude-sonnet-4-20250514"
        assert provider._resolve_model(
            LLMConfig(model="gpt-4o-mini")) == "claude-sonnet-4-20250514"
        assert provider._resolve_model(
            LLMConfig(model="claude-sonnet-4")) == "claude-sonnet-4"


class TestEmbeddingProviderConfig:
    """EMBEDDING_PROVIDER validator accepts fake/openai only."""

    def test_accepts_fake_and_openai(self) -> None:
        from app.config import Settings

        assert Settings(DEVPILOT_EMBEDDING_PROVIDER="fake").EMBEDDING_PROVIDER == "fake"
        assert Settings(
            DEVPILOT_EMBEDDING_PROVIDER="openai").EMBEDDING_PROVIDER == "openai"

    def test_rejects_anthropic(self) -> None:
        from pydantic import ValidationError

        from app.config import Settings

        with pytest.raises(ValidationError):
            Settings(DEVPILOT_EMBEDDING_PROVIDER="anthropic")
