"""Tests for Phase 20B — provider reliability hardening.

Covers:
- automatic health probing (probe_provider / probe_all, passive counters)
- recovery detection (bad-spell success → recovery + warm-up)
- health-based provider selection (healthy before degraded/unhealthy, probe priority)
- adaptive request timeouts from observed latency
- configurable post-failure cooldown
- the background ProviderHealthProbe loop (deterministic via injected sleep)

All tests are deterministic and never call a paid LLM.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Callable, Dict, List, Optional

import pytest

from app.llm.base import BaseLLMProvider, LLMConfig, LLMMessage, LLMResponse
from app.llm.router import CircuitState, ProviderHealth, ProviderRouter


def _msg(content: str = "hello") -> List[LLMMessage]:
    return [LLMMessage(role="user", content=content)]


class _StubProvider(BaseLLMProvider):
    def __init__(self, name: str, handler: Optional[Callable] = None) -> None:
        self._name = name
        self._handler = handler
        self.calls = 0

    @property
    def provider_name(self) -> str:
        return self._name

    @property
    def default_model(self) -> str:
        return f"{self._name}-model"

    async def chat(self, messages, config=None):
        self.calls += 1
        if self._handler is None:
            return LLMResponse(content=f"reply-{self._name}")
        return await self._handler(messages, config)

    async def chat_stream(self, messages, config=None):
        yield f"chunk-{self._name}"


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
        LLM_PROVIDER_FALLBACKS={},
        PROVIDER_TIMEOUT_SECONDS=10,
        PROVIDER_RETRY_MAX=2,
        PROVIDER_RETRY_BASE_BACKOFF_SECONDS=0.5,
        PROVIDER_RETRY_MAX_BACKOFF_SECONDS=10.0,
        PROVIDER_CIRCUIT_BREAKER_FAILURE_THRESHOLD=3,
        PROVIDER_CIRCUIT_BREAKER_COOLDOWN_SECONDS=30.0,
        PROVIDER_CIRCUIT_BREAKER_HALF_OPEN_MAX_CALLS=2,
        PROVIDER_HEALTH_WINDOW=100,
        PROVIDER_HEALTH_MIN_SAMPLES=5,
        PROVIDER_HEALTH_DEGRADED_SUCCESS_RATE=0.5,
        PROVIDER_HEALTH_UNHEALTHY_SUCCESS_RATE=0.3,
        PROVIDER_METRICS_PERSIST=True,
        PROVIDER_HEALTH_PROBE_ENABLED=True,
        PROVIDER_HEALTH_PROBE_INTERVAL_SECONDS=120.0,
        PROVIDER_HEALTH_PROBE_TIMEOUT_SECONDS=5.0,
        PROVIDER_HEALTH_BASED_SELECTION=True,
        PROVIDER_ADAPTIVE_TIMEOUT_ENABLED=True,
        PROVIDER_ADAPTIVE_TIMEOUT_MULTIPLIER=3.0,
        PROVIDER_ADAPTIVE_TIMEOUT_MAX_SECONDS=60.0,
        PROVIDER_COOLDOWN_AFTER_FAILURE_SECONDS=5.0,
        PROVIDER_WARM_UP_SECONDS=30.0,
        OPENAI_API_KEY="sk-test",
        ANTHROPIC_API_KEY="ak-test",
        GEMINI_API_KEY="gk-test",
        OPENROUTER_API_KEY="or-test",
        NVIDIA_API_KEY="nv-test",
        OLLAMA_BASE_URL="http://localhost:11434",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class _Clock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


class TestProviderHealthRecovery:
    def test_record_recovery_episode_is_idempotent(self) -> None:
        h = ProviderHealth("p")
        h.record_failure(10.0)
        h.record_recovery()
        assert h.recoveries == 1
        assert h.last_recovery_at is not None
        # A second recovery with no new failure is the same episode.
        h.record_recovery()
        assert h.recoveries == 1
        # A new failure starts a fresh episode.
        h.record_failure(10.0)
        h.record_recovery()
        assert h.recoveries == 2

    def test_success_after_failure_is_recovery(self) -> None:
        h = ProviderHealth("p")
        h.record_failure(10.0)
        h.record_success(20.0)
        assert h.recoveries == 1
        assert h.last_recovery_at is not None

    def test_probe_counters_do_not_enter_traffic_window(self) -> None:
        h = ProviderHealth("p")
        h.record_probe(True, 5.0)
        h.record_probe(False, 5.0)
        assert h.probes == 2
        assert h.failed_probes == 1
        assert h.last_probe_ok is False
        # Probes never pollute the success-rate window.
        assert h.success_rate is None
        assert h.total_requests == 0

    def test_warming_state(self) -> None:
        h = ProviderHealth("p")
        assert h.is_warming() is False
        h.mark_warming(30.0)
        assert h.is_warming() is True
        h.mark_warming(0.0)
        assert h.is_warming() is False


class TestHealthBasedSelection:
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
    async def test_degraded_provider_is_last_resort(self) -> None:
        # gemini is degraded (bad success rate), openai is healthy — even
        # though gemini has higher priority, openai must be tried first.
        settings = _make_settings(PROVIDER_PRIORITY=["gemini", "openai"])
        gemini = _StubProvider("gemini")
        openai = _StubProvider("openai")
        r = self._router(settings=settings, providers={"gemini": gemini, "openai": openai})
        gemini_entry = next(e for e in r.entries if e.name == "gemini")
        openai_entry = next(e for e in r.entries if e.name == "openai")
        for _ in range(6):  # enough samples for the rate ranks to apply
            gemini_entry.health.record_failure(50.0)

        result = await r.chat(_msg())
        assert result.content == "reply-openai"
        assert r.active_provider == "openai"
        assert gemini.calls == 0  # never tried while a healthier option exists

    @pytest.mark.asyncio
    async def test_few_samples_do_not_starve_provider(self) -> None:
        # A single failure (cold start / one bad call) must NOT brand the
        # provider 'unhealthy': with fewer than PROVIDER_HEALTH_MIN_SAMPLES
        # samples it stays 'unknown' and priority decides, so its circuit
        # can still trip from accumulating consecutive failures.
        settings = _make_settings(
            PROVIDER_PRIORITY=["gemini", "openai"],
            PROVIDER_RETRY_MAX=0,
            PROVIDER_CIRCUIT_BREAKER_FAILURE_THRESHOLD=2,
            PROVIDER_COOLDOWN_AFTER_FAILURE_SECONDS=0.0,
        )
        fail_times = 3

        async def _flaky(messages, config=None):
            nonlocal fail_times
            if fail_times > 0:
                fail_times -= 1
                raise Exception("server error")
            return LLMResponse(content="reply-gemini")

        gemini = _StubProvider("gemini", handler=_flaky)
        openai = _StubProvider("openai")
        r = ProviderRouter(
            factory=_StubFactory({"gemini": gemini, "openai": openai}),
            settings=settings,
            sleep=asyncio.sleep,
        )
        # Two calls: gemini keeps being tried (not starved) and trips its
        # circuit after 2 consecutive failures.
        for _ in range(2):
            result = await r.chat(_msg())
            assert result.content == "reply-openai"
        gemini_entry = next(e for e in r.entries if e.name == "gemini")
        assert gemini_entry.breaker.state is CircuitState.OPEN
        assert gemini_entry.health.consecutive_failures >= 2

    @pytest.mark.asyncio
    async def test_unhealthy_only_used_when_nothing_else(self) -> None:
        # openai is unhealthy, gemini healthy — gemini wins by health.
        settings = _make_settings(PROVIDER_PRIORITY=["openai", "gemini"])
        gemini = _StubProvider("gemini")
        openai = _StubProvider("openai")
        r = self._router(settings=settings, providers={"gemini": gemini, "openai": openai})
        openai_entry = next(e for e in r.entries if e.name == "openai")
        for _ in range(6):  # enough samples for the rate ranks to apply
            openai_entry.health.record_failure(1.0)

        result = await r.chat(_msg())
        assert result.content == "reply-gemini"

    @pytest.mark.asyncio
    async def test_recovering_probe_gets_priority(self) -> None:
        # OPEN circuit past cooldown is due for a recovery probe — the probe
        # must be admitted BEFORE the healthy provider (otherwise health-based
        # selection would starve the circuit breaker of probes).
        clock = _Clock()
        settings = _make_settings(
            PROVIDER_PRIORITY=["gemini", "openai"],
            PROVIDER_RETRY_MAX=0,
            PROVIDER_CIRCUIT_BREAKER_FAILURE_THRESHOLD=1,
            PROVIDER_CIRCUIT_BREAKER_COOLDOWN_SECONDS=30.0,
            PROVIDER_COOLDOWN_AFTER_FAILURE_SECONDS=0.0,
        )

        fail_times = 1

        async def _flaky(messages, config=None):
            nonlocal fail_times
            if fail_times > 0:
                fail_times -= 1
                raise Exception("server error")
            return LLMResponse(content="reply-gemini")

        gemini = _StubProvider("gemini", handler=_flaky)
        openai = _StubProvider("openai")
        r = ProviderRouter(
            factory=_StubFactory({"gemini": gemini, "openai": openai}),
            settings=settings,
            sleep=asyncio.sleep,
            now_fn=clock,
        )
        result = await r.chat(_msg())  # gemini trips its circuit, failover
        assert result.content == "reply-openai"
        gemini_entry = next(e for e in r.entries if e.name == "gemini")
        assert gemini_entry.breaker.state is CircuitState.OPEN

        # Past cooldown the probe is due — gemini gets tried first again.
        clock.now += 31.0
        result = await r.chat(_msg())
        assert result.content == "reply-gemini"
        assert gemini_entry.breaker.state is CircuitState.CLOSED

    def test_selection_disabled_keeps_priority_order(self) -> None:
        settings = _make_settings(
            PROVIDER_PRIORITY=["gemini", "openai"],
            PROVIDER_HEALTH_BASED_SELECTION=False,
        )
        r = self._router(settings=settings, providers={
            "gemini": _StubProvider("gemini"), "openai": _StubProvider("openai"),
        })
        names = [e.name for e in r._ordered_entries()]
        assert names == ["gemini", "openai"]


class TestAdaptiveTimeout:
    def test_effective_timeout_scales_with_latency(self) -> None:
        settings = _make_settings(
            PROVIDER_TIMEOUT_SECONDS=10.0,
            PROVIDER_ADAPTIVE_TIMEOUT_MULTIPLIER=3.0,
            PROVIDER_ADAPTIVE_TIMEOUT_MAX_SECONDS=60.0,
        )
        r = ProviderRouter(settings=settings, factory=_StubFactory({}))
        entry = SimpleNamespace(health=SimpleNamespace(avg_latency_ms=5000.0))
        # 5000ms avg → 15s budget (3x), above the 10s base.
        assert r._effective_timeout(entry) == pytest.approx(15.0)
        # Very slow provider is capped at 60s.
        entry_slow = SimpleNamespace(health=SimpleNamespace(avg_latency_ms=30000.0))
        assert r._effective_timeout(entry_slow) == pytest.approx(60.0)

    def test_adaptive_disabled_uses_base(self) -> None:
        settings = _make_settings(
            PROVIDER_TIMEOUT_SECONDS=10.0,
            PROVIDER_ADAPTIVE_TIMEOUT_ENABLED=False,
        )
        r = ProviderRouter(settings=settings, factory=_StubFactory({}))
        entry = SimpleNamespace(health=SimpleNamespace(avg_latency_ms=5000.0))
        assert r._effective_timeout(entry) == pytest.approx(10.0)

    def test_no_latency_data_uses_base(self) -> None:
        r = ProviderRouter(settings=_make_settings(), factory=_StubFactory({}))
        entry = SimpleNamespace(health=SimpleNamespace(avg_latency_ms=None))
        assert r._effective_timeout(entry) == pytest.approx(10.0)


class TestPostFailureCooldown:
    @pytest.mark.asyncio
    async def test_failed_provider_is_skipped_during_cooldown(self) -> None:
        clock = _Clock()
        settings = _make_settings(
            PROVIDER_PRIORITY=["gemini", "openai"],
            PROVIDER_RETRY_MAX=0,
            PROVIDER_COOLDOWN_AFTER_FAILURE_SECONDS=5.0,
            # Isolate the cooldown mechanic from health-based reordering.
            PROVIDER_HEALTH_BASED_SELECTION=False,
        )

        async def _boom(messages, config=None):
            raise Exception("server error")

        gemini = _StubProvider("gemini", handler=_boom)
        openai = _StubProvider("openai")
        r = ProviderRouter(
            factory=_StubFactory({"gemini": gemini, "openai": openai}),
            settings=settings,
            sleep=asyncio.sleep,
            now_fn=clock,
        )
        result = await r.chat(_msg())
        assert result.content == "reply-openai"
        gemini_entry = next(e for e in r.entries if e.name == "gemini")
        # Circuit may still be closed (threshold 3, only 1 failure) but the
        # post-failure cooldown must keep gemini out.
        assert gemini_entry.cooldown_until > clock.now

        calls_after_first = gemini.calls
        result = await r.chat(_msg())
        assert result.content == "reply-openai"
        assert gemini.calls == calls_after_first  # skipped during cooldown

        # After the cooldown expires gemini is eligible again.
        clock.now += 6.0
        gemini._handler = None  # recovered
        result = await r.chat(_msg())
        assert result.content == "reply-gemini"


class TestHealthProbes:
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
    async def test_probe_all_hits_every_configured_provider(self) -> None:
        gemini = _StubProvider("gemini")
        openai = _StubProvider("openai")
        r = self._router(providers={"gemini": gemini, "openai": openai})
        results = await r.probe_all()
        assert results["gemini"] is True
        assert results["openai"] is True
        assert gemini.calls == 1 and openai.calls == 1
        ge = next(e for e in r.entries if e.name == "gemini")
        assert ge.health.probes == 1
        assert ge.health.success_rate is None  # probes stay out of the window

    @pytest.mark.asyncio
    async def test_probe_failure_is_passive(self) -> None:
        async def _boom(messages, config=None):
            raise Exception("connection refused")

        gemini = _StubProvider("gemini", handler=_boom)
        r = self._router(providers={"gemini": gemini})
        ok = await r.probe_provider("gemini")
        assert ok is False
        ge = next(e for e in r.entries if e.name == "gemini")
        assert ge.health.failed_probes == 1
        # Passive: no breaker trip, no traffic-window pollution.
        assert ge.breaker.state is CircuitState.CLOSED
        assert ge.health.success_rate is None

    @pytest.mark.asyncio
    async def test_probe_observes_recovery(self) -> None:
        fail_times = 2

        async def _flaky(messages, config=None):
            nonlocal fail_times
            if fail_times > 0:
                fail_times -= 1
                raise Exception("server error")
            return LLMResponse(content="ok")

        gemini = _StubProvider("gemini", handler=_flaky)
        r = self._router(providers={"gemini": gemini})
        await r.probe_provider("gemini")
        await r.probe_provider("gemini")
        assert await r.probe_provider("gemini") is True
        ge = next(e for e in r.entries if e.name == "gemini")
        assert ge.health.recoveries == 1
        assert ge.health.is_warming() is True

    def test_probe_service_loop(self) -> None:
        """The background probe loop starts, probes, and stops cleanly."""
        from app.services.provider_probe import ProviderHealthProbe

        settings = _make_settings(PROVIDER_HEALTH_PROBE_INTERVAL_SECONDS=2.0)
        gemini = _StubProvider("gemini")
        r = ProviderRouter(
            factory=_StubFactory({"gemini": gemini}),
            settings=settings,
            sleep=asyncio.sleep,
        )
        probe = ProviderHealthProbe(router=r, settings=settings)

        async def _run() -> None:
            probe.start()
            assert probe._task is not None
            results = await probe.probe_once()
            assert results.get("gemini") is True
            assert probe.runs >= 1
            await probe.stop()
            assert probe._task is None
            # Starting again after stop is safe (idempotent lifecycle).
            probe.start()
            await probe.stop()
            assert probe._task is None

        asyncio.run(_run())

    def test_probe_disabled_when_interval_zero(self) -> None:
        from app.services.provider_probe import ProviderHealthProbe

        probe = ProviderHealthProbe(settings=_make_settings(
            PROVIDER_HEALTH_PROBE_INTERVAL_SECONDS=0.0))
        assert probe.enabled is False
        probe.start()
        assert probe._task is None


class TestConfigSnapshotReliability:
    def test_config_snapshot_exposes_reliability_knobs(self) -> None:
        r = ProviderRouter(settings=_make_settings(), factory=_StubFactory({}))
        cfg = r.config_snapshot()
        rel = cfg["reliability"]
        assert rel["health_probe_enabled"] is True
        assert rel["health_based_selection"] is True
        assert rel["adaptive_timeout_enabled"] is True
        assert rel["cooldown_after_failure_seconds"] == 5.0
        assert rel["warm_up_seconds"] == 30.0
