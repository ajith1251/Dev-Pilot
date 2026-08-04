"""
Tests for WebSocketManager shutdown cleanup and lifecycle.

Covers:
- close_all() with zero connections
- close_all() with connections across multiple runs
- close_all() idempotency (second call is a no-op)
- close_all() clears all internal tracking
- close_all() handles WebSocket close failures gracefully
- close_all() logs the correct count
- Lifespan shutdown path wiring
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import WebSocket

from app.services.ws_manager import WebSocketManager, ws_manager


# ═════════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════════


def _make_mock_websocket() -> MagicMock:
    """Create a mock WebSocket that behaves like a real one."""
    ws = MagicMock(spec=WebSocket)
    ws.close = AsyncMock()
    ws.send_text = AsyncMock()
    ws.accept = AsyncMock()
    return ws


# ═════════════════════════════════════════════════════════════════
# 1 — close_all() WITH ZERO CONNECTIONS
# ═════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestCloseAllNoConnections:
    """close_all() behaves safely when there are no active connections."""

    async def test_close_all_empty(self):
        """close_all() should succeed with zero connections."""
        mgr = WebSocketManager()
        await mgr.close_all()
        assert mgr.active_connections == 0

    async def test_close_all_idempotent_when_empty(self):
        """close_all() should be idempotent when called multiple times with no connections."""
        mgr = WebSocketManager()
        await mgr.close_all()
        await mgr.close_all()
        await mgr.close_all()
        assert mgr.active_connections == 0


# ═════════════════════════════════════════════════════════════════
# 2 — close_all() WITH ACTIVE CONNECTIONS
# ═════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestCloseAllWithConnections:
    """close_all() properly closes connections and clears tracking."""

    async def test_close_all_single_run(self):
        """close_all() should close all connections for a single run."""
        mgr = WebSocketManager()
        ws = _make_mock_websocket()

        await mgr.connect(ws, "RUN-001")
        assert mgr.active_connections == 1

        await mgr.close_all()
        assert mgr.active_connections == 0
        ws.close.assert_called_once_with(code=1001, reason="Server shutting down")

    async def test_close_all_multiple_runs(self):
        """close_all() should close connections across all runs."""
        mgr = WebSocketManager()
        ws1 = _make_mock_websocket()
        ws2 = _make_mock_websocket()
        ws3 = _make_mock_websocket()

        await mgr.connect(ws1, "RUN-001")
        await mgr.connect(ws2, "RUN-001")
        await mgr.connect(ws3, "RUN-002")
        assert mgr.active_connections == 3

        await mgr.close_all()
        assert mgr.active_connections == 0
        ws1.close.assert_called_once_with(code=1001, reason="Server shutting down")
        ws2.close.assert_called_once_with(code=1001, reason="Server shutting down")
        ws3.close.assert_called_once_with(code=1001, reason="Server shutting down")

    async def test_close_all_clears_internal_dict(self):
        """close_all() should clear the internal _connections dict."""
        mgr = WebSocketManager()
        ws = _make_mock_websocket()

        await mgr.connect(ws, "RUN-001")
        assert len(mgr._connections) > 0

        await mgr.close_all()
        assert len(mgr._connections) == 0

    async def test_close_all_logs_count(self):
        """close_all() should log the number of closed connections."""
        mgr = WebSocketManager()
        ws1 = _make_mock_websocket()
        ws2 = _make_mock_websocket()

        await mgr.connect(ws1, "RUN-001")
        await mgr.connect(ws2, "RUN-002")

        with patch("app.services.ws_manager.logger") as mock_logger:
            await mgr.close_all()
            mock_logger.info.assert_called_once_with(
                "Closed %d WebSocket connection(s)", 2
            )


# ═════════════════════════════════════════════════════════════════
# 3 — close_all() IDEMPOTENCY
# ═════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestCloseAllIdempotent:
    """close_all() can be safely called multiple times."""

    async def test_close_all_twice(self):
        """Calling close_all() twice should not raise."""
        mgr = WebSocketManager()
        ws = _make_mock_websocket()

        await mgr.connect(ws, "RUN-001")
        await mgr.close_all()
        await mgr.close_all()
        assert mgr.active_connections == 0
        # close should have been called only once (first close_all)
        ws.close.assert_called_once()

    async def test_close_all_then_new_connections_work(self):
        """After close_all(), new connections should still work."""
        mgr = WebSocketManager()
        ws = _make_mock_websocket()

        await mgr.close_all()
        # Connect a new connection after close_all
        await mgr.connect(ws, "RUN-NEW")
        assert mgr.active_connections == 1

        await mgr.close_all()
        assert mgr.active_connections == 0


# ═════════════════════════════════════════════════════════════════
# 4 — close_all() ERROR HANDLING
# ═════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestCloseAllErrorHandling:
    """close_all() gracefully handles WebSocket close failures."""

    async def test_close_all_websocket_raises(self):
        """If a WebSocket.close() raises, close_all() should continue."""
        mgr = WebSocketManager()
        ws1 = _make_mock_websocket()
        ws2 = _make_mock_websocket()

        # ws1.close raises an exception
        ws1.close = AsyncMock(side_effect=RuntimeError("Connection already closed"))

        await mgr.connect(ws1, "RUN-001")
        await mgr.connect(ws2, "RUN-002")

        # Should not raise despite ws1 failing
        await mgr.close_all()
        assert mgr.active_connections == 0
        # ws2 should still be called
        ws2.close.assert_called_once_with(code=1001, reason="Server shutting down")

    async def test_close_all_multiple_failures(self):
        """close_all() should handle ALL websockets failing."""
        mgr = WebSocketManager()
        ws1 = _make_mock_websocket()
        ws2 = _make_mock_websocket()

        ws1.close = AsyncMock(side_effect=RuntimeError("Fail 1"))
        ws2.close = AsyncMock(side_effect=RuntimeError("Fail 2"))

        await mgr.connect(ws1, "RUN-001")
        await mgr.connect(ws2, "RUN-001")

        # Should not raise despite all failing
        await mgr.close_all()
        assert mgr.active_connections == 0


# ═════════════════════════════════════════════════════════════════
# 5 — close_all() INTEGRATION WITH TRACKING
# ═════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestCloseAllTracking:
    """close_all() correctly integrates with connection tracking."""

    async def test_active_connections_after_close_all(self):
        """active_connections should return 0 after close_all()."""
        mgr = WebSocketManager()
        ws1 = _make_mock_websocket()
        ws2 = _make_mock_websocket()

        await mgr.connect(ws1, "RUN-001")
        await mgr.connect(ws2, "RUN-002")
        assert mgr.active_connections == 2

        await mgr.close_all()
        assert mgr.active_connections == 0

    async def test_connect_after_close_all_then_close_all_again(self):
        """Connect → close_all → connect → close_all cycle works."""
        mgr = WebSocketManager()

        for i in range(3):
            ws = _make_mock_websocket()
            await mgr.connect(ws, f"RUN-{i:03d}")
            assert mgr.active_connections == 1
            await mgr.close_all()
            assert mgr.active_connections == 0

    async def test_close_all_with_list_watchers(self):
        """close_all() should also close __list__ watchers."""
        mgr = WebSocketManager()
        ws = _make_mock_websocket()

        await mgr.connect(ws, "__list__")
        assert mgr.active_connections == 1

        await mgr.close_all()
        assert mgr.active_connections == 0
        ws.close.assert_called_once()


# ═════════════════════════════════════════════════════════════════
# 6 — LIFESPAN SHUTDOWN WIRING
# ═════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestLifespanShutdownWiring:
    """The application lifespan should call close_all() during shutdown."""

    async def test_lifespan_calls_close_all(self):
        """main.py lifespan should call ws_manager.close_all() during shutdown.

        Note: The lifespan imports ws_manager lazily via
        `from app.services.ws_manager import ws_manager` inside the function
        body, so we patch app.services.ws_manager.ws_manager directly.
        """
        from fastapi import FastAPI

        with patch("app.services.ws_manager.ws_manager") as mock_ws:
            mock_ws.close_all = AsyncMock()
            mock_ws.active_connections = 2

            with patch("app.main.settings") as mock_settings:
                mock_settings.DATABASE_URL = None
                mock_settings.is_debug = False
                mock_settings.LOG_LEVEL = "INFO"

                from app.main import lifespan

                app = FastAPI()
                async with lifespan(app):
                    pass

                mock_ws.close_all.assert_called_once()

    async def test_lifespan_shutdown_handles_close_failure(self):
        """If close_all() raises, lifespan should handle it gracefully."""
        from fastapi import FastAPI

        with patch("app.services.ws_manager.ws_manager") as mock_ws:
            mock_ws.close_all = AsyncMock(
                side_effect=RuntimeError("WebSocket cleanup failed")
            )
            mock_ws.active_connections = 1

            with patch("app.main.settings") as mock_settings:
                mock_settings.DATABASE_URL = None
                mock_settings.is_debug = False
                mock_settings.LOG_LEVEL = "INFO"

                from app.main import lifespan

                app = FastAPI()
                async with lifespan(app):
                    pass

    async def test_lifespan_shutdown_no_database(self):
        """Lifespan shutdown should work without database configured."""
        from fastapi import FastAPI

        with patch("app.services.ws_manager.ws_manager") as mock_ws:
            mock_ws.close_all = AsyncMock()
            mock_ws.active_connections = 0

            with patch("app.main.settings") as mock_settings:
                mock_settings.DATABASE_URL = None
                mock_settings.is_debug = False
                mock_settings.LOG_LEVEL = "INFO"

                from app.main import lifespan

                app = FastAPI()
                async with lifespan(app):
                    pass

                mock_ws.close_all.assert_called_once()


# ═════════════════════════════════════════════════════════════════
# 7 — GLOBAL SINGLETON
# ═════════════════════════════════════════════════════════════════


class TestGlobalSingleton:
    """The ws_manager global singleton should be a WebSocketManager instance."""

    def test_ws_manager_is_websocketmanager(self):
        """The global ws_manager should be an instance of WebSocketManager."""
        from app.services.ws_manager import ws_manager

        assert isinstance(ws_manager, WebSocketManager)

    def test_ws_manager_has_close_all(self):
        """The global ws_manager should have close_all method."""
        from app.services.ws_manager import ws_manager

        assert hasattr(ws_manager, "close_all")


# ═════════════════════════════════════════════════════════════════
# 8 — GRAPH LIVE FEED (§19C)
# ═════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestBroadcastGraphUpdate:
    """WebSocketManager.broadcast_graph_update fans out to __graph__ watchers."""

    async def test_fans_out_to_graph_watchers_only(self):
        mgr = WebSocketManager()
        graph_ws = _make_mock_websocket()
        run_ws = _make_mock_websocket()

        await mgr.connect(graph_ws, "__graph__")
        await mgr.connect(run_ws, "RUN-UNRELATED")

        sent = await mgr.broadcast_graph_update(
            {"version": 12, "run_id": "RUN-G", "updated_nodes": ["n1"]}
        )
        assert sent == 1

        payload = json.loads(graph_ws.send_text.call_args.args[0])
        assert payload["type"] == "graph_update"
        assert payload["event_type"] == "version_incremented"
        assert payload["data"]["version"] == 12
        assert payload["data"]["run_id"] == "RUN-G"
        assert payload["data"]["updated_nodes"] == ["n1"]
        assert "timestamp" in payload

        run_ws.send_text.assert_not_called()

    async def test_no_watchers_returns_zero(self):
        mgr = WebSocketManager()
        sent = await mgr.broadcast_graph_update({"version": 1})
        assert sent == 0

    async def test_send_failure_is_tolerated(self):
        mgr = WebSocketManager()
        failing = _make_mock_websocket()
        failing.send_text = AsyncMock(side_effect=RuntimeError("socket closed"))
        await mgr.connect(failing, "__graph__")

        sent = await mgr.broadcast_graph_update({"version": 2})
        assert sent == 0
        assert mgr.active_connections == 0
        assert callable(ws_manager.close_all)
