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
        assert "openrouter" in factory._providers
        assert "nvidia" in factory._providers
        assert "cloudflare" in factory._providers
        assert "openai_compatible" in factory._providers
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
        # The factory forwards runtime registrations to the centralized
        # provider registry — undo that so later tests keep the canonical set.
        from app.llm import provider_registry as reg

        reg._PROVIDER_SPECS.pop("dummy", None)
        if "dummy" in reg._PROVIDER_ORDER:
            reg._PROVIDER_ORDER.remove("dummy")


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


class TestGeminiPaidTier:
    """Phase 20B B1 — DEVPILOT_GEMINI_TIER=paid (billing attached to the key).

    Paid tier disables the free-tier daily-quota machinery: no cross-model
    failover, no 24h exhaustion markers, no "wait for midnight" errors. A
    genuine quota/billing error surfaces immediately; transient per-minute
    429s are still retried.
    """

    def test_default_tier_is_free_and_paid_models_ignored(self) -> None:
        from app.config import settings
        from app.llm.providers.gemini import GeminiProvider

        with patch.object(settings, "GEMINI_API_KEY", "test-key"), \
             patch.object(settings, "GEMINI_PAID_MODELS",
                          ["gemini-3.6-pro-preview"]):
            provider = GeminiProvider()
        assert provider.tier == "free"
        assert provider.default_model == "gemini-3.6-flash"
        assert provider.model_candidates == (
            "gemini-3.6-flash", "gemini-3.5-flash-lite", "gemini-3.5-flash")

    def test_paid_tier_uses_configured_models(self) -> None:
        from app.config import settings
        from app.llm.providers.gemini import GeminiProvider

        with patch.object(settings, "GEMINI_API_KEY", "test-key"), \
             patch.object(settings, "GEMINI_TIER", "paid"), \
             patch.object(settings, "GEMINI_PAID_MODELS",
                          ["gemini-3.6-pro-preview", "gemini-3.6-flash"]):
            provider = GeminiProvider()
        assert provider.tier == "paid"
        assert provider.default_model == "gemini-3.6-pro-preview"
        assert provider.model_candidates == (
            "gemini-3.6-pro-preview", "gemini-3.6-flash")
        assert provider._resolve_model(LLMConfig()) == "gemini-3.6-pro-preview"
        # an explicit model is still honored
        assert provider._resolve_model(
            LLMConfig(model="gemini-3.6-flash")) == "gemini-3.6-flash"

    def test_paid_tier_ignores_exhaustion_markers(self) -> None:
        """Billing removes the daily per-model buckets — markers are ignored,
        so the preferred model is always returned (no failover)."""
        from app.config import settings
        from app.llm.providers.gemini import GeminiProvider, time as _time

        with patch.object(settings, "GEMINI_API_KEY", "test-key"), \
             patch.object(settings, "GEMINI_TIER", "paid"):
            provider = GeminiProvider()
        provider._exhausted_at["gemini-3.6-flash"] = _time.monotonic()
        assert provider._exhausted_models == {"gemini-3.6-flash"}
        assert provider._first_available("gemini-3.6-flash") == "gemini-3.6-flash"
        assert provider._resolve_model(LLMConfig()) == "gemini-3.6-flash"

    @pytest.mark.asyncio
    async def test_paid_tier_quota_error_fails_fast_without_failover(self) -> None:
        """A billing/quota error on a paid key is a REAL problem — raise it
        immediately, never mark the model exhausted, never switch models."""
        from app.config import settings
        from app.core.exceptions import LLMError
        from app.llm.providers.gemini import GeminiProvider

        class _Billing(Exception):
            code = 429

        fake_client = MagicMock()
        call = AsyncMock(side_effect=_Billing(
            "You exceeded your current quota, please check your plan and "
            "billing details."))
        fake_client.aio.models.generate_content = call

        with patch.object(settings, "GEMINI_API_KEY", "test-key"), \
             patch.object(settings, "GEMINI_TIER", "paid"), \
             patch("app.llm.providers.gemini.genai.Client",
                   return_value=fake_client), \
             patch("app.llm.providers.gemini.asyncio.sleep", new=AsyncMock()):
            provider = GeminiProvider()
            with pytest.raises(LLMError, match="paid-tier call failed"):
                await provider.chat([LLMMessage(role="user", content="hi")])

        # Exactly one probe of the default model, no exhaustion marker.
        assert call.await_count == 1
        used = [c.kwargs["model"] for c in call.await_args_list]
        assert used == ["gemini-3.6-flash"]
        assert provider._exhausted_at == {}

    @pytest.mark.asyncio
    async def test_paid_tier_still_retries_transient_rate_limits(self) -> None:
        """Only the daily-quota failover is disabled — a transient per-minute
        429 keeps its exponential-backoff retry in the paid tier too."""
        from app.config import settings
        from app.llm.providers.gemini import GeminiProvider

        class _PerMinute(Exception):
            code = 429
            retry_delay = None

        good_response = MagicMock()
        good_response.text = "ok"
        good_response.candidates = []
        good_response.usage_metadata = None

        fake_client = MagicMock()
        call = AsyncMock(side_effect=[
            _PerMinute("429 RESOURCE_EXHAUSTED: generate_content_free_tier_requests, "
                       "limit: 5/min"),
            good_response,
        ])
        fake_client.aio.models.generate_content = call

        with patch.object(settings, "GEMINI_API_KEY", "test-key"), \
             patch.object(settings, "GEMINI_TIER", "paid"), \
             patch("app.llm.providers.gemini.genai.Client",
                   return_value=fake_client), \
             patch("app.llm.providers.gemini.asyncio.sleep", new=AsyncMock()):
            provider = GeminiProvider()
            result = await provider.chat([LLMMessage(role="user", content="hi")])

        assert result.content == "ok"
        assert call.await_count == 2  # one retry after the 429, same model
        used = [c.kwargs["model"] for c in call.await_args_list]
        assert used == ["gemini-3.6-flash", "gemini-3.6-flash"]


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


