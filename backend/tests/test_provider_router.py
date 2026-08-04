"""Tests for Phase 19B — multi-provider router (failover, retries, circuit
breaker, health, metrics, redaction) and the provider API surface.

All tests are deterministic and never call a paid LLM.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Callable, Dict, List
from unittest.mock import patch

import pytest

from app.core.exceptions import (
    AllProvidersFailedError,
    LLMError,
    ProviderNotAvailableError,
)
from app.llm.base import BaseLLMProvider, LLMConfig, LLMMessage, LLMResponse
from app.llm.redaction import redact_dict, redact_secret
from app.llm.router import (
    CircuitBreaker,
    CircuitState,
    FailureKind,
    ProviderHealth,
    ProviderRouter,
    RoutedProvider,
    RetryStrategy,
    classify_failure,
)


def _msg(content: str = "hello") -> List[LLMMessage]:
    return [LLMMessage(role="user", content=content)]


class _StubProvider(BaseLLMProvider):
    """Configurable stub: handler may return a response or raise."""

    def __init__(
        self,
        name: str,
        handler: Optional[Callable] = None,
        default_model: Optional[str] = None,
    ) -> None:
        self._name = name
        self._handler = handler
        self._model = default_model or f"{name}-model"
        self.calls = 0

    @property
    def provider_name(self) -> str:
        return self._name

    @property
    def default_model(self) -> str:
        return self._model

    async def chat(self, messages, config=None):
        self.calls += 1
        if self._handler is None:
            return LLMResponse(content=f"reply-{self._name}")
        return await self._handler(messages, config)

    async def chat_stream(self, messages, config=None):
        if self._handler is None:
            yield f"chunk-{self._name}"
            return
        async for chunk in self._handler(messages, config):
            yield chunk


class _StubFactory:
    def __init__(self, providers: Dict[str, BaseLLMProvider]) -> None:
        self.providers = providers

    def get_provider(self, name: str) -> BaseLLMProvider:
        if name not in self.providers:
            raise KeyError(f"no provider {name}")
        return self.providers[name]


def _make_settings(**overrides) -> SimpleNamespace:
    defaults = dict(
        PROVIDER_ROUTING_ENABLED=True,
        LLM_PROVIDER="gemini",
        LLM_MODEL="gpt-4o-mini",
        PROVIDER_PRIORITY=[],
        PROVIDER_TIMEOUT_SECONDS=10,
        PROVIDER_RETRY_MAX=2,
        PROVIDER_RETRY_BASE_BACKOFF_SECONDS=0.5,
        PROVIDER_RETRY_MAX_BACKOFF_SECONDS=10.0,
        PROVIDER_CIRCUIT_BREAKER_FAILURE_THRESHOLD=3,
        PROVIDER_CIRCUIT_BREAKER_COOLDOWN_SECONDS=30.0,
        PROVIDER_CIRCUIT_BREAKER_HALF_OPEN_MAX_CALLS=2,
        PROVIDER_HEALTH_WINDOW=100,
        PROVIDER_HEALTH_DEGRADED_SUCCESS_RATE=0.5,
        PROVIDER_HEALTH_UNHEALTHY_SUCCESS_RATE=0.3,
        PROVIDER_METRICS_PERSIST=True,
        OPENAI_API_KEY="sk-test",
        ANTHROPIC_API_KEY="ak-test",
        GEMINI_API_KEY="gk-test",
        OPENROUTER_API_KEY="or-test",
        OLLAMA_BASE_URL="http://localhost:11434",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class _Clock:
    """Injectable monotonic clock."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


