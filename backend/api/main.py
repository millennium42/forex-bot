"""Ponto de entrada da aplicação FastAPI."""

from __future__ import annotations

from fastapi import FastAPI

from backend.api.routers import promotion, signals, trades, ws

app = FastAPI(
    title="Forex Bot API",
    description="API REST e WebSocket para bot de trading",
    version="1.0.0",
)

app.include_router(promotion.router)
app.include_router(ws.router)
app.include_router(signals.router)
app.include_router(trades.router)


@app.get("/health", tags=["System"])
def health_check() -> dict[str, str]:
    """Health check básico."""
    return {"status": "ok"}