class TestGeminiTierConfig:
    """DEVPILOT_GEMINI_TIER / DEVPILOT_GEMINI_PAID_MODELS parsing (Phase 20B B1)."""

    def test_tier_defaults_to_free(self) -> None:
        from app.config import Settings

        assert Settings().GEMINI_TIER == "free"

    def test_tier_accepts_paid_and_case_normalizes(self) -> None:
        from app.config import Settings

        assert Settings(DEVPILOT_GEMINI_TIER="paid").GEMINI_TIER == "paid"
        assert Settings(DEVPILOT_GEMINI_TIER="FREE").GEMINI_TIER == "free"
        assert Settings(DEVPILOT_GEMINI_TIER=" Paid ").GEMINI_TIER == "paid"

    def test_tier_rejects_unknown_value(self) -> None:
        from pydantic import ValidationError

        from app.config import Settings

        with pytest.raises(ValidationError):
            Settings(DEVPILOT_GEMINI_TIER="pro")

    def test_paid_models_parses_env_string(self) -> None:
        from app.config import Settings

        s = Settings(
            DEVPILOT_GEMINI_PAID_MODELS="gemini-3.6-pro-preview,gemini-3.6-flash")
        assert s.GEMINI_PAID_MODELS == [
            "gemini-3.6-pro-preview", "gemini-3.6-flash"]

    def test_paid_models_normalizes_and_dedupes(self) -> None:
        from app.config import Settings

        s = Settings(DEVPILOT_GEMINI_PAID_MODELS="Gemini-3.6-Pro-Preview,gemini-3.6-flash,gemini-3.6-pro-preview")
        assert s.GEMINI_PAID_MODELS == [
            "gemini-3.6-pro-preview", "gemini-3.6-flash"]

    def test_paid_models_empty_when_unset(self) -> None:
        from app.config import Settings

        assert Settings(DEVPILOT_GEMINI_PAID_MODELS="").GEMINI_PAID_MODELS == []
        assert Settings(DEVPILOT_GEMINI_PAID_MODELS=None).GEMINI_PAID_MODELS == []


class TestOpenRouterProvider:
    """OpenRouter provider — DEVPILOT_OPENROUTER_MODEL + sentinel handling."""

    def test_requires_api_key(self) -> None:
        from app.config import settings
        from app.core.exceptions import LLMConfigurationError
        from app.llm.providers.openrouter import OpenRouterProvider

        with patch.object(settings, "OPENROUTER_API_KEY", None):
            with pytest.raises(LLMConfigurationError):
                OpenRouterProvider()

    def test_default_model_uses_configured_openrouter_model(self) -> None:
        from app.config import settings
        from app.llm.providers.openrouter import OpenRouterProvider

        provider = OpenRouterProvider.__new__(OpenRouterProvider)
        with patch.object(settings, "OPENROUTER_MODEL",
                          "poolside/laguna-s-2.1:free"):
            assert provider.default_model == "poolside/laguna-s-2.1:free"

    def test_default_model_falls_back_to_auto_router(self) -> None:
        from app.config import settings
        from app.llm.providers.openrouter import OpenRouterProvider

        provider = OpenRouterProvider.__new__(OpenRouterProvider)
        with patch.object(settings, "OPENROUTER_MODEL", None):
            assert provider.default_model == "openrouter/auto"

    def test_resolve_model_ignores_openai_sentinel(self) -> None:
        from app.config import settings
        from app.llm.providers.openrouter import OpenRouterProvider

        provider = OpenRouterProvider.__new__(OpenRouterProvider)
        with patch.object(settings, "OPENROUTER_MODEL",
                          "poolside/laguna-s-2.1:free"):
            # LLMConfig() defaults to "gpt-4o-mini" — treated as "unset"
            assert provider._resolve_model(
                LLMConfig()) == "poolside/laguna-s-2.1:free"
            assert provider._resolve_model(
                LLMConfig(model="gpt-4o-mini")) == "poolside/laguna-s-2.1:free"
            # an explicit OpenRouter model is honored
            assert provider._resolve_model(
                LLMConfig(model="cohere/north-mini-code:free")
            ) == "cohere/north-mini-code:free"

    def test_openrouter_model_config_parses(self) -> None:
        from app.config import Settings

        s = Settings(DEVPILOT_OPENROUTER_MODEL="poolside/laguna-s-2.1:free")
        assert s.OPENROUTER_MODEL == "poolside/laguna-s-2.1:free"
        assert Settings(DEVPILOT_OPENROUTER_MODEL=None).OPENROUTER_MODEL is None

    @pytest.mark.asyncio
    async def test_chat_sends_resolved_model(self) -> None:
        from app.config import settings
        from app.llm.providers.openrouter import OpenRouterProvider

        fake_client = MagicMock()
        fake_choice = MagicMock()
        fake_choice.message.content = "hello from openrouter"
        fake_choice.finish_reason = "stop"
        fake_response = MagicMock()
        fake_response.choices = [fake_choice]
        fake_response.usage = None
        fake_client.chat.completions.create = AsyncMock(
            return_value=fake_response)

        with patch.object(settings, "OPENROUTER_API_KEY", "or-test-key"), patch.object(
            settings, "OPENROUTER_MODEL", "poolside/laguna-s-2.1:free"
        ), patch("app.llm.providers.openrouter.AsyncOpenAI",
                 return_value=fake_client):
            provider = OpenRouterProvider()
            result = await provider.chat(
                [LLMMessage(role="user", content="hi")],
                config=LLMConfig(temperature=0.1),
            )

        assert result.content == "hello from openrouter"
        call = fake_client.chat.completions.create.await_args
        assert call is not None
        kwargs = call.kwargs
        assert kwargs["model"] == "poolside/laguna-s-2.1:free"
        assert kwargs["temperature"] == 0.1
        assert kwargs["messages"] == [{"role": "user", "content": "hi"}]


