"""
ASGI middleware — Phase 20B operational hardening.

``CorrelationIdMiddleware``
    Generates/accepts a correlation ID per request, makes it available to
    structured logging via ``app.core.context``, and echoes it back on the
    ``X-Request-ID`` response header.

``RequestSizeLimitMiddleware``
    Rejects request bodies larger than ``DEVPILOT_MAX_REQUEST_BODY_BYTES``
    with 413 (request limits). Enforced for both the declared
    ``Content-Length`` header and the streamed body (chunked bodies), so an
    oversized payload can never be buffered in memory.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.context import (
    get_correlation_id,
    new_correlation_id,
    reset_correlation_id,
    set_correlation_id,
)

logger = logging.getLogger("devpilot")


class CorrelationIdMiddleware:
    """Attach a correlation ID to every HTTP request + response."""

    def __init__(self, app: Callable[..., Awaitable[Any]]) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        incoming = headers.get(b"x-request-id")
        cid = incoming.decode("latin-1") if incoming else new_correlation_id()
        token = set_correlation_id(cid)

        async def send_wrapper(message: dict) -> None:
            if message.get("type") == "http.response.start":
                message["headers"] = list(message.get("headers") or []) + [
                    (b"x-request-id", cid.encode())
                ]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            reset_correlation_id(token)


class RequestBodyTooLarge(Exception):
    """Internal marker — the streamed body exceeded the configured limit."""


class RequestSizeLimitMiddleware:
    """Reject oversized request bodies with 413 before they are buffered."""

    def __init__(
        self,
        app: Callable[..., Awaitable[Any]],
        max_bytes: int,
    ) -> None:
        self.app = app
        self.max_bytes = max(1024, int(max_bytes))

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        content_length = headers.get(b"content-length")
        if content_length:
            try:
                if int(content_length) > self.max_bytes:
                    await self._reject(scope, receive, send)
                    return
            except (ValueError, TypeError):
                pass

        received = 0

        async def capped_receive() -> dict:
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise RequestBodyTooLarge()
            return message

        try:
            await self.app(scope, capped_receive, send)
        except RequestBodyTooLarge:
            await self._reject(scope, receive, send)

    async def _reject(self, scope: dict, receive: Callable, send: Callable) -> None:
        cid = get_correlation_id() or "-"
        logger.warning(
            "Rejected oversized request (limit=%d bytes) correlation_id=%s",
            self.max_bytes,
            cid,
        )
        response = JSONResponse(
            status_code=413,
            content={
                "success": False,
                "error": "RequestTooLarge",
                "message": f"Request body exceeds the {self.max_bytes} byte limit",
            },
        )
        await response(scope, receive, send)
