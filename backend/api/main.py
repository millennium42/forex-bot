"""Ponto de entrada da aplicação FastAPI."""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.api.dependencies import get_db
from backend.api.routers import promotion, signals, system, trades, ws
from backend.config import get_settings

settings = get_settings()
if settings.sentry_dsn:
    import sentry_sdk
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        traces_sample_rate=1.0,
    )

app = FastAPI(
    title="Forex Bot API",
    description="API REST e WebSocket para bot de trading",
    version="1.0.0",
)

app.include_router(promotion.router)
app.include_router(ws.router)
app.include_router(signals.router)
app.include_router(system.router)
app.include_router(trades.router)


@app.get("/health", tags=["System"])
def health_check() -> dict[str, str]:
    """Health check básico (liveness)."""
    return {"status": "ok"}


@app.get("/ready", tags=["System"])
def ready_check(db: Session = Depends(get_db)) -> dict[str, str]:  # noqa: B008
    """Health check avançado (readiness)."""
    try:
        db.execute(text("SELECT 1"))
    except Exception as e:
        raise HTTPException(status_code=503, detail="Database Unavailable") from e

    return {"status": "ok", "db": "ok"}
