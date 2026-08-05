"""Endpoint para consulta de sinais."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.api.dependencies import get_db
from backend.models.instrument import Instrument
from backend.models.signal import Signal

router = APIRouter(prefix="/signals", tags=["Signals"])

MAX_SIGNALS_LIMIT = 500


class SignalRecord(BaseModel):
    """Um sinal emitido pela fusão, com o par resolvido a partir do instrumento."""

    id: int
    pair: str
    direction: str
    confidence: float
    fused_score: float
    sentiment_score: float | None
    technical_score: float | None
    weight_version: str
    inputs: dict[str, Any]
    created_at: datetime


@router.get("/", response_model=list[SignalRecord])
def list_signals(limit: int = 50, db: Session = Depends(get_db)) -> list[SignalRecord]:  # noqa: B008
    """Lista os sinais mais recentes emitidos pelo signal fusion."""
    rows = (
        db.query(Signal, Instrument.symbol)
        .join(Instrument, Signal.instrument_id == Instrument.id)
        .order_by(Signal.created_at.desc())
        .limit(min(limit, MAX_SIGNALS_LIMIT))
        .all()
    )
    return [
        SignalRecord(
            id=signal.id,
            pair=symbol,
            direction=signal.direction.value,
            confidence=signal.confidence,
            fused_score=signal.fused_score,
            sentiment_score=signal.sentiment_score,
            technical_score=signal.technical_score,
            weight_version=signal.weight_version,
            inputs=signal.inputs,
            created_at=signal.created_at,
        )
        for signal, symbol in rows
    ]
