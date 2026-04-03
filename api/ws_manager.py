from __future__ import annotations
import logging
from collections import deque
from fastapi import WebSocket

logger = logging.getLogger(__name__)

_BUFFER_SIZE = 500   # max events kept per run (covers the full pipeline)


class ConnectionManager:
    """
    Tracks active WebSocket connections per run_id and broadcasts events.

    Events are also stored in a per-run replay buffer so that clients that
    connect after the pipeline has already started receive the full history
    immediately on connection.
    """

    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = {}
        self._buffers: dict[str, deque[dict]] = {}

    async def connect(self, run_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.setdefault(run_id, set()).add(websocket)
        logger.debug("WS connected run_id=%s clients=%d", run_id, len(self._connections[run_id]))

        # Replay all buffered events so late-joining clients catch up instantly
        for event in list(self._buffers.get(run_id, [])):
            try:
                await websocket.send_json(event)
            except Exception:
                break   # connection already gone

    def disconnect(self, run_id: str, websocket: WebSocket) -> None:
        bucket = self._connections.get(run_id)
        if bucket:
            bucket.discard(websocket)
            if not bucket:
                del self._connections[run_id]

    async def broadcast(self, run_id: str, event: dict) -> None:
        # Always buffer first so replays are complete
        self._buffers.setdefault(run_id, deque(maxlen=_BUFFER_SIZE)).append(event)

        dead: list[WebSocket] = []
        for ws in list(self._connections.get(run_id, set())):
            try:
                await ws.send_json(event)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(run_id, ws)

    def clear_buffer(self, run_id: str) -> None:
        """Free memory after a run completes."""
        self._buffers.pop(run_id, None)


# Module-level singleton shared across the app
manager = ConnectionManager()
