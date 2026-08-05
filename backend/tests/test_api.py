"""Testes para a API FastAPI."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.api.main import app

client = TestClient(app)


def test_openapi_schema() -> None:
    """Garante que a API gera um OpenAPI (swagger) válido."""
    response = client.get("/openapi.json")
    assert response.status_code == 200, "Falha ao gerar o schema OpenAPI."
    schema = response.json()
    assert "Forex Bot API" in schema["info"]["title"]
    assert "/promotion/status" in schema["paths"]
    assert "/signals/" in schema["paths"]
    assert "/trades/" in schema["paths"]
    assert "/trades/history" in schema["paths"]
    assert "/audit/" in schema["paths"]
    assert "/health" in schema["paths"]


def test_health_check() -> None:
    """Verifica se o health check responde corretamente."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_websocket_ping_pong() -> None:
    """Testa a conexão WebSocket recebendo ping e respondendo pong."""
    with client.websocket_connect("/ws") as websocket:
        websocket.send_text("ping")
        data = websocket.receive_text()
        assert data == "pong"


def test_endpoints_exist() -> None:
    """Testa se os endpoints existem."""
    res_signals = client.get("/signals/")
    assert res_signals.status_code == 200

    res_trades = client.get("/trades/")
    assert res_trades.status_code == 200


@pytest.mark.asyncio
async def test_websocket_manager_broadcast() -> None:
    """Testa o broadcast via manager usando mocks."""
    from backend.api.ws_manager import ConnectionManager

    class FakeWebsocket:
        def __init__(self) -> None:
            self.accepted = False
            self.messages: list[dict[str, str]] = []

        async def accept(self) -> None:
            self.accepted = True

        async def send_json(self, data: dict[str, str]) -> None:
            self.messages.append(data)

    ws1 = FakeWebsocket()
    ws2 = FakeWebsocket()

    manager = ConnectionManager()
    await manager.connect(ws1)  # type: ignore[arg-type]
    await manager.connect(ws2)  # type: ignore[arg-type]

    assert len(manager.active_connections) == 2

    msg = {"event": "signal", "data": "BUY EURUSD"}
    await manager.broadcast(msg)

    assert ws1.messages == [msg]
    assert ws2.messages == [msg]

    manager.disconnect(ws1)  # type: ignore[arg-type]
    assert len(manager.active_connections) == 1
