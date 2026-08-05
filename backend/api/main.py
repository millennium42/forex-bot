"""Ponto de entrada da aplicação FastAPI."""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.api.dependencies import get_db
from backend.api.rate_limiter import RateLimiter
from backend.api.routers import audit, promotion, signals, system, trades, ws
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

# Origens explícitas, não "*". Esta API dispara ordens e aciona o kill switch:
# qualquer página aberta no navegador do operador não pode falar com ela.
# `allow_origins=["*"]` com `allow_credentials=True` é, aliás, rejeitado pelos
# próprios navegadores — a combinação nunca funcionou de verdade.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:3000",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(audit.router)
app.include_router(promotion.router)
app.include_router(ws.router)
app.include_router(signals.router)
app.include_router(system.router)
app.include_router(trades.router)


@app.get("/health", tags=["System"], dependencies=[Depends(RateLimiter(requests=100, window=60))])
def health_check() -> dict[str, str]:
    """Health check básico (liveness)."""
    return {"status": "ok"}


@app.get("/ready", tags=["System"], dependencies=[Depends(RateLimiter(requests=100, window=60))])
def ready_check(db: Session = Depends(get_db)) -> dict[str, str]:  # noqa: B008
    """Health check avançado (readiness)."""
    try:
        db.execute(text("SELECT 1"))
    except Exception as e:
        raise HTTPException(status_code=503, detail="Database Unavailable") from e

    return {"status": "ok", "db": "ok"}