class TestNvidiaProvider:
    """NVIDIA NIM provider — OpenAI-compatible endpoint, config, streaming."""

    def test_requires_api_key(self) -> None:
        from app.config import settings
        from app.core.exceptions import LLMConfigurationError
        from app.llm.providers.nvidia import NvidiaProvider

        with patch.object(settings, "NVIDIA_API_KEY", None):
            with pytest.raises(LLMConfigurationError):
                NvidiaProvider()

    def test_default_model_uses_configured_nvidia_model(self) -> None:
        from app.config import settings
        from app.llm.providers.nvidia import NvidiaProvider

        provider = NvidiaProvider.__new__(NvidiaProvider)
        with patch.object(settings, "NVIDIA_MODEL",
                          "deepseek-ai/deepseek-r1"):
            assert provider.default_model == "deepseek-ai/deepseek-r1"

    def test_default_model_falls_back_to_hosted_default(self) -> None:
        from app.config import settings
        from app.llm.providers.nvidia import NvidiaProvider

        provider = NvidiaProvider.__new__(NvidiaProvider)
        with patch.object(settings, "NVIDIA_MODEL", None):
            assert provider.default_model == "meta/llama-3.1-8b-instruct"

    def test_resolve_model_ignores_openai_sentinel(self) -> None:
        from app.config import settings
        from app.llm.providers.nvidia import NvidiaProvider

        provider = NvidiaProvider.__new__(NvidiaProvider)
        with patch.object(settings, "NVIDIA_MODEL",
                          "meta/llama-3.1-8b-instruct"):
            # LLMConfig() defaults to "gpt-4o-mini" — treated as "unset"
            assert provider._resolve_model(
                LLMConfig()) == "meta/llama-3.1-8b-instruct"
            assert provider._resolve_model(
                LLMConfig(model="gpt-4o-mini")) == "meta/llama-3.1-8b-instruct"
            # an explicit NIM model is honored
            assert provider._resolve_model(
                LLMConfig(model="nvidia/llama-3.3-nemotron-super-49b-v1")
            ) == "nvidia/llama-3.3-nemotron-super-49b-v1"

    def test_client_gets_timeout_and_retries(self) -> None:
        from app.config import settings
        from app.llm.providers.nvidia import NvidiaProvider

        fake_client = MagicMock()
        with patch.object(settings, "NVIDIA_API_KEY", "nv-test-key"), patch(
            "app.llm.providers.nvidia.AsyncOpenAI"
        ) as mock_client_cls:
            mock_client_cls.return_value = fake_client
            NvidiaProvider()
        call = mock_client_cls.call_args  # AsyncOpenAI(...)
        assert call is not None
        kwargs = call.kwargs
        assert kwargs["api_key"] == "nv-test-key"
        assert kwargs["base_url"] == "https://integrate.api.nvidia.com/v1"
        assert kwargs["timeout"] == 300.0
        assert kwargs["max_retries"] == 2

    def test_nvidia_config_parses(self) -> None:
        from app.config import Settings

        s = Settings(
            DEVPILOT_NVIDIA_MODEL="nvidia/llama-3.3-70b-instruct",
            DEVPILOT_NVIDIA_TIMEOUT_SECONDS="45",
            DEVPILOT_NVIDIA_MAX_RETRIES="3",
        )
        assert s.NVIDIA_MODEL == "nvidia/llama-3.3-70b-instruct"
        assert s.NVIDIA_TIMEOUT_SECONDS == 45.0
        assert s.NVIDIA_MAX_RETRIES == 3
        assert Settings(DEVPILOT_NVIDIA_MODEL=None).NVIDIA_MODEL is None
        assert s.NVIDIA_BASE_URL == "https://integrate.api.nvidia.com/v1"

    @pytest.mark.asyncio
    async def test_chat_sends_resolved_model(self) -> None:
        from app.config import settings
        from app.llm.providers.nvidia import NvidiaProvider

        fake_client = MagicMock()
        fake_choice = MagicMock()
        fake_choice.message.content = "hello from nim"
        fake_choice.finish_reason = "stop"
        fake_response = MagicMock()
        fake_response.choices = [fake_choice]
        fake_response.usage = None
        fake_client.chat.completions.create = AsyncMock(
            return_value=fake_response)

        with patch.object(settings, "NVIDIA_API_KEY", "nv-test-key"), patch.object(
            settings, "NVIDIA_MODEL", "nvidia/llama-3.3-70b-instruct"
        ), patch("app.llm.providers.nvidia.AsyncOpenAI",
                 return_value=fake_client):
            provider = NvidiaProvider()
            result = await provider.chat(
                [LLMMessage(role="user", content="hi")],
                config=LLMConfig(temperature=0.1),
            )

        assert result.content == "hello from nim"
        call = fake_client.chat.completions.create.await_args
        assert call is not None
        kwargs = call.kwargs
        assert kwargs["model"] == "nvidia/llama-3.3-70b-instruct"
        assert kwargs["temperature"] == 0.1
        assert kwargs["messages"] == [{"role": "user", "content": "hi"}]

    @pytest.mark.asyncio
    async def test_chat_wraps_errors(self) -> None:
        from app.config import settings
        from app.core.exceptions import LLMError
        from app.llm.providers.nvidia import NvidiaProvider

        fake_client = MagicMock()
        fake_client.chat.completions.create = AsyncMock(
            side_effect=RuntimeError("boom"))

        with patch.object(settings, "NVIDIA_API_KEY", "nv-test-key"), patch(
            "app.llm.providers.nvidia.AsyncOpenAI", return_value=fake_client
        ):
            provider = NvidiaProvider()
            with pytest.raises(LLMError, match="NVIDIA chat call failed"):
                await provider.chat([LLMMessage(role="user", content="hi")])

    @pytest.mark.asyncio
    async def test_chat_stream_yields_deltas(self) -> None:
        from app.config import settings
        from app.llm.providers.nvidia import NvidiaProvider

        async def _stream():
            for text in ("hello", " ", "nim"):
                chunk = MagicMock()
                delta = MagicMock()
                delta.content = text
                chunk.choices = [MagicMock(delta=delta)]
                yield chunk

        fake_client = MagicMock()
        fake_client.chat.completions.create = AsyncMock(
            return_value=_stream())

        with patch.object(settings, "NVIDIA_API_KEY", "nv-test-key"), patch(
            "app.llm.providers.nvidia.AsyncOpenAI", return_value=fake_client
        ):
            provider = NvidiaProvider()
            parts = [c async for c in provider.chat_stream(
                [LLMMessage(role="user", content="hi")])]

        assert parts == ["hello", " ", "nim"]
        call = fake_client.chat.completions.create.await_args
        assert call is not None
        assert call.kwargs["stream"] is True

    @pytest.mark.asyncio
    async def test_chat_stream_wraps_errors(self) -> None:
        from app.config import settings
        from app.core.exceptions import LLMError
        from app.llm.providers.nvidia import NvidiaProvider

        fake_client = MagicMock()
        fake_client.chat.completions.create = AsyncMock(
            side_effect=RuntimeError("stream boom"))

        with patch.object(settings, "NVIDIA_API_KEY", "nv-test-key"), patch(
            "app.llm.providers.nvidia.AsyncOpenAI", return_value=fake_client
        ):
            provider = NvidiaProvider()
            with pytest.raises(LLMError, match="NVIDIA stream call failed"):
                async for _ in provider.chat_stream(
                        [LLMMessage(role="user", content="hi")]):
                    pass


