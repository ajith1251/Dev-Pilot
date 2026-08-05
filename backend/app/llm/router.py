"""
Provider Router (Phase 19B) — multi-provider failover & reliability platform.

Wraps the existing ``BaseLLMProvider`` abstraction so agents keep calling the
same ``provider.chat(...)`` interface while DevPilot becomes resilient to a
single provider failing:

    Agent → ProviderRouter → HealthMonitor → CircuitBreaker → Provider
                                                                  ↓
    Response ← Automatic failover ← next provider ← retry policy

Responsibilities implemented here:

* Provider selection & priority (health-aware, load-balancing ready)
* Automatic failover (quota / rate limit / timeout / outage / unavailability)
* Bounded retry with exponential backoff (recoverable failures only)
* Deterministic circuit breaker (closed → open → half-open → closed)
* Health monitoring (availability, latency, success rate, failures, uptime)
* Runtime metrics + failover events (observability)
* Secret-safe snapshots for API / CLI / dashboard

The router is provider-agnostic: it only depends on ``BaseLLMProvider``.
"""

from __future__ import annotations

import asyncio
import enum
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

from app.config import settings as _default_settings
from app.core.exceptions import (
    AllProvidersFailedError,
    LLMError,
    ProviderCallFailedError,
    ProviderNotAvailableError,
)
from app.core.logging import logger
from app.llm.base import BaseLLMProvider, LLMConfig, LLMMessage, LLMResponse
from app.llm.factory import factory as _default_factory
from app.llm.redaction import redact_dict, redact_secret

# ── Failure classification ─────────────────────────────────────

# Substrings that indicate *permanent* quota exhaustion (daily cap / billing)
# rather than a transient per-minute rate limit. Never retried — fail over.
_PERMANENT_QUOTA_MARKERS = (
    "exceeded your current quota",
    "check your plan and billing",
    "billing details",
    "per day",
    "daily",
    "payment",
)

_RATE_LIMIT_MARKERS = (
    "429", "rate limit", "too many requests", "resource_exhausted",
    "quota", "requests/minute", "requests per minute",
)

_SERVER_MARKERS = ("internal", "server error", "unavailable", "gateway", "502", "503", "504")

_NETWORK_MARKERS = (
    "connection", "connect", "dns", "socket", "timeout", "timed out",
    "ssl", "network", "unreachable",
)


class FailureKind(enum.Enum):
    """Classification of a provider call failure."""

    QUOTA = "quota"            # permanent quota exhaustion → fail over now
    RATE_LIMIT = "rate_limit"  # 429 → retryable with backoff
    TIMEOUT = "timeout"        # request exceeded the router timeout → retryable
    NETWORK = "network"        # connectivity → retryable
    SERVER = "server"          # 5xx → retryable
    PERMANENT = "permanent"    # 4xx auth/not-found → not retryable, fail over
    UNKNOWN = "unknown"        # conservative → retryable


class Capability(enum.Enum):
    """Routing capabilities (Phase 20B) — typed per-capability fallback chains.

    Each agent stage labels its calls with a capability so the router can
    scope failover to the provider chain configured for that kind of work
    (``DEVPILOT_LLM_PROVIDER_FALLBACKS``). ``GENERAL`` is the fallback for
    unlabelled calls (CLI chat, API probes).
    """

    ANALYSIS = "analysis"    # repo_analyzer, issue_analyzer
    PLANNING = "planning"    # planner
    CODING = "coding"        # coding_agent, fix_agent (long generation)
    TESTING = "testing"      # test_agent
    REVIEW = "review"        # reviewer
    REASONING = "reasoning"  # collaborative reasoning / consensus
    GENERAL = "general"      # everything unlabelled

    @classmethod
    def names(cls) -> List[str]:
        """Canonical capability order (deterministic union ordering)."""
        return [c.value for c in cls]


RETRYABLE_KINDS = {
    FailureKind.RATE_LIMIT,
    FailureKind.TIMEOUT,
    FailureKind.NETWORK,
    FailureKind.SERVER,
    FailureKind.UNKNOWN,
}


