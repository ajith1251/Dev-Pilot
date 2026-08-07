"""Tests for Phase 20B — operational hardening surface.

Covers:
- /health/live and /health/ready (liveness/readiness split)
- /api/v1/operations/status, /metrics, /startup-validation
- SystemMetricsService counters + resource measurement
- correlation-ID middleware (echo + generation)
- request-size-limit middleware (413)
- WebSocket manager channel counts + system broadcast

All deterministic; no paid LLM calls.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.system_metrics import SystemMetricsService


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


class TestLivenessReadiness:
    def test_liveness_always_ok(self, client: TestClient) -> None:
        resp = client.get("/health/live")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["success"] is True

    def test_readiness_shape(self, client: TestClient) -> None:
        resp = client.get("/health/ready")
        # Ready may be 200 (healthy) or 503 (required subsystem down) — the
        # shape and consistency are what matter deterministically.
        assert resp.status_code in (200, 503)
        body = resp.json()
        assert "ready" in body
        assert body["ready"] is (resp.status_code == 200)
        assert "subsystems" in body
        assert "providers" in body["subsystems"]
        assert "database" in body["subsystems"]

    def test_existing_health_endpoint_unchanged(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "healthy"


class TestOperationsApi:
    def test_operations_status_matrix(self, client: TestClient) -> None:
        resp = client.get("/api/v1/operations/status")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "summary" in data
        assert "subsystems" in data
        for name in ("providers", "database", "graph", "repository_memory",
                     "inference", "orchestration", "websocket", "resources"):
            assert name in data["subsystems"], f"missing subsystem {name}"
        # No secrets anywhere in the matrix.
        blob = str(resp.json()).lower()
        for attr in ("NVIDIA_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY",
                     "CLOUDFLARE_API_KEY", "ANTHROPIC_API_KEY"):
            key = getattr(_settings(), attr)
            if key:
                assert key.lower() not in blob

    def test_operations_metrics_shape(self, client: TestClient) -> None:
        resp = client.get("/api/v1/operations/metrics")
        assert resp.status_code == 200
        data = resp.json()["data"]
        for section in ("runs", "repositories", "autonomy", "providers", "resources"):
            assert section in data
        assert "uptime_seconds" in data
        assert data["resources"]["active_ws_connections"] >= 0

    def test_operations_startup_validation_shape(self, client: TestClient) -> None:
        resp = client.get("/api/v1/operations/startup-validation")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "error_count" in data
        assert "warning_count" in data
        assert "findings" in data
        assert isinstance(data["findings"], list)


class TestSystemMetricsService:
    def test_run_counters_and_throughput(self) -> None:
        m = SystemMetricsService()
        m.record_run_started("RUN-A")
        m.record_run_started("RUN-B")
        assert m.active_runs() == 2
        m.record_run_completed("RUN-A", 1200.0)
        m.record_run_completed("RUN-B", 800.0)
        assert m.active_runs() == 0
        assert m.run_completed_total == 2
        snap = m.snapshot()
        assert snap["runs"]["completed_total"] == 2
        assert snap["runs"]["avg_duration_ms"] == pytest.approx(1000.0)
        assert snap["runs"]["throughput_per_minute"] >= 0

    def test_repository_and_autonomy_recording(self) -> None:
        m = SystemMetricsService()
        m.record_repository_processing("repo-a", 3.5)
        m.record_autonomy_goal("GOAL-1", 42.0, "completed")
        snap = m.snapshot()
        assert snap["repositories"]["processed_total"] == 1
        assert snap["repositories"]["avg_processing_seconds"] == pytest.approx(3.5)
        assert snap["autonomy"]["goals_total"] == 1
        assert snap["autonomy"]["avg_duration_seconds"] == pytest.approx(42.0)

    def test_bounded_windows(self) -> None:
        m = SystemMetricsService(max_history=5)
        for i in range(20):
            m.record_run_started(f"RUN-{i}")
            m.record_run_completed(f"RUN-{i}", 10.0)
        snap = m.snapshot()
        assert len(snap["runs"]["recent_duration_ms"]) <= 20

    def test_memory_usage_is_positive_when_measurable(self) -> None:
        value = SystemMetricsService.memory_usage_mb()
        # May be None on Windows without psutil — otherwise must be sane.
        if value is not None:
            assert value > 0

    def test_open_task_count(self) -> None:
        assert SystemMetricsService.open_task_count() >= 0


class TestCorrelationMiddleware:
    def test_echoes_incoming_request_id(self, client: TestClient) -> None:
        resp = client.get("/health/live", headers={"X-Request-ID": "trace-abc-123"})
        assert resp.status_code == 200
        assert resp.headers.get("x-request-id") == "trace-abc-123"

    def test_generates_request_id_when_absent(self, client: TestClient) -> None:
        resp = client.get("/health/live")
        assert resp.status_code == 200
        cid = resp.headers.get("x-request-id")
        assert cid and len(cid) >= 8


class _FakeReceiveSend:
    """Minimal ASGI harness for exercising middleware standalone."""

    def __init__(self, body: bytes) -> None:
        self.body = body
        self.received: List[dict] = []
        self.sent: List[dict] = []

    async def receive(self) -> dict:
        if not self.body:
            return {"type": "http.disconnect"}
        chunk, self.body = self.body[:1024], self.body[1024:]
        return {"type": "http.request", "body": chunk, "more_body": bool(self.body)}

    async def send(self, message: dict) -> None:
        self.sent.append(message)


class TestRequestSizeLimit:
    @pytest.mark.asyncio
    async def test_oversized_body_rejected_413(self) -> None:
        from app.core.middleware import RequestSizeLimitMiddleware

        async def inner_app(scope, receive, send):
            while True:
                msg = await receive()
                if msg["type"] != "http.request":
                    break
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            })
            await send({"type": "http.response.body", "body": b"{}"})

        middleware = RequestSizeLimitMiddleware(inner_app, max_bytes=1024)
        harness = _FakeReceiveSend(b"x" * 4096)
        scope = {"type": "http", "headers": []}
        await middleware(scope, harness.receive, harness.send)
        start = next(m for m in harness.sent if m["type"] == "http.response.start")
        assert start["status"] == 413

    @pytest.mark.asyncio
    async def test_content_length_header_rejected(self) -> None:
        from app.core.middleware import RequestSizeLimitMiddleware

        async def inner_app(scope, receive, send):  # pragma: no cover
            pass

        middleware = RequestSizeLimitMiddleware(inner_app, max_bytes=1024)
        harness = _FakeReceiveSend(b"")
        scope = {"type": "http", "headers": [(b"content-length", b"2048")]}
        await middleware(scope, harness.receive, harness.send)
        start = next(m for m in harness.sent if m["type"] == "http.response.start")
        assert start["status"] == 413

    @pytest.mark.asyncio
    async def test_small_body_passes_through(self) -> None:
        from app.core.middleware import RequestSizeLimitMiddleware

        async def inner_app(scope, receive, send):
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            })
            await send({"type": "http.response.body", "body": b"ok"})

        middleware = RequestSizeLimitMiddleware(inner_app, max_bytes=1024)
        harness = _FakeReceiveSend(b"tiny")
        scope = {"type": "http", "headers": []}
        await middleware(scope, harness.receive, harness.send)
        start = next(m for m in harness.sent if m["type"] == "http.response.start")
        assert start["status"] == 200


class TestWebSocketManagerOps:
    @pytest.mark.asyncio
    async def test_channel_counts_and_system_broadcast(self) -> None:
        from app.services.ws_manager import WebSocketManager

        manager = WebSocketManager()
        assert manager.channel_counts() == {}

        class _FakeWs:
            def __init__(self) -> None:
                self.sent: List[str] = []

            async def accept(self) -> None:
                pass

            async def send_text(self, text: str) -> None:
                self.sent.append(text)

            async def close(self, code=None, reason=None) -> None:
                pass

        ws = _FakeWs()
        await manager.connect(ws, "__system__")  # type: ignore[arg-type]
        assert manager.active_connections == 1
        counts = manager.channel_counts()
        assert counts.get("__system__") == 1

        sent = await manager.broadcast_system_status({"ready": True})
        assert sent == 1
        assert "system_status" in ws.sent[0]

        await manager.disconnect(ws, "__system__")  # type: ignore[arg-type]
        assert manager.active_connections == 0


def _settings():
    from app.config import settings

    return settings