class TestCloudflareProvider:
    """Cloudflare Workers AI provider — OpenAI-compatible endpoint, config."""

    def test_requires_api_key(self) -> None:
        from app.config import settings
        from app.core.exceptions import LLMConfigurationError
        from app.llm.providers.cloudflare import CloudflareProvider

        with patch.object(settings, "CLOUDFLARE_API_KEY", None):
            with pytest.raises(LLMConfigurationError):
                CloudflareProvider()

    def test_default_model_uses_configured_model(self) -> None:
        from app.config import settings
        from app.llm.providers.cloudflare import CloudflareProvider

        provider = CloudflareProvider.__new__(CloudflareProvider)
        with patch.object(settings, "CLOUDFLARE_MODEL", "@cf/meta/llama-3.1-8b-instruct"):
            assert provider.default_model == "@cf/meta/llama-3.1-8b-instruct"

    def test_default_model_falls_back(self) -> None:
        from app.config import settings
        from app.llm.providers.cloudflare import CloudflareProvider

        provider = CloudflareProvider.__new__(CloudflareProvider)
        with patch.object(settings, "CLOUDFLARE_MODEL", None):
            assert provider.default_model == "@cf/meta/llama-4-scout-17b-16e-instruct"

    def test_resolve_model_ignores_openai_sentinel(self) -> None:
        from app.config import settings
        from app.llm.providers.cloudflare import CloudflareProvider

        provider = CloudflareProvider.__new__(CloudflareProvider)
        with patch.object(settings, "CLOUDFLARE_MODEL", "@cf/meta/llama-3.1-8b-instruct"):
            assert provider._resolve_model(
                LLMConfig()) == "@cf/meta/llama-3.1-8b-instruct"
            assert provider._resolve_model(
                LLMConfig(model="gpt-4o-mini")) == "@cf/meta/llama-3.1-8b-instruct"
            assert provider._resolve_model(
                LLMConfig(model="@cf/meta/llama-3.3-70b-instruct-fp8-fast")
            ) == "@cf/meta/llama-3.3-70b-instruct-fp8-fast"

    def test_client_gets_timeout_and_retries(self) -> None:
        from app.config import settings
        from app.llm.providers.cloudflare import CloudflareProvider

        fake_client = MagicMock()
        with patch.object(settings, "CLOUDFLARE_API_KEY", "cf-test-key"), patch.object(
            settings, "CLOUDFLARE_ACCOUNT_ID", "acc-123"
        ), patch("app.llm.providers.cloudflare.AsyncOpenAI"
                 ) as mock_client_cls:
            mock_client_cls.return_value = fake_client
            CloudflareProvider()
        call = mock_client_cls.call_args  # AsyncOpenAI(...)
        assert call is not None
        kwargs = call.kwargs
        assert kwargs["api_key"] == "cf-test-key"
        assert kwargs["base_url"] == (
            "https://api.cloudflare.com/client/v4/accounts/acc-123/ai/v1"
        )
        assert kwargs["timeout"] == 60.0
        assert kwargs["max_retries"] == 2

    def test_client_builds_url_from_account_id(self) -> None:
        from app.config import settings
        from app.llm.providers.cloudflare import CloudflareProvider

        with patch.object(settings, "CLOUDFLARE_ACCOUNT_ID", "acc-123"):
            assert CloudflareProvider._resolve_base_url() == (
                "https://api.cloudflare.com/client/v4/accounts/acc-123/ai/v1"
            )

    def test_client_uses_explicit_base_url(self) -> None:
        from app.config import settings
        from app.llm.providers.cloudflare import CloudflareProvider

        with patch.object(
            settings, "CLOUDFLARE_BASE_URL", "https://example.com/v1"
        ), patch.object(settings, "CLOUDFLARE_ACCOUNT_ID", None):
            assert CloudflareProvider._resolve_base_url() == "https://example.com/v1"

    def test_client_requires_account_id_without_base_url(self) -> None:
        from app.config import settings
        from app.core.exceptions import LLMConfigurationError
        from app.llm.providers.cloudflare import CloudflareProvider

        with patch.object(settings, "CLOUDFLARE_BASE_URL", None), patch.object(
            settings, "CLOUDFLARE_ACCOUNT_ID", None
        ):
            with pytest.raises(LLMConfigurationError, match="CLOUDFLARE_ACCOUNT_ID"):
                CloudflareProvider._resolve_base_url()

    def test_cloudflare_config_parses(self) -> None:
        from app.config import Settings

        s = Settings(
            DEVPILOT_CLOUDFLARE_MODEL="@cf/meta/llama-3.1-8b-instruct",
            DEVPILOT_CLOUDFLARE_TIMEOUT_SECONDS="90",
            DEVPILOT_CLOUDFLARE_MAX_RETRIES="3",
        )
        assert s.CLOUDFLARE_MODEL == "@cf/meta/llama-3.1-8b-instruct"
        assert s.CLOUDFLARE_TIMEOUT_SECONDS == 90.0
        assert s.CLOUDFLARE_MAX_RETRIES == 3
        assert Settings(DEVPILOT_CLOUDFLARE_MODEL=None).CLOUDFLARE_MODEL is None

    @pytest.mark.asyncio
    async def test_chat_sends_resolved_model(self) -> None:
        from app.config import settings
        from app.llm.providers.cloudflare import CloudflareProvider

        fake_client = MagicMock()
        fake_choice = MagicMock()
        fake_choice.message.content = "hello from workers ai"
        fake_choice.finish_reason = "stop"
        fake_response = MagicMock()
        fake_response.choices = [fake_choice]
        fake_response.usage = None
        fake_client.chat.completions.create = AsyncMock(
            return_value=fake_response)

        with patch.object(settings, "CLOUDFLARE_API_KEY", "cf-test-key"), patch.object(
            settings, "CLOUDFLARE_ACCOUNT_ID", "acc-123"
        ), patch.object(
            settings, "CLOUDFLARE_MODEL", "@cf/meta/llama-3.1-8b-instruct"
        ), patch("app.llm.providers.cloudflare.AsyncOpenAI",
                return_value=fake_client):
            provider = CloudflareProvider()
            result = await provider.chat(
                [LLMMessage(role="user", content="hi")],
                config=LLMConfig(temperature=0.1),
            )

        assert result.content == "hello from workers ai"
        call = fake_client.chat.completions.create.await_args
        assert call is not None
        kwargs = call.kwargs
        assert kwargs["model"] == "@cf/meta/llama-3.1-8b-instruct"
        assert kwargs["temperature"] == 0.1
        assert kwargs["messages"] == [{"role": "user", "content": "hi"}]

    @pytest.mark.asyncio
    async def test_chat_wraps_errors(self) -> None:
        from app.config import settings
        from app.core.exceptions import LLMError
        from app.llm.providers.cloudflare import CloudflareProvider

        fake_client = MagicMock()
        fake_client.chat.completions.create = AsyncMock(
            side_effect=RuntimeError("boom"))

        with patch.object(settings, "CLOUDFLARE_API_KEY", "cf-test-key"), patch.object(
            settings, "CLOUDFLARE_ACCOUNT_ID", "acc-123"
        ), patch(
            "app.llm.providers.cloudflare.AsyncOpenAI", return_value=fake_client
        ):
            provider = CloudflareProvider()
            with pytest.raises(LLMError, match="Cloudflare chat call failed"):
                await provider.chat([LLMMessage(role="user", content="hi")])

    @pytest.mark.asyncio
    async def test_chat_stream_yields_deltas(self) -> None:
        from app.config import settings
        from app.llm.providers.cloudflare import CloudflareProvider

        async def _stream():
            for text in ("hello", " ", "cf"):
                chunk = MagicMock()
                delta = MagicMock()
                delta.content = text
                chunk.choices = [MagicMock(delta=delta)]
                yield chunk

        fake_client = MagicMock()
        fake_client.chat.completions.create = AsyncMock(
            return_value=_stream())

        with patch.object(settings, "CLOUDFLARE_API_KEY", "cf-test-key"), patch.object(
            settings, "CLOUDFLARE_ACCOUNT_ID", "acc-123"
        ), patch(
            "app.llm.providers.cloudflare.AsyncOpenAI", return_value=fake_client
        ):
            provider = CloudflareProvider()
            parts = [c async for c in provider.chat_stream(
                [LLMMessage(role="user", content="hi")])]

        assert parts == ["hello", " ", "cf"]
        call = fake_client.chat.completions.create.await_args
        assert call is not None
        assert call.kwargs["stream"] is True

    @pytest.mark.asyncio
    async def test_chat_stream_skips_non_string_deltas(self) -> None:
        from app.config import settings
        from app.llm.providers.cloudflare import CloudflareProvider

        async def _stream():
            for text in ("provider", "-", 2, "ok", 4):
                chunk = MagicMock()
                delta = MagicMock()
                delta.content = text
                chunk.choices = [MagicMock(delta=delta)]
                yield chunk

        fake_client = MagicMock()
        fake_client.chat.completions.create = AsyncMock(
            return_value=_stream())

        with patch.object(settings, "CLOUDFLARE_API_KEY", "cf-test-key"), patch.object(
            settings, "CLOUDFLARE_ACCOUNT_ID", "acc-123"
        ), patch(
            "app.llm.providers.cloudflare.AsyncOpenAI", return_value=fake_client
        ):
            provider = CloudflareProvider()
            parts = [c async for c in provider.chat_stream(
                [LLMMessage(role="user", content="hi")])]

        assert parts == ["provider", "-", "ok"]