class TestClassifyFailure:
    def test_quota_before_rate_limit(self) -> None:
        class _E(Exception):
            code = 429

        # A daily-cap 429 is permanent quota, never a retryable rate limit.
        assert classify_failure(
            _E("You exceeded your current quota, please check your plan and billing")
        ) is FailureKind.QUOTA
        # The per-minute free-tier message remains retryable.
        assert classify_failure(
            _E("429 RESOURCE_EXHAUSTED: generate_content_free_tier_requests, limit: 5/min")
        ) is FailureKind.RATE_LIMIT

    def test_status_codes(self) -> None:
        class _E(Exception):
            code = 500

        e400 = type("E", (Exception,), {"code": 400})()
        e401 = type("E", (Exception,), {"code": 401})()
        e429 = type("E", (Exception,), {"code": 429})()
        e500 = type("E", (Exception,), {"code": 500})()
        e503 = type("E", (Exception,), {"code": 503})()
        assert classify_failure(e400) is FailureKind.PERMANENT
        assert classify_failure(e401) is FailureKind.PERMANENT
        assert classify_failure(e429) is FailureKind.RATE_LIMIT
        assert classify_failure(e500) is FailureKind.SERVER
        assert classify_failure(e503) is FailureKind.SERVER

    def test_message_markers(self) -> None:
        assert classify_failure(TimeoutError("timed out")) is FailureKind.TIMEOUT
        assert classify_failure(Exception("connection refused")) is FailureKind.NETWORK
        assert classify_failure(Exception("Internal Server Error")) is FailureKind.SERVER
        assert classify_failure(Exception("quota exceeded")) is FailureKind.RATE_LIMIT
        assert classify_failure(Exception("billing details")) is FailureKind.QUOTA
        assert classify_failure(Exception("something weird")) is FailureKind.UNKNOWN


class TestRetryStrategy:
    def test_should_retry_only_recoverable(self) -> None:
        s = RetryStrategy(max_retries=2)
        assert s.should_retry(0, FailureKind.RATE_LIMIT)
        assert s.should_retry(1, FailureKind.TIMEOUT)
        assert not s.should_retry(2, FailureKind.NETWORK)  # budget exhausted
        assert not s.should_retry(0, FailureKind.QUOTA)  # fail over now
        assert not s.should_retry(0, FailureKind.PERMANENT)

    def test_exponential_backoff_capped(self) -> None:
        s = RetryStrategy(max_retries=5, base_backoff_seconds=1.0, max_backoff_seconds=5.0)
        assert s.backoff_seconds(1) == 1.0
        assert s.backoff_seconds(2) == 2.0
        assert s.backoff_seconds(3) == 4.0
        assert s.backoff_seconds(4) == 5.0  # capped


class TestCircuitBreaker:
    def test_opens_after_threshold(self) -> None:
        b = CircuitBreaker(failure_threshold=3)
        for _ in range(3):
            b.record_failure()
        assert b.state is CircuitState.OPEN
        assert b.is_circuit_open() is True

    def test_rejects_while_open(self) -> None:
        b = CircuitBreaker(failure_threshold=1, cooldown_seconds=30.0, now_fn=_Clock())
        b.record_failure()
        assert b.allow_request() is False  # skipped while open

    def test_transitions_half_open_and_recovers(self) -> None:
        clock = _Clock()
        b = CircuitBreaker(failure_threshold=1, cooldown_seconds=30.0, now_fn=clock)
        b.record_failure()
        assert b.state is CircuitState.OPEN

        clock.now += 31.0  # past cooldown
        assert b.allow_request() is True  # probe admitted
        assert b.state is CircuitState.HALF_OPEN
        b.record_success()
        assert b.state is CircuitState.CLOSED

    def test_half_open_budget_limited(self) -> None:
        clock = _Clock()
        b = CircuitBreaker(
            failure_threshold=1, cooldown_seconds=30.0,
            half_open_max_calls=1, now_fn=clock,
        )
        b.record_failure()
        clock.now += 31.0
        assert b.allow_request() is True
        assert b.allow_request() is False  # budget used

    def test_failed_probe_retrips_immediately(self) -> None:
        clock = _Clock()
        b = CircuitBreaker(failure_threshold=3, cooldown_seconds=30.0, now_fn=clock)
        b.record_failure()  # 1 failure — not open yet
        b.record_failure()
        b.record_failure()  # open
        clock.now += 31.0
        assert b.allow_request() is True  # half-open probe
        b.record_failure()  # probe fails → open again with fresh cooldown
        assert b.state is CircuitState.OPEN
        assert b.allow_request() is False  # still in cooldown