def classify_failure(exc: Exception) -> FailureKind:
    """Provider-agnostic failure classification.

    Order matters: permanent quota markers are checked before generic rate
    limit markers so a daily-cap 429 is treated as QUOTA (fail over) rather
    than RATE_LIMIT (retry until the cap-reset never comes).
    """
    message = (str(exc) or "").lower()
    code = getattr(exc, "code", None)

    if isinstance(exc, TimeoutError):
        return FailureKind.TIMEOUT
    if isinstance(code, int):
        if code == 429:
            if any(marker in message for marker in _PERMANENT_QUOTA_MARKERS):
                return FailureKind.QUOTA
            return FailureKind.RATE_LIMIT
        if code in (500, 502, 503, 504):
            return FailureKind.SERVER
        if 400 <= code <= 499:
            return FailureKind.PERMANENT

    if any(marker in message for marker in _PERMANENT_QUOTA_MARKERS):
        return FailureKind.QUOTA
    if any(marker in message for marker in _RATE_LIMIT_MARKERS):
        return FailureKind.RATE_LIMIT
    if any(marker in message for marker in _SERVER_MARKERS):
        return FailureKind.SERVER
    if any(marker in message for marker in _NETWORK_MARKERS):
        return FailureKind.NETWORK
    return FailureKind.UNKNOWN


# ── Circuit breaker ────────────────────────────────────────────