class TestOpenAICompatibleProvider:
    """Generic OpenAI-compatible endpoint provider — config + routing."""

    def test_requires_base_url(self) -> None:
        from app.config import settings
        from app.core.exceptions import LLMConfigurationError
        from app.llm.providers.openai_compatible import OpenAICompatibleProvider

        with patch.object(settings, "OPENAI_COMPATIBLE_BASE_URL", None):
            with pytest.raises(LLMConfigurationError):
                OpenAICompatibleProvider()

    def test_default_model_uses_configured_model(self) -> None:
        from app.config import settings
        from app.llm.providers.openai_compatible import OpenAICompatibleProvider

        provider = OpenAICompatibleProvider.__new__(OpenAICompatibleProvider)
        with patch.object(settings, "OPENAI_COMPATIBLE_MODEL",
                          "meta-llama/Meta-Llama-3.1-8B-Instruct"):
            assert provider.default_model == "meta-llama/Meta-Llama-3.1-8B-Instruct"

    def test_default_model_falls_back(self) -> None:
        from app.config import settings
        from app.llm.providers.openai_compatible import OpenAICompatibleProvider

        provider = OpenAICompatibleProvider.__new__(OpenAICompatibleProvider)
        with patch.object(settings, "OPENAI_COMPATIBLE_MODEL", None):
            assert provider.default_model == LLMConfig().model

    def test_resolve_model_ignores_openai_sentinel(self) -> None:
        from app.config import settings
        from app.llm.providers.openai_compatible import OpenAICompatibleProvider

        provider = OpenAICompatibleProvider.__new__(OpenAICompatibleProvider)
        with patch.object(settings, "OPENAI_COMPATIBLE_MODEL",
                          "meta-llama/Meta-Llama-3.1-8B-Instruct"):
            assert provider._resolve_model(
                LLMConfig()) == "meta-llama/Meta-Llama-3.1-8B-Instruct"
            assert provider._resolve_model(
                LLMConfig(model="gpt-4o-mini")) == "meta-llama/Meta-Llama-3.1-8B-Instruct"
            assert provider._resolve_model(
                LLMConfig(model="phi-3-mini")) == "phi-3-mini"

    def test_client_gets_timeout_retries_and_placeholder_key(self) -> None:
        from app.config import settings
        from app.llm.providers.openai_compatible import OpenAICompatibleProvider

        fake_client = MagicMock()
        with patch.object(settings, "OPENAI_COMPATIBLE_BASE_URL",
                          "http://localhost:8000/v1"), patch.object(
            settings, "OPENAI_COMPATIBLE_API_KEY", None
        ), patch("app.llm.providers.openai_compatible.AsyncOpenAI"
                 ) as mock_client_cls:
            mock_client_cls.return_value = fake_client
            OpenAICompatibleProvider()
        call = mock_client_cls.call_args
        assert call is not None
        kwargs = call.kwargs
        assert kwargs["api_key"] == "openai-compatible"
        assert kwargs["base_url"] == "http://localhost:8000/v1"
        assert kwargs["timeout"] == 60.0
        assert kwargs["max_retries"] == 2

    def test_client_uses_configured_key(self) -> None:
        from app.config import settings
        from app.llm.providers.openai_compatible import OpenAICompatibleProvider

        fake_client = MagicMock()
        with patch.object(settings, "OPENAI_COMPATIBLE_BASE_URL",
                          "http://localhost:8000/v1"), patch.object(
            settings, "OPENAI_COMPATIBLE_API_KEY", "secret-key"
        ), patch("app.llm.providers.openai_compatible.AsyncOpenAI"
                 ) as mock_client_cls:
            mock_client_cls.return_value = fake_client
            OpenAICompatibleProvider()
        call = mock_client_cls.call_args
        assert call is not None
        assert call.kwargs["api_key"] == "secret-key"

    def test_openai_compatible_config_parses(self) -> None:
        from app.config import Settings

        s = Settings(
            DEVPILOT_OPENAI_COMPATIBLE_MODEL="phi-3-mini",
            DEVPILOT_OPENAI_COMPATIBLE_TIMEOUT_SECONDS="45",
            DEVPILOT_OPENAI_COMPATIBLE_MAX_RETRIES="1",
        )
        assert s.OPENAI_COMPATIBLE_MODEL == "phi-3-mini"
        assert s.OPENAI_COMPATIBLE_TIMEOUT_SECONDS == 45.0
        assert s.OPENAI_COMPATIBLE_MAX_RETRIES == 1
        assert Settings(DEVPILOT_OPENAI_COMPATIBLE_MODEL=None).OPENAI_COMPATIBLE_MODEL is None

    @pytest.mark.asyncio
    async def test_chat_sends_resolved_model(self) -> None:
        from app.config import settings
        from app.llm.providers.openai_compatible import OpenAICompatibleProvider

        fake_client = MagicMock()
        fake_choice = MagicMock()
        fake_choice.message.content = "hello from vllm"
        fake_choice.finish_reason = "stop"
        fake_response = MagicMock()
        fake_response.choices = [fake_choice]
        fake_response.usage = None
        fake_client.chat.completions.create = AsyncMock(
            return_value=fake_response)

        with patch.object(settings, "OPENAI_COMPATIBLE_BASE_URL",
                          "http://localhost:8000/v1"), patch.object(
            settings, "OPENAI_COMPATIBLE_MODEL", "phi-3-mini"
        ), patch("app.llm.providers.openai_compatible.AsyncOpenAI",
                 return_value=fake_client):
            provider = OpenAICompatibleProvider()
            result = await provider.chat(
                [LLMMessage(role="user", content="hi")],
                config=LLMConfig(temperature=0.1),
            )

        assert result.content == "hello from vllm"
        call = fake_client.chat.completions.create.await_args
        assert call is not None
        kwargs = call.kwargs
        assert kwargs["model"] == "phi-3-mini"
        assert kwargs["temperature"] == 0.1
        assert kwargs["messages"] == [{"role": "user", "content": "hi"}]

    @pytest.mark.asyncio
    async def test_chat_wraps_errors(self) -> None:
        from app.config import settings
        from app.core.exceptions import LLMError
        from app.llm.providers.openai_compatible import OpenAICompatibleProvider

        fake_client = MagicMock()
        fake_client.chat.completions.create = AsyncMock(
            side_effect=RuntimeError("boom"))

        with patch.object(settings, "OPENAI_COMPATIBLE_BASE_URL",
                          "http://localhost:8000/v1"), patch(
            "app.llm.providers.openai_compatible.AsyncOpenAI",
            return_value=fake_client,
        ):
            provider = OpenAICompatibleProvider()
            with pytest.raises(LLMError, match="OpenAI-compatible chat call failed"):
                await provider.chat([LLMMessage(role="user", content="hi")])


