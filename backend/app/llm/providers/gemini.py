"""Google Gemini provider (official google-genai SDK)."""

from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, TypeVar, Awaitable

from google import genai

from app.config import settings
from app.core.exceptions import LLMConfigurationError, LLMError
from app.llm.base import BaseLLMProvider, LLMConfig, LLMMessage, LLMResponse

# Retry policy for the Gemini free tier (limit: ~5 requests/minute on
# gemini-3.x-flash). 429/RESOURCE_EXHAUSTED and transient 5xx errors are
# retried with exponential backoff so bursts of agent calls still complete.
_MAX_RETRIES = 6
_BASE_BACKOFF_SECONDS = 15.0
_RETRYABLE_CODES = (429, 500, 502, 503, 504)

# Ordered failover list for the free-tier DAILY quota: each model has its own
# ~20-requests/day bucket (per-model, per-project), so falling through the
# candidates multiplies the daily budget (e.g. 3.6-flash + flash-lite + 3.5-flash
# ≈ 60 calls/day vs the ~28 a full demo needs). gemini-2.5-flash is retired for
# new users; 3.5-flash is kept as a last-resort candidate.
_CANDIDATE_MODELS = (
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
)

# How long a model's exhausted-free-tier-daily-quota marker is remembered.
# The cap resets once per day (midnight Pacific), so a 24h TTL lets
# long-lived processes (e.g. the API server) reuse a model the next day
# WITHOUT a restart — after the TTL expires the model is retried on the
# next call instead of being skipped forever. Tests override this.
_EXHAUSTION_TTL_SECONDS = 24 * 60 * 60

T = TypeVar("T")


# Substrings that indicate a *permanent* quota exhaustion (free-tier daily
# cap reached, billing/payment issue) rather than a transient per-minute
# rate limit. These are NOT retryable — the cap only resets on a schedule
# (typically midnight Pacific), so retrying would only burn backoff time.
_PERMANENT_QUOTA_MARKERS = (
    "exceeded your current quota",
    "check your plan and billing",
    "billing details",
    "per day",
    "daily",
    "payment",
)


def _is_permanent_quota(exc: Exception) -> bool:
    """True when the free-tier daily/billing quota is exhausted (not retryable)."""
    message = str(exc).lower()
    return any(marker in message for marker in _PERMANENT_QUOTA_MARKERS)


def _is_rate_limited(exc: Exception) -> bool:
    """True for retryable 429 / RESOURCE_EXHAUSTED / transient server errors.

    Permanent quota exhaustion (daily cap reached) is NOT retryable.
    """
    if _is_permanent_quota(exc):
        return False
    code = getattr(exc, "code", None)
    if isinstance(code, int) and code in _RETRYABLE_CODES:
        return True
    message = str(exc).lower()
    return any(k in message for k in (
        "resource_exhausted", "429", "quota", "rate limit",
        "too many requests", "internal", "unavailable",
    ))