class TestProviderHealth:
    def test_success_rate_over_window(self) -> None:
        h = ProviderHealth("p", window=10)
        assert h.success_rate is None
        h.record_success(10.0)
        h.record_success(20.0)
        h.record_failure(5.0)
        assert h.success_rate == pytest.approx(2 / 3)
        assert h.avg_latency_ms is not None
        assert h.total_requests == 3

    def test_status_thresholds(self) -> None:
        h = ProviderHealth("p", window=10)
        assert h.status(0.9, 0.6, CircuitState.CLOSED) == "unknown"
        h.record_success()
        h.record_success()
        assert h.status(0.9, 0.6, CircuitState.CLOSED) == "healthy"
        h.record_failure()
        assert h.status(0.9, 0.6, CircuitState.CLOSED) == "degraded"
        h.record_failure()
        assert h.status(0.9, 0.6, CircuitState.CLOSED) == "unhealthy"
        assert h.status(0.9, 0.6, CircuitState.OPEN) == "unhealthy"


class TestRouterBasicRouting:
    def _router(self, settings=None, providers=None, **kwargs) -> ProviderRouter:
        settings = settings or _make_settings()
        providers = providers or {}
        return ProviderRouter(
            factory=_StubFactory(providers),
            settings=settings,
            sleep=asyncio.sleep,
            **kwargs,
        )

    @pytest.mark.asyncio
    async def test_routes_to_primary_and_marks_active(self) -> None:
        primary = _StubProvider("gemini")
        secondary = _StubProvider("openai")
        r = self._router(providers={"gemini": primary, "openai": secondary})
        result = await r.chat(_msg())
        assert result.content == "reply-gemini"
        assert r.active_provider == "gemini"
        assert primary.calls == 1
        assert secondary.calls == 0
        snap = r.health_snapshot()
        assert snap["active_provider"] == "gemini"

    @pytest.mark.asyncio
    async def test_priority_list_controls_order(self) -> None:
        settings = _make_settings(PROVIDER_PRIORITY=["openai", "gemini"])
        primary = _StubProvider("openai")
        r = self._router(
            settings=settings, providers={"openai": primary, "gemini": _StubProvider("gemini")}
        )
        await r.chat(_msg())
        assert r.active_provider == "openai"
        assert r._priority() == ["openai", "gemini"]

    @pytest.mark.asyncio
    async def test_no_providers_configured_raises(self) -> None:
        settings = _make_settings(
            OPENAI_API_KEY="", ANTHROPIC_API_KEY="", GEMINI_API_KEY="",
            OPENROUTER_API_KEY="", OLLAMA_BASE_URL="",
        )
        r = self._router(settings=settings, providers={})
        with pytest.raises(ProviderNotAvailableError):
            await r.chat(_msg())

    @pytest.mark.asyncio
    async def test_provider_missing_at_runtime_skipped(self) -> None:
        # 'openai' configured (key set) but factory cannot produce it.
        settings = _make_settings(PROVIDER_PRIORITY=["openai"])
        r = self._router(settings=settings, providers={})
        with pytest.raises(ProviderNotAvailableError):
            await r.chat(_msg())