class TestOllamaCloudProvider:
    """Ollama Cloud provider — OpenAI-compatible remote inference, config."""

    def test_requires_api_key(self) -> None:
        from app.config import settings
        from app.core.exceptions import LLMConfigurationError
        from app.llm.providers.ollama_cloud import OllamaCloudProvider

        with patch.object(settings, "OLLAMA_CLOUD_API_KEY", None):
            with pytest.raises(LLMConfigurationError):
                OllamaCloudProvider()

    def test_default_model_uses_configured_model(self) -> None:
        from app.config import settings
        from app.llm.providers.ollama_cloud import OllamaCloudProvider

        provider = OllamaCloudProvider.__new__(OllamaCloudProvider)
        with patch.object(settings, "OLLAMA_CLOUD_MODEL", "deepseek-v4-flash:preview"):
            assert provider.default_model == "deepseek-v4-flash:preview"

    def test_default_model_falls_back(self) -> None:
        from app.config import settings
        from app.llm.providers.ollama_cloud import OllamaCloudProvider

        provider = OllamaCloudProvider.__new__(OllamaCloudProvider)
        with patch.object(settings, "OLLAMA_CLOUD_MODEL", None):
            assert provider.default_model == "gemma4:31b"

    def test_resolve_model_ignores_openai_sentinel(self) -> None:
        from app.config import settings
        from app.llm.providers.ollama_cloud import OllamaCloudProvider

        provider = OllamaCloudProvider.__new__(OllamaCloudProvider)
        with patch.object(settings, "OLLAMA_CLOUD_MODEL", "gpt-oss:20b"):
            assert provider._resolve_model(LLMConfig()) == "gpt-oss:20b"
            assert provider._resolve_model(
                LLMConfig(model="gpt-4o-mini")) == "gpt-oss:20b"
            assert provider._resolve_model(
                LLMConfig(model="gpt-oss:120b")) == "gpt-oss:120b"

    def test_client_uses_default_base_url_when_unset(self) -> None:
        from app.config import settings
        from app.llm.providers.ollama_cloud import OllamaCloudProvider

        fake_client = MagicMock()
        with patch.object(settings, "OLLAMA_CLOUD_API_KEY", "oc-test"), patch.object(
            settings, "OLLAMA_CLOUD_BASE_URL", None
        ), patch("app.llm.providers.ollama_cloud.AsyncOpenAI",
                 return_value=fake_client) as mock_client_cls:
            OllamaCloudProvider()
        call = mock_client_cls.call_args
        assert call is not None
        assert call.kwargs["base_url"] == "https://ollama.com/v1"
        assert call.kwargs["api_key"] == "oc-test"

    def test_ollama_cloud_config_parses(self) -> None:
        from app.config import Settings

        s = Settings(
            DEVPILOT_OLLAMA_CLOUD_MODEL="gpt-oss:120b",
            DEVPILOT_OLLAMA_CLOUD_TIMEOUT_SECONDS="45",
            DEVPILOT_OLLAMA_CLOUD_MAX_RETRIES="1",
        )
        assert s.OLLAMA_CLOUD_MODEL == "gpt-oss:120b"
        assert s.OLLAMA_CLOUD_TIMEOUT_SECONDS == 45.0
        assert s.OLLAMA_CLOUD_MAX_RETRIES == 1
        assert Settings(DEVPILOT_OLLAMA_CLOUD_MODEL=None).OLLAMA_CLOUD_MODEL is None

    @pytest.mark.asyncio
    async def test_chat_sends_resolved_model(self) -> None:
        from app.config import settings
        from app.llm.providers.ollama_cloud import OllamaCloudProvider

        fake_client = MagicMock()
        fake_choice = MagicMock()
        fake_choice.message.content = "hello from ollama cloud"
        fake_choice.finish_reason = "stop"
        fake_response = MagicMock()
        fake_response.choices = [fake_choice]
        fake_response.usage = None
        fake_client.chat.completions.create = AsyncMock(return_value=fake_response)

        with patch.object(settings, "OLLAMA_CLOUD_API_KEY", "oc-test"), patch.object(
            settings, "OLLAMA_CLOUD_MODEL", "gpt-oss:20b"
        ), patch("app.llm.providers.ollama_cloud.AsyncOpenAI",
                 return_value=fake_client):
            provider = OllamaCloudProvider()
            result = await provider.chat(
                [LLMMessage(role="user", content="hi")],
                config=LLMConfig(temperature=0.1),
            )

        assert result.content == "hello from ollama cloud"
        call = fake_client.chat.completions.create.await_args
        assert call is not None
        kwargs = call.kwargs
        assert kwargs["model"] == "gpt-oss:20b"
        assert kwargs["temperature"] == 0.1
        assert kwargs["messages"] == [{"role": "user", "content": "hi"}]