class GeminiProvider(BaseLLMProvider):
    """LLM provider for Google's Gemini models.

    Uses the official `google-genai` SDK (async `client.aio.models`).
    Requires a GEMINI_API_KEY in .env (free tier available via Google AI
    Studio). Per-minute rate-limit (429) and transient errors are retried
    with exponential backoff; a model's exhausted free-tier DAILY quota
    triggers automatic failover to the next candidate model (_CANDIDATE_MODELS)
    so multi-run workflows (e.g. the live demo) survive the 20 req/day
    per-model cap.
    """

    def __init__(self) -> None:
        api_key = settings.GEMINI_API_KEY
        if not api_key:
            raise LLMConfigurationError(
                "GEMINI_API_KEY is not set. "
                "Set it in your .env file or environment."
            )
        self._client = genai.Client(api_key=api_key)
        # Models whose free-tier daily quota is exhausted (resets daily at
        # midnight Pacific). Until the marker expires (see
        # _EXHAUSTION_TTL_SECONDS) calls fail over to the next candidate with
        # remaining quota (see _CANDIDATE_MODELS). Stored as model → marked_at
        # (time.monotonic) and lazily pruned so a long-lived process recovers
        # quota after the daily reset without a restart. A single dict (rather
        # than a parallel set) keeps membership and expiry in sync by
        # construction — there is no way to add a marker without a timestamp.
        self._exhausted_at: Dict[str, float] = {}
        self._exhaustion_ttl_seconds: float = _EXHAUSTION_TTL_SECONDS

    @property
    def _exhausted_models(self) -> set:
        """Backward-compatible view of exhausted model names.

        Internal state is a single dict (model → marked_at) so membership
        and expiry can never drift; this property exposes just the names for
        any existing introspection/assertions.
        """
        return set(self._exhausted_at)

    def _prune_exhausted(self) -> None:
        """Drop exhaustion markers whose TTL has elapsed.

        The free-tier daily cap resets once per day, so markers older than
        _exhaustion_ttl_seconds no longer reflect reality: the model's bucket
        has refilled. Pruning (lazily, on every availability decision) lets a
        long-lived process recover after midnight without a restart.
        """
        now = time.monotonic()
        expired = [
            name for name, ts in self._exhausted_at.items()
            if now - ts >= self._exhaustion_ttl_seconds
        ]
        for name in expired:
            self._exhausted_at.pop(name, None)

    def _first_available(self, preferred: str) -> str:
        """Return the preferred model unless its daily quota is exhausted.

        Falls back to the first candidate with remaining quota; when every
        candidate is exhausted, returns the preferred model so the caller
        raises its clear "all quota exhausted" error. Expired exhaustion
        markers are pruned first, so a model whose daily bucket refilled
        (after the TTL / midnight reset) is tried again automatically.
        """
        self._prune_exhausted()
        if preferred not in self._exhausted_at:
            return preferred
        # Only fail over among the free-tier candidate models. An explicitly
        # requested non-candidate model (e.g. gemini-3.6-pro-preview) must not
        # be silently swapped for a flash model — it surfaces its own error.
        if preferred not in _CANDIDATE_MODELS:
            return preferred
        for name in _CANDIDATE_MODELS:
            if name not in self._exhausted_at:
                return name
        return preferred

    async def _with_retry(self, call: Callable[[str], Awaitable[T]], model: str) -> T:
        """Run an async Gemini call for a model, retrying transient errors.

        Transient per-minute 429s / 5xx are retried with exponential backoff
        (starting at _BASE_BACKOFF_SECONDS; an explicit API retry delay is
        honored). When a model's free-tier DAILY quota is exhausted (permanent),
        the model is marked exhausted and the call fails over to the next
        candidate model with fresh quota, with a reset backoff budget.
        """
        last_exc: Optional[Exception] = None
        attempt = 0
        while attempt <= _MAX_RETRIES:
            try:
                return await call(model)
            except Exception as exc:
                last_exc = exc
                if _is_permanent_quota(exc):
                    self._exhausted_at[model] = time.monotonic()
                    nxt = self._first_available(model)
                    if nxt != model:
                        print(f"  [gemini] daily quota exhausted on {model}; "
                              f"switching to {nxt}")
                        model = nxt
                        attempt = 0  # fresh rate-limit budget on the new model
                        continue
                    raise LLMError(
                        "Gemini free-tier daily quota exhausted on all "
                        f"candidate models ({', '.join(sorted(self._exhausted_at))}). "
                        "Resets at midnight Pacific. Wait for the reset, use a "
                        "different API key, or upgrade to a paid tier. Original "
                        f"error: {exc}"
                    ) from exc
                if not _is_rate_limited(exc) or attempt >= _MAX_RETRIES:
                    break
                delay = _BASE_BACKOFF_SECONDS * (2 ** attempt)
                # Honor an explicit retry-delay if the SDK exposed one.
                retry_info = getattr(exc, "retry_delay", None)
                if retry_info is not None:
                    secs = getattr(retry_info, "seconds", None) or getattr(
                        retry_info, "duration", None)
                    if isinstance(secs, (int, float)) and secs > 0:
                        delay = float(secs)
                print(f"  [gemini] rate-limited/transient ({exc}); retrying in "
                      f"{delay:.0f}s (attempt {attempt + 1}/{_MAX_RETRIES})")
                await asyncio.sleep(delay)
                attempt += 1
        raise LLMError(f"Gemini call failed: {last_exc}") from last_exc

    @property
    def provider_name(self) -> str:
        return "gemini"

    @property
    def default_model(self) -> str:
        # gemini-3.6-flash is the current stable free-tier flash model with
        # working quota (gemini-2.5-flash was retired; gemini-3.5-flash's
        # free-tier daily quota can be consumed quickly). Deliberately
        # independent of settings.LLM_MODEL (which is OpenAI-biased:
        # "gpt-4o-mini") — mirroring how AnthropicProvider hardcodes its own
        # default so a switch of provider alone just works.
        return "gemini-3.6-flash"

    def _resolve_model(self, cfg: LLMConfig) -> str:
        """Pick the model for a call, ignoring the OpenAI-sentinel default.

        Agents call provider.chat(..., config=LLMConfig(temperature=...)) with
        no model, and LLMConfig() defaults to "gpt-4o-mini" (OpenAI-specific).
        Sending that to the Gemini API would 404, so treat the sentinel value
        as "unset" and fall back to the Gemini default.
        """
        model = (cfg.model or "").strip()
        if not model or model == LLMConfig().model:
            model = self.default_model
        # Prefer the requested model, but skip any whose daily quota is
        # exhausted so calls land on a model that can still serve them.
        return self._first_available(model)

    @staticmethod
    def _to_contents(messages: List[LLMMessage]) -> list:
        """Convert DevPilot messages to google-genai contents.

        A system message is extracted separately (Gemini wants it in the
        generation config, not as a turn).
        """
        system_text = ""
        turns: List[Dict[str, Any]] = []
        for m in messages:
            if m.role == "system":
                system_text = (system_text + "\n" + m.content).strip()
                continue
            role = "model" if m.role == "assistant" else "user"
            turns.append({"role": role, "parts": [{"text": m.content}]})
        if not turns:
            turns.append({"role": "user", "parts": [{"text": ""}]})
        return turns, system_text

    async def chat(
        self,
        messages: List[LLMMessage],
        config: Optional[LLMConfig] = None,
    ) -> LLMResponse:
        cfg = config or LLMConfig()
        contents, system_text = self._to_contents(messages)
        gen_config: Dict[str, Any] = {
            "temperature": cfg.temperature,
            "max_output_tokens": cfg.max_tokens,
            "top_p": cfg.top_p,
        }
        if system_text:
            gen_config["system_instruction"] = system_text
        if cfg.stop:
            gen_config["stop_sequences"] = cfg.stop

        async def _call(model: str):
            return await self._client.aio.models.generate_content(
                model=model,
                contents=contents,
                config=gen_config,
            )

        response = await self._with_retry(_call, self._resolve_model(cfg))

        text = getattr(response, "text", None) or ""
        finish_reason = "stop"
        try:
            if response.candidates and response.candidates[0].finish_reason:
                finish_reason = str(response.candidates[0].finish_reason)
        except Exception:
            pass

        usage: Optional[Dict[str, int]] = None
        try:
            um = getattr(response, "usage_metadata", None)
            if um is not None:
                usage = {
                    "prompt_tokens": um.prompt_token_count or 0,
                    "completion_tokens": um.candidates_token_count or 0,
                    "total_tokens": um.total_token_count or 0,
                }
        except Exception:
            usage = None

        return LLMResponse(
            content=text,
            finish_reason=finish_reason,
            usage=usage,
            raw=response,
        )

    async def chat_stream(
        self,
        messages: List[LLMMessage],
        config: Optional[LLMConfig] = None,
    ) -> AsyncIterator[str]:
        cfg = config or LLMConfig()
        contents, system_text = self._to_contents(messages)
        gen_config: Dict[str, Any] = {
            "temperature": cfg.temperature,
            "max_output_tokens": cfg.max_tokens,
            "top_p": cfg.top_p,
        }
        if system_text:
            gen_config["system_instruction"] = system_text
        if cfg.stop:
            gen_config["stop_sequences"] = cfg.stop

        # NOTE: generate_content_stream is an async-generator method — the
        # HTTP request happens lazily during iteration, so the retry wrapper
        # cannot cover it (a mid-stream retry would duplicate tokens). The
        # chat() path (which agents use) has full retry-with-backoff; stream
        # errors surface as LLMError for the caller to handle.
        try:
            stream = self._client.aio.models.generate_content_stream(
                model=self._resolve_model(cfg),
                contents=contents,
                config=gen_config,
            )
            async for chunk in stream:
                text = getattr(chunk, "text", None)
                if text:
                    yield text
        except Exception as exc:
            raise LLMError(f"Gemini stream call failed: {exc}") from exc
