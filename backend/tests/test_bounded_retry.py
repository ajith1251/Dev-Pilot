"""
Unit tests for the shared bounded-retry helper (Phase 19).

The three retry sites (_stage_coding, _stage_task_analysis, the autonomy
iteration loop) all delegate to ``run_bounded_retry``; these tests pin the
helper's contract so future sites get the same safety net in one line:

    * success on the first attempt -> no retry, attempts == 1
    * transient failure -> retry, on_retry fired between attempts
    * transient failure exhausted -> bounded (no extra attempt), final result
    * deterministic failure -> never retried (even with attempts left)
    * exceptions from attempt_fn propagate (environmental -> no retry)
    * max_attempts < 1 rejected
"""

from __future__ import annotations

from typing import List

import pytest

from app.services.bounded_retry import run_bounded_retry


async def _attempt_sequence(results):
    """Build an attempt_fn that returns results[attempt-1] and records calls."""
    calls: List[int] = []

    async def attempt_fn(n: int):
        calls.append(n)
        if n - 1 >= len(results):
            raise AssertionError("attempt_fn called beyond provided results")
        return results[n - 1]

    return attempt_fn, calls


def _is_ok(result) -> bool:
    return result == "ok"


def _transient(result) -> bool:
    return result == "transient-fail"


@pytest.mark.asyncio
async def test_succeeds_first_attempt_no_retry():
    attempt_fn, calls = await _attempt_sequence(["ok"])
    outcome = await run_bounded_retry(
        attempt_fn, _is_ok, _transient, max_attempts=3)

    assert outcome.result == "ok"
    assert outcome.attempts == 1
    assert outcome.retried is False
    assert calls == [1]


@pytest.mark.asyncio
async def test_retries_then_succeeds_with_on_retry():
    retries: List[int] = []

    def on_retry(n: int, result):
        retries.append((n, result))

    attempt_fn, calls = await _attempt_sequence(
        ["transient-fail", "transient-fail", "ok"])
    outcome = await run_bounded_retry(
        attempt_fn, _is_ok, _transient, max_attempts=3, on_retry=on_retry)

    assert outcome.result == "ok"
    assert outcome.attempts == 3
    assert outcome.retried is True
    assert calls == [1, 2, 3]
    # on_retry fired after attempts 1 and 2 (not after the success)
    assert [n for n, _ in retries] == [1, 2]
    assert [r for _, r in retries] == ["transient-fail", "transient-fail"]


@pytest.mark.asyncio
async def test_transient_exhaustion_is_bounded():
    retries: List[int] = []

    def on_retry(n: int, result):
        retries.append(n)

    attempt_fn, calls = await _attempt_sequence(
        ["transient-fail", "transient-fail", "transient-fail"])
    outcome = await run_bounded_retry(
        attempt_fn, _is_ok, _transient, max_attempts=2, on_retry=on_retry)

    assert calls == [1, 2], "must not exceed max_attempts"
    assert outcome.attempts == 2
    assert outcome.retried is True
    assert outcome.result == "transient-fail"
    # on_retry fired exactly attempts-1 times — never after the final
    # (exhausted) attempt, so no misleading "retrying" event after failure.
    assert retries == [1], f"on_retry must fire once (got {retries})"


@pytest.mark.asyncio
async def test_deterministic_failure_never_retried():
    """should_retry False stops immediately even with attempts remaining."""
    retries: List[int] = []

    def on_retry(n: int, result):
        retries.append(n)

    attempt_fn, calls = await _attempt_sequence(["deterministic-fail"])
    outcome = await run_bounded_retry(
        attempt_fn, _is_ok, _transient, max_attempts=3, on_retry=on_retry)

    assert calls == [1], "deterministic failure must not retry"
    assert outcome.attempts == 1
    assert outcome.retried is False
    assert outcome.result == "deterministic-fail"
    assert retries == [], "deterministic failure must not emit a retry event"


@pytest.mark.asyncio
async def test_exception_propagates_no_retry():
    """Exceptions from attempt_fn are NOT retried (environmental contract)."""
    calls: List[int] = []

    async def attempt_fn(n: int):
        calls.append(n)
        raise RuntimeError("workspace unavailable")

    with pytest.raises(RuntimeError, match="workspace unavailable"):
        await run_bounded_retry(
            attempt_fn, _is_ok, _transient, max_attempts=3)

    assert calls == [1], "an exception must not be retried"


@pytest.mark.asyncio
async def test_rejects_zero_max_attempts():
    async def attempt_fn(n: int):  # pragma: no cover - never reached
        return "ok"

    with pytest.raises(ValueError, match="max_attempts"):
        await run_bounded_retry(attempt_fn, _is_ok, _transient, max_attempts=0)


@pytest.mark.asyncio
async def test_single_attempt_bound_works():
    """max_attempts=1: one attempt, no retry possible."""
    attempt_fn, calls = await _attempt_sequence(["transient-fail"])
    outcome = await run_bounded_retry(
        attempt_fn, _is_ok, _transient, max_attempts=1)

    assert calls == [1]
    assert outcome.attempts == 1
    assert outcome.retried is False