class TestRouterFailover:
    @pytest.mark.asyncio
    async def test_retries_then_fails_over(self) -> None:
        class _RateLimited(Exception):
            code = 429

        sleeps: List[float] = []

        async def _record_sleep(delay: float) -> None:
            sleeps.append(delay)

        async def _boom(messages, config=None):
            raise _RateLimited("rate limit hit")

        primary = _StubProvider("gemini", handler=_boom)
        secondary = _StubProvider("openai")
        settings = _make_settings(PROVIDER_RETRY_MAX=2, PROVIDER_RETRY_BASE_BACKOFF_SECONDS=0.5)
        r = ProviderRouter(
            factory=_StubFactory({"gemini": primary, "openai": secondary}),
            settings=settings,
            sleep=_record_sleep,
        )
        result = await r.chat(_msg())
        assert result.content == "reply-openai"
        assert primary.calls == 3  # 1 initial + 2 retries
        assert sleeps == [0.5, 1.0]  # exponential backoff
        assert r.active_provider == "openai"

        metrics = r.metrics_snapshot()
        assert metrics["totals"]["failovers"] == 1
        assert metrics["totals"]["retries"] == 2
        events = metrics["failover_events"]
        assert events[0]["from"] == "gemini"
        assert events[0]["to"] == "openai"

    @pytest.mark.asyncio
    async def test_quota_fails_over_immediately(self) -> None:
        class _DailyCap(Exception):
            code = 429

        async def _boom(messages, config=None):
            raise _DailyCap("You exceeded your current quota, please check your plan and billing")

        primary = _StubProvider("gemini", handler=_boom)
        secondary = _StubProvider("openai")
        r = ProviderRouter(
            factory=_StubFactory({"gemini": primary, "openai": secondary}),
            settings=_make_settings(PROVIDER_RETRY_MAX=5),
            sleep=asyncio.sleep,
        )
        result = await r.chat(_msg())
        assert result.content == "reply-openai"
        assert primary.calls == 1  # permanent quota — no retry burn
        assert r.active_provider == "openai"

    @pytest.mark.asyncio
    async def test_all_providers_fail_raises_with_details(self) -> None:
        class _RateLimited(Exception):
            code = 429

        async def _boom(messages, config=None):
            raise _RateLimited("rate limit hit")

        p1 = _StubProvider("gemini", handler=_boom)
        p2 = _StubProvider("openai", handler=_boom)
        r = ProviderRouter(
            factory=_StubFactory({"gemini": p1, "openai": p2}),
            settings=_make_settings(PROVIDER_RETRY_MAX=0),
            sleep=asyncio.sleep,
        )
        with pytest.raises(AllProvidersFailedError) as excinfo:
            await r.chat(_msg())
        assert len(excinfo.value.failures) == 2
        assert excinfo.value.failures[0]["provider"] == "gemini"
        assert excinfo.value.failures[0]["kind"] == "rate_limit"


class TestRouterCircuit:
    @pytest.mark.asyncio
    async def test_open_circuit_skips_failing_provider(self) -> None:
        clock = _Clock()
        settings = _make_settings(
            PROVIDER_RETRY_MAX=2,
            PROVIDER_CIRCUIT_BREAKER_FAILURE_THRESHOLD=3,
        )

        async def _boom(messages, config=None):
            raise Exception("server error")

        primary = _StubProvider("gemini", handler=_boom)
        secondary = _StubProvider("openai")
        r = ProviderRouter(
            factory=_StubFactory({"gemini": primary, "openai": secondary}),
            settings=settings,
            sleep=asyncio.sleep,
            now_fn=clock,
        )
        # First call fails over to openai (3 failures trip the breaker).
        result = await r.chat(_msg())
        assert result.content == "reply-openai"
        entry = next(e for e in r.entries if e.name == "gemini")
        assert entry.breaker.state is CircuitState.OPEN

        # Subsequent calls skip gemini entirely (still open within cooldown).
        result = await r.chat(_msg())
        assert result.content == "reply-openai"
        assert primary.calls == 3  # no new attempts while open

    @pytest.mark.asyncio
    async def test_circuit_recovers_after_cooldown_via_probe(self) -> None:
        clock = _Clock()
        settings = _make_settings(
            PROVIDER_RETRY_MAX=2,
            PROVIDER_CIRCUIT_BREAKER_FAILURE_THRESHOLD=3,
            PROVIDER_CIRCUIT_BREAKER_COOLDOWN_SECONDS=30.0,
        )

        fail_times = 3

        async def _flaky(messages, config=None):
            nonlocal fail_times
            if fail_times > 0:
                fail_times -= 1
                raise Exception("server error")
            return LLMResponse(content="reply-gemini")

        primary = _StubProvider("gemini", handler=_flaky)
        secondary = _StubProvider("openai")
        r = ProviderRouter(
            factory=_StubFactory({"gemini": primary, "openai": secondary}),
            settings=settings,
            sleep=asyncio.sleep,
            now_fn=clock,
        )
        await r.chat(_msg())  # trips breaker → failover to openai
        entry = next(e for e in r.entries if e.name == "gemini")
        assert entry.breaker.state is CircuitState.OPEN

        clock.now += 31.0  # past cooldown → half-open probe admitted
        result = await r.chat(_msg())
        assert result.content == "reply-gemini"
        assert entry.breaker.state is CircuitState.CLOSED
        assert r.active_provider == "gemini"