class CircuitState(enum.Enum):
    """Deterministic circuit breaker states."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Per-provider circuit breaker.

    State machine (all transitions deterministic):

        CLOSED ──(consecutive_failures >= threshold)──▶ OPEN
        OPEN ──(cooldown elapsed)──▶ HALF_OPEN
        HALF_OPEN ──(probe succeeds)──▶ CLOSED
        HALF_OPEN ──(probe fails)──▶ OPEN

    While OPEN the provider is skipped entirely, preventing repeated calls to
    an unhealthy endpoint. HALF_OPEN admits a bounded number of probe calls;
    the first success re-closes the circuit and recovery is automatic.
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        cooldown_seconds: float = 30.0,
        half_open_max_calls: int = 1,
        now_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self.failure_threshold = max(1, int(failure_threshold))
        self.cooldown_seconds = max(0.0, float(cooldown_seconds))
        self.half_open_max_calls = max(1, int(half_open_max_calls))
        self._now = now_fn
        self.state = CircuitState.CLOSED
        self.consecutive_failures = 0
        self.opened_at: Optional[float] = None
        self.half_open_calls = 0

    def is_circuit_open(self, now: Optional[float] = None) -> bool:
        """True when the circuit rejects traffic without mutating state."""
        if self.state is not CircuitState.OPEN:
            return False
        current = now if now is not None else self._now()
        if self.opened_at is not None and current - self.opened_at >= self.cooldown_seconds:
            return False  # past cooldown → a probe will be admitted
        return True

    def allow_request(self, now: Optional[float] = None) -> bool:
        """Admit a request, transitioning OPEN → HALF_OPEN past cooldown.

        Mutates state (half-open probe budget) — call exactly once per
        candidate attempt.
        """
        current = now if now is not None else self._now()
        if self.state is CircuitState.OPEN:
            if self.opened_at is not None and current - self.opened_at >= self.cooldown_seconds:
                self.state = CircuitState.HALF_OPEN
                self.half_open_calls = 0
            else:
                return False
        if self.state is CircuitState.HALF_OPEN:
            if self.half_open_calls >= self.half_open_max_calls:
                return False
            self.half_open_calls += 1
        return True

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.half_open_calls = 0
        self.opened_at = None
        self.state = CircuitState.CLOSED

    def record_failure(self, now: Optional[float] = None) -> None:
        self.consecutive_failures += 1
        if self.state is CircuitState.HALF_OPEN:
            # A failed half-open probe re-trips immediately with a fresh
            # cooldown so a still-broken provider keeps being skipped.
            self.state = CircuitState.OPEN
            self.opened_at = now if now is not None else self._now()
            return
        if self.consecutive_failures >= self.failure_threshold:
            self.state = CircuitState.OPEN
            self.opened_at = now if now is not None else self._now()

    def snapshot(self) -> Dict[str, Any]:
        return {
            "state": self.state.value,
            "consecutive_failures": self.consecutive_failures,
            "failure_threshold": self.failure_threshold,
            "cooldown_seconds": self.cooldown_seconds,
            "half_open_max_calls": self.half_open_max_calls,
        }


# ── Retry strategy ─────────────────────────────────────────────


class RetryStrategy:
    """Bounded retry with exponential backoff.

    Retries ONLY on recoverable failures (rate limit, timeout, network,
    transient server error, unknown). Permanent quota exhaustion and 4xx
    client errors fail over immediately — retrying them would only burn
    backoff time.
    """

    def __init__(
        self,
        max_retries: int = 2,
        base_backoff_seconds: float = 0.5,
        max_backoff_seconds: float = 10.0,
    ) -> None:
        self.max_retries = max(0, int(max_retries))
        self.base_backoff_seconds = max(0.0, float(base_backoff_seconds))
        self.max_backoff_seconds = max(0.1, float(max_backoff_seconds))

    def should_retry(self, attempt: int, kind: FailureKind) -> bool:
        """Attempt is 0-indexed; retry while below max and failure recoverable."""
        return kind in RETRYABLE_KINDS and attempt < self.max_retries

    def backoff_seconds(self, attempt: int) -> float:
        """Exponential backoff for the *next* attempt (attempt is 1-indexed)."""
        delay = self.base_backoff_seconds * (2 ** (attempt - 1))
        return min(delay, self.max_backoff_seconds)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "max_retries": self.max_retries,
            "base_backoff_seconds": self.base_backoff_seconds,
            "max_backoff_seconds": self.max_backoff_seconds,
        }


# ── Health monitoring ──────────────────────────────────────────


class ProviderHealth:
    """Rolling health + runtime metrics for a single provider.

    Success rate is computed over a bounded sliding window so health
    reflects recent behavior (a provider that recovered stops looking
    unhealthy once stale failures age out of the window).
    """

    def __init__(self, provider_name: str, window: int = 100) -> None:
        self.provider_name = provider_name
        self.window = max(1, int(window))
        # One record per request: 1 = success, 0 = failure. Bounded so health
        # reflects recent behavior (stale results age out of the window).
        self.results: deque = deque(maxlen=self.window)
        self.total_requests = 0
        self.successful_requests = 0
        self.failed_requests = 0
        self.retries = 0
        self.failovers = 0
        self.last_latency_ms: Optional[float] = None
        self.avg_latency_ms: Optional[float] = None  # exponential moving average
        self.last_success_at: Optional[float] = None
        self.last_failure_at: Optional[float] = None
        self.consecutive_failures = 0
        self.started_at = time.time()

    def record_success(self, latency_ms: Optional[float] = None) -> None:
        self.total_requests += 1
        self.successful_requests += 1
        self.results.append(1)
        self.consecutive_failures = 0
        self.last_success_at = time.time()
        if latency_ms is not None:
            self.last_latency_ms = latency_ms
            if self.avg_latency_ms is None:
                self.avg_latency_ms = latency_ms
            else:
                self.avg_latency_ms = 0.9 * self.avg_latency_ms + 0.1 * latency_ms

    def record_failure(self, latency_ms: Optional[float] = None) -> None:
        self.total_requests += 1
        self.failed_requests += 1
        self.results.append(0)
        self.consecutive_failures += 1
        self.last_failure_at = time.time()
        if latency_ms is not None:
            self.last_latency_ms = latency_ms
            if self.avg_latency_ms is None:
                self.avg_latency_ms = latency_ms
            else:
                self.avg_latency_ms = 0.9 * self.avg_latency_ms + 0.1 * latency_ms

    def record_retry(self) -> None:
        self.retries += 1

    def record_failover(self) -> None:
        self.failovers += 1

    @property
    def success_rate(self) -> Optional[float]:
        if not self.results:
            return None
        return sum(self.results) / len(self.results)

    def status(
        self,
        degraded_threshold: float,
        unhealthy_threshold: float,
        circuit_state: CircuitState,
    ) -> str:
        if circuit_state is CircuitState.OPEN:
            return "unhealthy"
        rate = self.success_rate
        if rate is None:
            return "unknown"
        if rate < unhealthy_threshold:
            return "unhealthy"
        if rate < degraded_threshold:
            return "degraded"
        return "healthy"

    def uptime_seconds(self) -> float:
        return max(0.0, time.time() - self.started_at)

    def snapshot(self) -> Dict[str, Any]:
        rate = self.success_rate
        return {
            "provider": self.provider_name,
            "status": "unknown",  # filled by the router (needs thresholds)
            "total_requests": self.total_requests,
            "successful_requests": self.successful_requests,
            "failed_requests": self.failed_requests,
            "success_rate": round(rate, 4) if rate is not None else None,
            "consecutive_failures": self.consecutive_failures,
            "retries": self.retries,
            "failovers": self.failovers,
            "avg_latency_ms": round(self.avg_latency_ms, 2)
                if self.avg_latency_ms is not None else None,
            "last_latency_ms": round(self.last_latency_ms, 2)
                if self.last_latency_ms is not None else None,
            "last_success_at": self.last_success_at,
            "last_failure_at": self.last_failure_at,
            "uptime_seconds": round(self.uptime_seconds(), 2),
        }


# ── Provider entry ─────────────────────────────────────────────


@dataclass
class ProviderEntry:
    """A provider registered with the router."""

    name: str
    provider: Optional[BaseLLMProvider]
    priority: int
    configured: bool = True
    enabled: bool = True
    breaker: CircuitBreaker = field(default_factory=CircuitBreaker)
    health: ProviderHealth = field(default_factory=lambda: ProviderHealth("p"))
    default_model: Optional[str] = None

    def snapshot(self, active: bool = False, status: str = "unknown") -> Dict[str, Any]:
        return {
            "name": self.name,
            "priority": self.priority,
            "configured": self.configured,
            "enabled": self.enabled and self.configured,
            "default_model": self.default_model,
            "status": status,
            "active": active,
            "circuit": self.breaker.snapshot(),
            "health": self.health.snapshot(),
        }


# ── Metrics registry ───────────────────────────────────────────


class MetricsRegistry:
    """Aggregate runtime metrics + failover event log.

    Health instances are SHARED with the router's ProviderEntry objects (via
    ``register``), so per-provider counters recorded during execution are the
    same objects aggregated into totals.
    """

    def __init__(self, max_failover_events: int = 50) -> None:
        self._health: Dict[str, ProviderHealth] = {}
        self.failover_events: deque = deque(maxlen=max_failover_events)

    def register(self, name: str, health: ProviderHealth) -> None:
        """Bind a provider name to its shared health instance."""
        self._health[name] = health

    def for_provider(self, name: str, window: int = 100) -> ProviderHealth:
        if name not in self._health:
            self._health[name] = ProviderHealth(name, window=window)
        return self._health[name]

    def record_failover(self, source: str, target: str, reason: str) -> None:
        self.for_provider(source).record_failover()
        self.failover_events.append({
            "timestamp": time.time(),
            "from": source,
            "to": target,
            "reason": reason,
        })

    def totals(self) -> Dict[str, Any]:
        return {
            "total_requests": sum(h.total_requests for h in self._health.values()),
            "successful_requests": sum(h.successful_requests for h in self._health.values()),
            "failed_requests": sum(h.failed_requests for h in self._health.values()),
            "retries": sum(h.retries for h in self._health.values()),
            "failovers": sum(h.failovers for h in self._health.values()),
        }

    def snapshot(self) -> Dict[str, Any]:
        return {
            "totals": self.totals(),
            "per_provider": {name: h.snapshot() for name, h in self._health.items()},
            "failover_events": list(self.failover_events),
        }


# ── Router ─────────────────────────────────────────────────────

# name → (settings attr to check for availability, provider is always-present)
_PROVIDER_AVAILABILITY: Dict[str, tuple] = {
    "openai": ("OPENAI_API_KEY", False),
    "anthropic": ("ANTHROPIC_API_KEY", False),
    "gemini": ("GEMINI_API_KEY", False),
    "openrouter": ("OPENROUTER_API_KEY", False),
    "ollama": ("OLLAMA_BASE_URL", False),
    "fake": ("", True),
}

# Canonical registry order used to fill the default priority list.
_CANONICAL_ORDER = ("gemini", "openai", "anthropic", "openrouter", "ollama", "fake")


class ProviderRouter:
    """Health-aware, failover-capable router over ``BaseLLMProvider``.

    Selection is deterministic: providers are tried in priority order, and
    each candidate is gated by its circuit breaker. A candidate that exhausts
    its retry budget (or hits a non-retryable failure) triggers failover to
    the next healthy provider. When every provider fails the router raises
    ``AllProvidersFailedError`` — it never silently swallows a failure.
    """

    def __init__(
        self,
        factory: Any = None,
        settings: Any = None,
        sleep: Callable[..., Any] = asyncio.sleep,
        now_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._factory = factory if factory is not None else _default_factory
        self._settings = settings if settings is not None else _default_settings
        self._sleep = sleep
        self._now_fn = now_fn
        self._active_provider: Optional[str] = None
        self._timeout_seconds = float(self._settings.PROVIDER_TIMEOUT_SECONDS)

        self._retry = RetryStrategy(
            max_retries=int(self._settings.PROVIDER_RETRY_MAX),
            base_backoff_seconds=float(self._settings.PROVIDER_RETRY_BASE_BACKOFF_SECONDS),
            max_backoff_seconds=float(self._settings.PROVIDER_RETRY_MAX_BACKOFF_SECONDS),
        )

        breaker_kwargs = {
            "failure_threshold": int(self._settings.PROVIDER_CIRCUIT_BREAKER_FAILURE_THRESHOLD),
            "cooldown_seconds": float(self._settings.PROVIDER_CIRCUIT_BREAKER_COOLDOWN_SECONDS),
            "half_open_max_calls": int(self._settings.PROVIDER_CIRCUIT_BREAKER_HALF_OPEN_MAX_CALLS),
            "now_fn": self._now_fn,
        }
        self._breaker_kwargs = breaker_kwargs
        self._health_window = int(self._settings.PROVIDER_HEALTH_WINDOW)
        self.metrics = MetricsRegistry()
        self.entries: List[ProviderEntry] = self._build_entries()
        for entry in self.entries:
            self.metrics.register(entry.name, entry.health)

    # ── Build / priority ─────────────────────────────────────────

    def _default_priority(self) -> List[str]:
        primary = (self._settings.LLM_PROVIDER or "").strip().lower()
        result: List[str] = []
        if primary and primary in _CANONICAL_ORDER:
            result.append(primary)
        for name in _CANONICAL_ORDER:
            if name not in result:
                result.append(name)
        return result

    def _priority(self) -> List[str]:
        configured = list(self._settings.PROVIDER_PRIORITY or [])
        if configured:
            return configured
        return self._default_priority()

    def _fallbacks(self) -> Dict[str, List[str]]:
        """Per-capability typed provider chains (Phase 20B).

        Read defensively so a settings stub without the field (unit tests,
        older configs) degrades to no overrides.
        """
        raw = getattr(self._settings, "LLM_PROVIDER_FALLBACKS", None) or {}
        out: Dict[str, List[str]] = {}
        for cap, names in raw.items():
            cap_key = str(cap).strip().lower()
            if not cap_key:
                continue
            items = [str(n).strip().lower() for n in names if str(n).strip()]
            if items:
                out[cap_key] = items
        return out

    def _priority_for(self, capability: Optional[str]) -> Optional[List[str]]:
        """Typed chain for a capability, else None (use the global list)."""
        if not capability:
            return None
        cap_key = str(capability).strip().lower()
        return self._fallbacks().get(cap_key)

    def _candidate_names(self, capability: Optional[str]) -> List[str]:
        """Provider names tried for a call, in priority order.

        A configured capability chain is authoritative: calls of that kind
        only fall through providers in its list (a typed fallback instead of
        the global ``DEVPILOT_PROVIDER_PRIORITY``). Unlabelled calls and
        capabilities without an override keep the global chain.
        """
        cap_list = self._priority_for(capability)
        if cap_list is not None:
            return cap_list
        return self._priority()

    def _all_priority_names(self) -> List[str]:
        """Union of the global chain and every capability chain.

        Keeps providers that appear ONLY in a capability fallback registered
        with the router (so they get health tracking, circuit breakers and
        observability) even when the global priority excludes them.
        """
        names: List[str] = []
        for name in list(self._priority()):
            if name not in names:
                names.append(name)
        for cap in Capability.names():
            for name in self._fallbacks().get(cap, []):
                if name not in names:
                    names.append(name)
        return names

    def _provider_configured(self, name: str) -> bool:
        if name == "fake":
            return True
        attr, _always = _PROVIDER_AVAILABILITY.get(name, (None, False))
        if attr is None:
            return False
        value = getattr(self._settings, attr, None)
        return bool(value)

    def _build_entries(self) -> List[ProviderEntry]:
        entries: List[ProviderEntry] = []
        seen = set()
        for priority, name in enumerate(self._all_priority_names()):
            if name in seen:
                continue
            seen.add(name)
            configured = self._provider_configured(name)
            provider: Optional[BaseLLMProvider] = None
            default_model: Optional[str] = None
            if configured:
                try:
                    provider = self._factory.get_provider(name)
                    default_model = provider.default_model
                except Exception:
                    configured = False
                    provider = None
            entry = ProviderEntry(
                name=name,
                provider=provider,
                priority=priority,
                configured=configured,
                breaker=CircuitBreaker(**self._breaker_kwargs),
                health=ProviderHealth(name, window=self._health_window),
                default_model=default_model,
            )
            entries.append(entry)
        return entries

    def _ordered_entries(self, capability: Optional[str] = None) -> List[ProviderEntry]:
        """Configured, enabled, circuit-not-open entries in candidate order.

        The candidate set is the typed capability chain when one is configured
        for ``capability``, otherwise the global priority list — so per-call
        failover respects the capability's chain.
        """
        by_name = {e.name: e for e in self.entries}
        result: List[ProviderEntry] = []
        for name in self._candidate_names(capability):
            entry = by_name.get(name)
            if entry is not None and entry.configured and entry.enabled \
                    and not entry.breaker.is_circuit_open():
                result.append(entry)
        return result

    def _set_active(self, name: str) -> None:
        self._active_provider = name

    def _entry_status(self, entry: ProviderEntry) -> str:
        return entry.health.status(
            degraded_threshold=float(self._settings.PROVIDER_HEALTH_DEGRADED_SUCCESS_RATE),
            unhealthy_threshold=float(self._settings.PROVIDER_HEALTH_UNHEALTHY_SUCCESS_RATE),
            circuit_state=entry.breaker.state,
        )

    # ── Execution ────────────────────────────────────────────────

    async def _attempt_chat(
        self, entry: ProviderEntry, messages: List[LLMMessage], config: Optional[LLMConfig],
    ) -> LLMResponse:
        """Try one provider with bounded retries; raise ProviderCallFailedError."""
        attempt = 0
        while True:
            started = time.monotonic()
            try:
                result = await asyncio.wait_for(
                    entry.provider.chat(messages, config),  # type: ignore[union-attr]
                    timeout=self._timeout_seconds,
                )
                latency_ms = (time.monotonic() - started) * 1000.0
                entry.health.record_success(latency_ms)
                entry.breaker.record_success()
                return result
            except Exception as exc:
                latency_ms = (time.monotonic() - started) * 1000.0
                kind = classify_failure(exc)
                entry.health.record_failure(latency_ms)
                entry.breaker.record_failure()
                logger.warning(
                    "[router] %s failed (%s): %s",
                    entry.name, kind.value, str(exc)[:200],
                )
                if self._retry.should_retry(attempt, kind):
                    entry.health.record_retry()
                    delay = self._retry.backoff_seconds(attempt + 1)
                    logger.info(
                        "[router] retrying %s (attempt %d/%d) after %.1fs",
                        entry.name, attempt + 1, self._retry.max_retries, delay,
                    )
                    if delay > 0:
                        await self._sleep(delay)
                    attempt += 1
                    continue
                raise ProviderCallFailedError(
                    provider=entry.name,
                    kind=kind.value,
                    message=str(exc)[:300],
                ) from exc

    async def chat(
        self,
        messages: List[LLMMessage],
        config: Optional[LLMConfig] = None,
        capability: Optional[str] = None,
    ) -> LLMResponse:
        """Route a chat call with health-aware selection + automatic failover.

        ``capability`` (or ``config.capability``) selects the typed fallback
        chain configured via ``DEVPILOT_LLM_PROVIDER_FALLBACKS`` when one
        exists; otherwise the global priority chain is used.
        """
        cap = capability or (getattr(config, "capability", None) if config is not None else None)
        entries = self._ordered_entries(cap)
        if not entries:
            raise ProviderNotAvailableError(
                "No provider is configured and circuit-healthy for the request. "
                "Set an API key and (optionally) DEVPILOT_PROVIDER_PRIORITY or "
                "DEVPILOT_LLM_PROVIDER_FALLBACKS."
            )

        failures: List[Dict[str, str]] = []
        for index, entry in enumerate(entries):
            if not entry.breaker.allow_request():
                continue
            try:
                result = await self._attempt_chat(entry, messages, config)
                self._set_active(entry.name)
                return result
            except ProviderCallFailedError as exc:
                failures.append({
                    "provider": entry.name,
                    "kind": exc.kind,
                    "error": exc.message,
                })
                if index + 1 < len(entries):
                    next_name = entries[index + 1].name
                    self.metrics.record_failover(entry.name, next_name, exc.kind)
                    logger.warning(
                        "[router] failover %s → %s (%s)",
                        entry.name, next_name, exc.kind,
                    )

        raise AllProvidersFailedError(
            "All providers failed after retries and failover.",
            failures=failures,
        )

    async def chat_stream(
        self,
        messages: List[LLMMessage],
        config: Optional[LLMConfig] = None,
        capability: Optional[str] = None,
    ) -> AsyncIterator[str]:
        """Stream a chat call with failover only before the first token.

        A failure mid-stream (after tokens have been yielded) is surfaced as
        an error rather than retried — retrying would duplicate tokens.
        ``capability``/``config.capability`` select the typed fallback chain.
        """
        cap = capability or (getattr(config, "capability", None) if config is not None else None)
        entries = self._ordered_entries(cap)
        if not entries:
            raise ProviderNotAvailableError(
                "No provider is configured and circuit-healthy."
            )

        for index, entry in enumerate(entries):
            if not entry.breaker.allow_request():
                continue
            yielded_any = False
            try:
                stream = entry.provider.chat_stream(messages, config)  # type: ignore[union-attr]
                async for chunk in stream:
                    yielded_any = True
                    self._set_active(entry.name)
                    entry.health.record_success(0)
                    entry.breaker.record_success()
                    yield chunk
                return
            except Exception as exc:
                kind = classify_failure(exc)
                entry.health.record_failure(0)
                entry.breaker.record_failure()
                if yielded_any:
                    raise LLMError(
                        f"Stream from provider '{entry.name}' failed "
                        f"mid-stream ({kind.value}): {exc}"
                    ) from exc
                if index + 1 < len(entries):
                    next_name = entries[index + 1].name
                    self.metrics.record_failover(entry.name, next_name, kind.value)
                    continue
                raise AllProvidersFailedError(
                    "All providers failed while starting a stream."
                ) from exc

    # ── Observability snapshots (secret-safe) ────────────────────

    @property
    def active_provider(self) -> Optional[str]:
        return self._active_provider

    def primary_default_model(self) -> str:
        for entry in self._ordered_entries():
            if entry.provider is not None:
                try:
                    return entry.provider.default_model
                except Exception:
                    continue
        return self._settings.LLM_MODEL

    def provider_snapshots(self) -> List[Dict[str, Any]]:
        return [
            entry.snapshot(
                active=(self._active_provider == entry.name),
                status=self._entry_status(entry),
            )
            for entry in sorted(self.entries, key=lambda e: e.priority)
        ]

    def health_snapshot(self) -> Dict[str, Any]:
        return {
            "routing_enabled": bool(self._settings.PROVIDER_ROUTING_ENABLED),
            "active_provider": self._active_provider,
            "providers": [
                {
                    "name": entry.name,
                    "configured": entry.configured,
                    "enabled": entry.enabled and entry.configured,
                    "status": self._entry_status(entry),
                    "circuit_state": entry.breaker.state.value,
                    "default_model": entry.default_model,
                    "health": entry.health.snapshot(),
                }
                for entry in sorted(self.entries, key=lambda e: e.priority)
            ],
        }

    def metrics_snapshot(self) -> Dict[str, Any]:
        per_provider = {}
        for entry in self.entries:
            snap = entry.health.snapshot()
            snap["circuit_state"] = entry.breaker.state.value
            per_provider[entry.name] = snap
        return {
            "totals": self.metrics.totals(),
            "per_provider": per_provider,
            "failover_events": list(self.metrics.failover_events),
            "uptime_seconds": {
                entry.name: round(entry.health.uptime_seconds(), 2)
                for entry in self.entries
            },
        }

    def config_snapshot(self) -> Dict[str, Any]:
        """Redacted router configuration — NEVER includes raw secrets."""
        availability = {}
        for name in _CANONICAL_ORDER:
            attr, _ = _PROVIDER_AVAILABILITY.get(name, (None, False))
            value = getattr(self._settings, attr, None) if attr else None
            availability[name] = {
                "configured": bool(value) or name == "fake",
                "key": redact_secret(str(value)) if value else "<not set>",
            }
        snapshot = redact_dict({
            "routing_enabled": bool(self._settings.PROVIDER_ROUTING_ENABLED),
            "timeout_seconds": self._timeout_seconds,
            "retry": self._retry.snapshot(),
            "circuit_breaker": {
                "failure_threshold": self._breaker_kwargs["failure_threshold"],
                "cooldown_seconds": self._breaker_kwargs["cooldown_seconds"],
                "half_open_max_calls": self._breaker_kwargs["half_open_max_calls"],
            },
            "health": {
                "window": self._health_window,
                "degraded_success_rate": float(
                    self._settings.PROVIDER_HEALTH_DEGRADED_SUCCESS_RATE),
                "unhealthy_success_rate": float(
                    self._settings.PROVIDER_HEALTH_UNHEALTHY_SUCCESS_RATE),
            },
            "providers": availability,
        })
        # Provider names are not secrets — expose them verbatim (the generic
        # redactor masks every string inside lists).
        snapshot["provider_priority"] = self._priority()
        snapshot["provider_fallbacks"] = {
            cap: list(names) for cap, names in self._fallbacks().items()
        }
        return snapshot

    def snapshot(self) -> Dict[str, Any]:
        return {
            "routing_enabled": bool(self._settings.PROVIDER_ROUTING_ENABLED),
            "active_provider": self._active_provider,
            "providers": self.provider_snapshots(),
            "metrics": self.metrics_snapshot(),
            "config": self.config_snapshot(),
        }


# ── Routed provider (BaseLLMProvider facade) ───────────────────


class RoutedProvider(BaseLLMProvider):
    """A ``BaseLLMProvider`` facade that delegates through the router.

    This is what ``LLMFactory.get_provider()`` (no name) returns when
    routing is enabled, so agents keep working unchanged while gaining
    failover, retries and circuit breakers.
    """

    def __init__(self, router: Optional[ProviderRouter] = None) -> None:
        self.router = router if router is not None else get_router()

    @property
    def provider_name(self) -> str:
        return "routed"

    @property
    def default_model(self) -> str:
        return self.router.primary_default_model()

    async def chat(
        self,
        messages: List[LLMMessage],
        config: Optional[LLMConfig] = None,
    ) -> LLMResponse:
        return await self.router.chat(messages, config)

    async def chat_stream(
        self,
        messages: List[LLMMessage],
        config: Optional[LLMConfig] = None,
    ) -> AsyncIterator[str]:
        async for chunk in self.router.chat_stream(messages, config):
            yield chunk


# ── Module singletons ──────────────────────────────────────────

_router: Optional[ProviderRouter] = None
_routed_provider: Optional[RoutedProvider] = None


def reset_router() -> None:
    """Drop cached singletons (used by tests and for config reload)."""
    global _router, _routed_provider
    _router = None
    _routed_provider = None


def get_router() -> ProviderRouter:
    global _router
    if _router is None:
        _router = ProviderRouter()
    return _router


def get_routed_provider() -> RoutedProvider:
    global _routed_provider
    if _routed_provider is None:
        _routed_provider = RoutedProvider(get_router())
    return _routed_provider