class TestOpencodeZenProvider:
    """OpenCode Zen gateway provider — OpenAI-compatible, config."""

    def test_requires_api_key(self) -> None:
        from app.config import settings
        from app.core.exceptions import LLMConfigurationError
        from app.llm.providers.opencode_zen import OpencodeZenProvider

        with patch.object(settings, "OPENCODE_ZEN_API_KEY", None):
            with pytest.raises(LLMConfigurationError):
                OpencodeZenProvider()

    def test_default_model_uses_configured_model(self) -> None:
        from app.config import settings
        from app.llm.providers.opencode_zen import OpencodeZenProvider

        provider = OpencodeZenProvider.__new__(OpencodeZenProvider)
        with patch.object(settings, "OPENCODE_ZEN_MODEL", "claude-sonnet-4-5"):
            assert provider.default_model == "claude-sonnet-4-5"

    def test_default_model_falls_back(self) -> None:
        from app.config import settings
        from app.llm.providers.opencode_zen import OpencodeZenProvider

        provider = OpencodeZenProvider.__new__(OpencodeZenProvider)
        with patch.object(settings, "OPENCODE_ZEN_MODEL", None):
            assert provider.default_model == "deepseek-v4-flash-free"

    def test_resolve_model_ignores_openai_sentinel(self) -> None:
        from app.config import settings
        from app.llm.providers.opencode_zen import OpencodeZenProvider

        provider = OpencodeZenProvider.__new__(OpencodeZenProvider)
        with patch.object(settings, "OPENCODE_ZEN_MODEL", "deepseek-v4-flash-free"):
            assert provider._resolve_model(LLMConfig()) == "deepseek-v4-flash-free"
            assert provider._resolve_model(
                LLMConfig(model="gpt-4o-mini")) == "deepseek-v4-flash-free"
            assert provider._resolve_model(
                LLMConfig(model="gpt-5.2")) == "gpt-5.2"

    def test_client_uses_default_base_url_when_unset(self) -> None:
        from app.config import settings
        from app.llm.providers.opencode_zen import OpencodeZenProvider

        fake_client = MagicMock()
        with patch.object(settings, "OPENCODE_ZEN_API_KEY", "oz-test"), patch.object(
            settings, "OPENCODE_ZEN_BASE_URL", None
        ), patch("app.llm.providers.opencode_zen.AsyncOpenAI",
                 return_value=fake_client) as mock_client_cls:
            OpencodeZenProvider()
        call = mock_client_cls.call_args
        assert call is not None
        assert call.kwargs["base_url"] == "https://opencode.ai/zen/v1"
        assert call.kwargs["api_key"] == "oz-test"

    def test_opencode_zen_config_parses(self) -> None:
        from app.config import Settings

        s = Settings(
            DEVPILOT_OPENCODE_ZEN_MODEL="gpt-5.2",
            DEVPILOT_OPENCODE_ZEN_TIMEOUT_SECONDS="45",
            DEVPILOT_OPENCODE_ZEN_MAX_RETRIES="1",
        )
        assert s.OPENCODE_ZEN_MODEL == "gpt-5.2"
        assert s.OPENCODE_ZEN_TIMEOUT_SECONDS == 45.0
        assert s.OPENCODE_ZEN_MAX_RETRIES == 1
        assert Settings(DEVPILOT_OPENCODE_ZEN_MODEL=None).OPENCODE_ZEN_MODEL is None

    @pytest.mark.asyncio
    async def test_chat_sends_resolved_model(self) -> None:
        from app.config import settings
        from app.llm.providers.opencode_zen import OpencodeZenProvider

        fake_client = MagicMock()
        fake_choice = MagicMock()
        fake_choice.message.content = "hello from zen"
        fake_choice.finish_reason = "stop"
        fake_response = MagicMock()
        fake_response.choices = [fake_choice]
        fake_response.usage = None
        fake_client.chat.completions.create = AsyncMock(return_value=fake_response)

        with patch.object(settings, "OPENCODE_ZEN_API_KEY", "oz-test"), patch.object(
            settings, "OPENCODE_ZEN_MODEL", "deepseek-v4-flash-free"
        ), patch("app.llm.providers.opencode_zen.AsyncOpenAI",
                 return_value=fake_client):
            provider = OpencodeZenProvider()
            result = await provider.chat(
                [LLMMessage(role="user", content="hi")],
                config=LLMConfig(temperature=0.1),
            )

        assert result.content == "hello from zen"
        call = fake_client.chat.completions.create.await_args
        assert call is not None
        kwargs = call.kwargs
        assert kwargs["model"] == "deepseek-v4-flash-free"
        assert kwargs["temperature"] == 0.1
        assert kwargs["messages"] == [{"role": "user", "content": "hi"}]