class TestRouterStreaming:
    @pytest.mark.asyncio
    async def test_stream_fails_over_before_first_token(self) -> None:
        async def _boom(messages, config=None):
            raise Exception("connection refused")
            yield  # pragma: no cover

        primary = _StubProvider("gemini", handler=_boom)
        secondary = _StubProvider("openai")
        r = ProviderRouter(
            factory=_StubFactory({"gemini": primary, "openai": secondary}),
            settings=_make_settings(),
            sleep=asyncio.sleep,
        )
        chunks = [c async for c in r.chat_stream(_msg())]
        assert chunks == ["chunk-openai"]
        assert r.active_provider == "openai"

    @pytest.mark.asyncio
    async def test_stream_failure_mid_stream_surfaces_error(self) -> None:
        async def _flaky(messages, config=None):
            yield "partial-"
            raise Exception("network died")
            yield "rest"  # pragma: no cover

        primary = _StubProvider("gemini", handler=_flaky)
        r = ProviderRouter(
            factory=_StubFactory({"gemini": primary}),
            settings=_make_settings(),
            sleep=asyncio.sleep,
        )
        with pytest.raises(LLMError):
            chunks = [c async for c in r.chat_stream(_msg())]
            assert chunks  # pragma: no cover


class TestRouterObservability:
    def _router(self, **kwargs) -> ProviderRouter:
        return ProviderRouter(
            factory=_StubFactory({"gemini": _StubProvider("gemini")}),
            settings=_make_settings(**kwargs),
            sleep=asyncio.sleep,
        )

    def test_config_snapshot_redacts_secrets(self) -> None:
        r = self._router()
        cfg = r.config_snapshot()
        blob = str(cfg)
        assert "sk-test" not in blob
        assert "ak-test" not in blob
        assert "gk-test" not in blob
        assert "or-test" not in blob
        assert cfg["providers"]["gemini"]["key"].startswith("***")
        assert cfg["providers"]["openai"]["configured"] is True

    def test_config_snapshot_marks_unconfigured(self) -> None:
        r = self._router(GEMINI_API_KEY="")
        cfg = r.config_snapshot()
        assert cfg["providers"]["gemini"]["configured"] is False
        assert cfg["providers"]["gemini"]["key"] == "<not set>"

    def test_metrics_totals_and_uptime(self) -> None:
        r = self._router()
        snap = r.metrics_snapshot()
        assert snap["totals"]["total_requests"] == 0
        assert "gemini" in snap["uptime_seconds"]

    def test_provider_snapshots_list_priority(self) -> None:
        r = self._router()
        names = [p["name"] for p in r.provider_snapshots()]
        assert names[0] == "gemini"  # LLM_PROVIDER primary
        assert "fake" in names

    def test_primary_default_model(self) -> None:
        r = self._router()
        assert r.primary_default_model() == "gemini-model"


class TestRoutedProvider:
    def test_facade_delegates_to_router(self) -> None:
        router = ProviderRouter(
            factory=_StubFactory({"gemini": _StubProvider("gemini")}),
            settings=_make_settings(),
            sleep=asyncio.sleep,
        )
        facade = RoutedProvider(router)
        assert facade.provider_name == "routed"
        assert facade.default_model == "gemini-model"

    @pytest.mark.asyncio
    async def test_facade_chat_returns_router_result(self) -> None:
        router = ProviderRouter(
            factory=_StubFactory({"gemini": _StubProvider("gemini")}),
            settings=_make_settings(),
            sleep=asyncio.sleep,
        )
        facade = RoutedProvider(router)
        result = await facade.chat(_msg())
        assert result.content == "reply-gemini"


class TestRedaction:
    def test_redact_secret_masks_value(self) -> None:
        masked = redact_secret("sk-abcdef1234567890")
        assert masked == "sk-a…7890"
        assert "abcdef123456" not in masked
        assert redact_secret("") == "<not set>"
        assert redact_secret(None) == "<not set>"
        assert redact_secret("abc") == "***"  # too short to keep a hint

    def test_redact_dict_recursive(self) -> None:
        payload = {
            "api_key": "super-secret",
            "nested": {"token": "tok", "keep": "visible"},
            "list": [{"password": "pw"}],
            "GEMINI_API_KEY": "gk-secret",
        }
        out = redact_dict(payload)
        blob = str(out)
        assert "super-secret" not in blob
        assert "gk-secret" not in blob
        assert out["api_key"] == "supe…cret"
        assert out["nested"]["token"] == "***"  # too short to keep a hint
        assert out["list"][0]["password"] == "***"
        assert out["nested"]["keep"] == "visible"


