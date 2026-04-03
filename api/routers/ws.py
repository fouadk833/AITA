from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from api.ws_manager import manager

router = APIRouter()


@router.websocket("/ws/runs/{run_id}")
async def run_websocket(run_id: str, websocket: WebSocket) -> None:
    """
    Subscribe to live events for a specific test run.

    Messages sent by the server follow this schema:
      { "type": "connected",    "run_id": str }
      { "type": "progress",     "node": str, "status": "started"|"done"|"error", "message": str }
      { "type": "llm_token",    "agent": str, "file": str, "token": str }
      { "type": "test_saved",   "path": str, "layer": "unit"|"integration"|"e2e" }
      { "type": "run_result",   "passed": int, "failed": int, "skipped": int, "duration": float }
      { "type": "debug_result", "test_name": str, "root_cause": str, "confidence": int }
      { "type": "complete",     "status": str, "report": str }
      { "type": "error",        "message": str }

    The client may send any text message to keep the connection alive (heartbeat).
    """
    await manager.connect(run_id, websocket)
    try:
        await websocket.send_json({"type": "connected", "run_id": run_id})
        while True:
            await websocket.receive_text()   # accept heartbeat pings
    except WebSocketDisconnect:
        manager.disconnect(run_id, websocket)
