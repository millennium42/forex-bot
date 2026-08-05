"""WebSocket connection manager para emissão de eventos ao vivo."""
from __future__ import annotations

from typing import Any

from fastapi import WebSocket


class ConnectionManager:
    """Gerencia conexões WebSocket ativas para broadcast."""

    def __init__(self) -> None:
        """Inicializa o manager sem conexões."""
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        """Aceita a conexão e a armazena."""
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a conexão da lista."""
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Emite uma mensagem para todos os clientes conectados."""
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception:
                self.disconnect(connection)


manager = ConnectionManager()
