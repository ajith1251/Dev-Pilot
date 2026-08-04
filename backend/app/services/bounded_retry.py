"""
Shared bounded retry — Phase 19 refactor.

Generalizes the bounded retry pattern that previously lived inline in three
places (all driven by the same ~20-25% transient LLM variance on Gemini —
see docs/GEMINI_API_KEY_REPORT.md, PROJECT_STATE items 12/13):

    * ``OrchestrationService._stage_coding``          (CODING_RETRY)
    * ``OrchestrationService._stage_task_analysis``   (TASK_ANALYSIS_RETRY)
    * ``AutonomousExecutionController._run_iteration`` (RUN_RETRY)

A bounded retry is: try the operation up to ``max_attempts`` times; stop on
the first success; retry only when the failure is the *transient* signature
(``should_retry``); never retry deterministic failures; emit an observability
event between attempts. A genuinely broken pipeline fails the final attempt
too, so the gate still fails — no masking.

    outcome = await run_bounded_retry(
        attempt_fn=lambda n: await work(),      # async (attempt: int) -> result
        is_success=lambda r: bool(r.patch),     # stop when True
        should_retry=lambda r: r.status != "error",  # retry only transient
        max_attempts=2,
        on_retry=lambda attempt, r: self._add_event(...),
    )
    result = outcome.result   # last attempt's result (success or final failure)
    if outcome.retried:       # at least one retry happened
        ...
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional, TypeVar

T = TypeVar("T")


@dataclass
class RetryOutcome:
    """Result of a bounded retry loop.

    Attributes:
        result: The last attempt's result — either the successful one (when
            the loop stopped because ``is_success`` became True) or the final
            failure (when attempts were exhausted or a deterministic failure
            was hit).
        attempts: Number of attempts actually made (1..max_attempts).
        retried: True when at least one retry occurred.
    """

    result: Any
    attempts: int
    retried: bool


async def run_bounded_retry(
    attempt_fn: Callable[[int], Awaitable[T]],
    is_success: Callable[[T], bool],
    should_retry: Callable[[T], bool],
    max_attempts: int,
    on_retry: Optional[Callable[[int, T], None]] = None,
) -> RetryOutcome:
    """Run ``attempt_fn`` up to ``max_attempts`` times with bounded retry.

    Semantics (identical to the three original inline loops):

    * ``attempt_fn(attempt)`` runs one attempt (attempt is 1-based).
    * Stop with success when ``is_success(result)`` is True.
    * Retry only when ``is_success`` is False AND ``should_retry(result)`` is
      True AND attempts remain — a deterministic failure (should_retry False)
      stops immediately even if attempts remain.
    * ``on_retry(attempt, result)`` (sync) is called before each retry so
      callers can emit an observability event.
    * Exceptions from ``attempt_fn`` propagate to the caller — they are NOT
      retried (environmental/hard failures fail immediately, matching the
      autonomy loop's "environmental failure never retried" contract).

    Returns:
        A ``RetryOutcome`` carrying the last result, attempts used, and
        whether any retry occurred.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")

    last: Any = None
    for attempt in range(1, max_attempts + 1):
        last = await attempt_fn(attempt)
        if is_success(last):
            return RetryOutcome(result=last, attempts=attempt,
                                retried=attempt > 1)
        if attempt < max_attempts and should_retry(last):
            if on_retry is not None:
                on_retry(attempt, last)
            continue
        return RetryOutcome(result=last, attempts=attempt,
                            retried=attempt > 1)

    # Unreachable: the loop always returns above. Kept for type-narrowing.
    return RetryOutcome(result=last, attempts=max_attempts,
                        retried=max_attempts > 1)  # pragma: no cover
