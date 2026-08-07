"""
Request context — correlation IDs for structured logging (Phase 20B).

A correlation ID is generated per HTTP request (or read from the incoming
``X-Request-ID`` header), stored in a ``contextvars`` slot so every log line
emitted while the request is being served carries the same ID, and echoed on
the response header. This lets operators trace one request across provider
calls, database queries, WebSocket broadcasts, and background probes.

The contextvar is isolated per task/request by asyncio, so concurrent
requests never leak correlation IDs into each other.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar, Token
from typing import Optional

_correlation_id: ContextVar[Optional[str]] = ContextVar(
    "correlation_id", default=None
)


def new_correlation_id() -> str:
    """Generate a fresh short correlation ID (hex, collision-resistant)."""
    return uuid.uuid4().hex[:16]


def set_correlation_id(value: Optional[str]) -> Token:
    """Set the current task's correlation ID; returns a reset token."""
    return _correlation_id.set(value)


def reset_correlation_id(token: Token) -> None:
    """Restore the previous correlation ID (used in middleware finally)."""
    _correlation_id.reset(token)


def get_correlation_id() -> Optional[str]:
    """Return the current task's correlation ID (None outside a request)."""
    return _correlation_id.get()
