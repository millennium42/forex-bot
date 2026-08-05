"""Endpoint de WebSocket para eventos em tempo real."""
from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.api.ws_manager import manager

router = APIRouter(tags=["WebSocket"])


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """Conecta um cliente ao feed de eventos ao vivo (sinais e trades)."""
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        manager.disconnect(websocket)
