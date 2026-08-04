"""
WebSocket Manager — real-time run status broadcasting.

Tracks WebSocket connections per run_id and broadcasts
state changes (status, stage results, events) to all
connected clients for that run.

Thread-safe: uses asyncio.Lock for connection tracking.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from fastapi import WebSocket

from app.core.logging import logger


class WebSocketManager:
    """Manages WebSocket connections grouped by run_id.

    Usage:
        manager = WebSocketManager()

        # On connect
        await manager.connect(websocket, run_id)

        # On state change
        await manager.broadcast_run_update(run_id, run_data)

        # On disconnect
        manager.disconnect(websocket, run_id)
    """

    def __init__(self) -> None:
        self._connections: Dict[str, Set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, run_id: str) -> None:
        """Accept a WebSocket connection and subscribe to run updates.

        Args:
            websocket: The WebSocket connection.
            run_id: The run to subscribe to.
        """
        await websocket.accept()
        async with self._lock:
            if run_id not in self._connections:
                self._connections[run_id] = set()
            self._connections[run_id].add(websocket)
            logger.debug(
                "WebSocket connected for run %s — %d active connection(s)",
                run_id,
                len(self._connections[run_id]),
            )

    async def disconnect(self, websocket: WebSocket, run_id: str) -> None:
        """Remove a WebSocket connection.

        Args:
            websocket: The WebSocket connection to remove.
            run_id: The run it was subscribed to.
        """
        async with self._lock:
            if run_id in self._connections:
                self._connections[run_id].discard(websocket)
                if not self._connections[run_id]:
                    del self._connections[run_id]
                    logger.debug(
                        "WebSocket disconnected for run %s — no more connections",
                        run_id,
                    )
                else:
                    logger.debug(
                        "WebSocket disconnected for run %s — %d remaining",
                        run_id,
                        len(self._connections[run_id]),
                    )

    async def broadcast_run_update(
        self,
        run_id: str,
        data: Dict[str, Any],
    ) -> int:
        """Broadcast a run update to all connected clients.

        Args:
            run_id: The run to broadcast to.
            data: The update payload (e.g. run status, stage results).

        Returns:
            Number of clients the message was sent to.
        """
        async with self._lock:
            connections = self._connections.get(run_id, set()).copy()

        if not connections:
            return 0

        message = json.dumps(
            {
                "type": "run_update",
                "run_id": run_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "data": data,
            },
            default=str,
        )

        sent = 0
        for ws in connections:
            try:
                await ws.send_text(message)
                sent += 1
            except Exception as exc:
                logger.warning(
                    "Failed to send WebSocket message for run %s: %s",
                    run_id,
                    exc,
                )
                # Remove stale connection
                async with self._lock:
                    self._connections.get(run_id, set()).discard(ws)

        return sent

    async def broadcast_event(
        self,
        run_id: str,
        event_type: str,
        message: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Broadcast a lightweight event notification.

        Args:
            run_id: The run to broadcast to.
            event_type: Short event type (e.g. "stage_completed", "run_updated").
            message: Human-readable event description.
            metadata: Optional additional data.

        Returns:
            Number of clients the message was sent to.
        """
        async with self._lock:
            connections = self._connections.get(run_id, set()).copy()

        if not connections:
            return 0

        payload = json.dumps(
            {
                "type": "event",
                "run_id": run_id,
                "event_type": event_type,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "message": message,
                "metadata": metadata or {},
            },
            default=str,
        )

        sent = 0
        for ws in connections:
            try:
                await ws.send_text(payload)
                sent += 1
            except Exception as exc:
                logger.warning(
                    "Failed to send WebSocket event for run %s: %s",
                    run_id,
                    exc,
                )

        return sent

    async def broadcast_run_list(
        self,
        runs_data: List[Dict[str, Any]],
    ) -> int:
        """Broadcast an updated run list to all connected list watchers.

        List watchers subscribe with run_id="__list__".

        Args:
            runs_data: Serialized run list summaries.

        Returns:
            Number of clients the message was sent to.
        """
        async with self._lock:
            connections = self._connections.get("__list__", set()).copy()

        if not connections:
            return 0

        message = json.dumps(
            {
                "type": "run_list_update",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "data": runs_data,
            },
            default=str,
        )

        sent = 0
        for ws in connections:
            try:
                await ws.send_text(message)
                sent += 1
            except Exception:
                async with self._lock:
                    self._connections.get("__list__", set()).discard(ws)

        return sent

    async def broadcast_autonomy(
        self,
        goal_id: str,
        event_type: str,
        data: Dict[str, Any],
        message: str = "",
    ) -> int:
        """Broadcast an autonomy event to the global feed and a goal's feed.

        Global autonomy watchers subscribe with key "__autonomy__"; per-goal
        watchers subscribe with "__autonomy__:{goal_id}". Events carry a full
        status snapshot so clients stay live without polling.

        Returns:
            Number of clients the message was sent to.
        """
        async with self._lock:
            targets: Set[WebSocket] = set()
            targets.update(self._connections.get("__autonomy__", set()))
            targets.update(
                self._connections.get(f"__autonomy__:{goal_id}", set())
            )

        if not targets:
            return 0

        payload = json.dumps(
            {
                "type": "autonomy_event",
                "goal_id": goal_id,
                "event_type": event_type,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "message": message,
                "data": data,
            },
            default=str,
        )

        sent = 0
        for ws in targets:
            try:
                await ws.send_text(payload)
                sent += 1
            except Exception as exc:
                logger.warning(
                    "Failed to send autonomy WebSocket event for goal %s: %s",
                    goal_id,
                    exc,
                )

        return sent

    @property
    def active_connections(self) -> int:
        """Return the total number of active WebSocket connections."""
        count = 0
        for conns in self._connections.values():
            count += len(conns)
        return count

    async def close_all(self) -> None:
        """Close all WebSocket connections (used during shutdown)."""
        all_connections: List[WebSocket] = []
        async with self._lock:
            for conns in self._connections.values():
                all_connections.extend(conns)
            self._connections.clear()

        for ws in all_connections:
            try:
                await ws.close(code=1001, reason="Server shutting down")
            except Exception:
                pass

        logger.info("Closed %d WebSocket connection(s)", len(all_connections))


# Global singleton
ws_manager = WebSocketManager()