class TestFactoryRoutingWiring:
    def test_get_provider_none_returns_routed_when_enabled(self) -> None:
        from app.config import settings
        from app.llm import router as router_mod
        from app.llm.factory import factory

        router_mod.reset_router()
        with patch.object(settings, "PROVIDER_ROUTING_ENABLED", True):
            provider = factory.get_provider(None)
            assert isinstance(provider, RoutedProvider)
            assert provider.provider_name == "routed"

    def test_get_provider_none_returns_named_when_disabled(self) -> None:
        from app.config import settings
        from app.llm.factory import factory

        with patch.object(settings, "PROVIDER_ROUTING_ENABLED", False):
            provider = factory.get_provider(None)
            assert not isinstance(provider, RoutedProvider)
            assert provider.provider_name == settings.LLM_PROVIDER

    def test_ollama_and_openrouter_registered(self) -> None:
        from app.llm.factory import factory

        assert "ollama" in factory._providers
        assert "openrouter" in factory._providers


class TestNewProviderConfig:
    def test_ollama_provider_no_key_required(self) -> None:
        from app.config import settings
        from app.llm.providers.ollama import OllamaProvider

        with patch.object(settings, "OLLAMA_BASE_URL", "http://localhost:11434"):
            provider = OllamaProvider()
            assert provider.provider_name == "ollama"
            assert provider.default_model

    def test_ollama_resolves_model_sentinel_to_local_model(self) -> None:
        """LLMConfig() defaults to the OpenAI sentinel — Ollama must use its
        own local model instead of requesting gpt-4o-mini from the server."""
        from app.config import settings
        from app.llm.providers.ollama import OllamaProvider

        with patch.object(settings, "OLLAMA_BASE_URL", "http://localhost:11434"):
            provider = OllamaProvider()
        msgs, cfg, model = provider._build_args([LLMMessage(role="user", content="hi")], LLMConfig())
        assert model == provider.default_model
        assert model != LLMConfig().model
        assert msgs[0]["content"] == "hi"

    def test_openrouter_requires_key(self) -> None:
        from app.config import settings
        from app.core.exceptions import LLMConfigurationError
        from app.llm.providers.openrouter import OpenRouterProvider

        with patch.object(settings, "OPENROUTER_API_KEY", None):
            with pytest.raises(LLMConfigurationError):
                OpenRouterProvider()


class TestMetricsPersistenceDisabled:
    @pytest.mark.asyncio
    async def test_record_snapshot_noop_when_disabled(self) -> None:
        from app.services.provider_metrics_store import ProviderMetricsStore

        store = ProviderMetricsStore()
        ok = await store.record_snapshot([{"provider": "gemini", "status": "healthy"}])
        assert ok is False  # no DB / persistence unavailable → safe no-op
        assert await store.latest("gemini") is None
        assert await store.history("gemini") == []


# ── API surface (deterministic via injected router) ────────────


class TestProviderApi:
    def _client(self):
        from fastapi.testclient import TestClient

        from app.main import app as main_app

        return TestClient(main_app)

    def test_providers_endpoint_shape(self) -> None:
        client = self._client()
        resp = client.get("/api/v1/providers")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert "providers" in body["data"]
        assert "priority" in body["data"]
        assert any(p["name"] == "fake" for p in body["data"]["providers"])

    def test_providers_health_endpoint(self) -> None:
        client = self._client()
        resp = client.get("/api/v1/providers/health")
        assert resp.status_code == 200
        assert resp.json()["data"]["routing_enabled"] is True

    def test_providers_metrics_endpoint(self) -> None:
        client = self._client()
        resp = client.get("/api/v1/providers/metrics")
        assert resp.status_code == 200
        assert "totals" in resp.json()["data"]

    def test_providers_config_never_leaks_secrets(self) -> None:
        client = self._client()
        resp = client.get("/api/v1/providers/config")
        assert resp.status_code == 200
        blob = str(resp.json())
        for attr in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
                     "OPENROUTER_API_KEY"):
            key = getattr(self._settings(), attr)
            if key:
                assert key not in blob

    @staticmethod
    def _settings():
        from app.config import settings

        return settings
