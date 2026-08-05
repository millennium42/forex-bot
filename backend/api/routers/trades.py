"""Endpoint para consulta de trades."""

from __future__ import annotations

import sys
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.api.dependencies import get_db
from backend.models.enums import TradeStatus
from backend.models.instrument import Instrument
from backend.models.outcome import Outcome
from backend.models.signal import Signal
from backend.models.trade import Trade

router = APIRouter(prefix="/trades", tags=["Trades"])

MAX_HISTORY_LIMIT = 500


@router.get("/")
def list_trades() -> list[dict[str, Any]]:
    """Lista posições abertas agora no MT5 (visão ao vivo)."""
    if sys.platform != "win32":
        return []

    try:
        import MetaTrader5 as mt5  # noqa: N813

        if not mt5.initialize():
            return []

        positions = mt5.positions_get()
        if not positions:
            return []

        result = []
        for p in positions:
            side = "COMPRA" if p.type == mt5.POSITION_TYPE_BUY else "VENDA"
            result.append(
                {
                    "id": f"T-{p.ticket}",
                    "pair": p.symbol,
                    "side": side,
                    "entry": p.price_open,
                    "current": p.price_current,
                    "pnl": p.profit,
                    "time": "Agora",
                }
            )
        return result
    except Exception:
        return []


class SignalSummary(BaseModel):
    """Sinal que originou o trade — pesos, scores e confiança na hora da decisão."""

    id: int
    direction: str
    confidence: float
    fused_score: float
    sentiment_score: float | None
    technical_score: float | None
    weight_version: str
    inputs: dict[str, Any]


class OutcomeSummary(BaseModel):
    """Resultado real do trade encerrado, quando existe."""

    exit_price: float
    pnl: float
    pnl_pct: float
    duration_seconds: int
    predicted_direction: str | None
    actual_direction: str
    was_correct: bool


class TradeRecord(BaseModel):
    """Uma linha da história de trades, com o sinal e o resultado embutidos.

    Evita que o dashboard precise juntar três endpoints paginados separados
    para montar a tabela de trades e o modal de detalhe.
    """

    id: int
    client_request_id: str
    pair: str
    side: str
    status: str
    volume: float
    entry_price: float | None
    stop_loss: float
    take_profit: float | None
    trading_mode: str
    opened_at: datetime | None
    closed_at: datetime | None
    created_at: datetime
    signal: SignalSummary | None
    outcome: OutcomeSummary | None


@router.get("/history", response_model=list[TradeRecord])
def list_trade_history(
    symbol: str | None = None,
    status: TradeStatus | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 200,
    db: Session = Depends(get_db),  # noqa: B008
) -> list[TradeRecord]:
    """Histórico de trades persistidos no banco, com filtros para a aba Trades.

    Sem `relationship()` nos modelos (deliberado — ver AGENTS.md): a junção é
    feita aqui, explicitamente, com `outerjoin` para sinal e resultado porque
    nem todo trade tem os dois (trade manual sem sinal; trade ainda aberto sem
    outcome).
    """
    query = (
        db.query(Trade, Instrument.symbol, Signal, Outcome)
        .join(Instrument, Trade.instrument_id == Instrument.id)
        .outerjoin(Signal, Trade.signal_id == Signal.id)
        .outerjoin(Outcome, Outcome.trade_id == Trade.id)
    )

    if symbol is not None:
        query = query.filter(Instrument.symbol == symbol)
    if status is not None:
        query = query.filter(Trade.status == status)
    if since is not None:
        query = query.filter(Trade.created_at >= since)
    if until is not None:
        query = query.filter(Trade.created_at <= until)

    rows = query.order_by(Trade.created_at.desc()).limit(min(limit, MAX_HISTORY_LIMIT)).all()

    records: list[TradeRecord] = []
    for trade, symbol_value, signal, outcome in rows:
        signal_summary = (
            SignalSummary(
                id=signal.id,
                direction=signal.direction.value,
                confidence=signal.confidence,
                fused_score=signal.fused_score,
                sentiment_score=signal.sentiment_score,
                technical_score=signal.technical_score,
                weight_version=signal.weight_version,
                inputs=signal.inputs,
            )
            if signal is not None
            else None
        )
        outcome_summary = (
            OutcomeSummary(
                exit_price=outcome.exit_price,
                pnl=outcome.pnl,
                pnl_pct=outcome.pnl_pct,
                duration_seconds=outcome.duration_seconds,
                predicted_direction=(
                    outcome.predicted_direction.value
                    if outcome.predicted_direction is not None
                    else None
                ),
                actual_direction=outcome.actual_direction.value,
                was_correct=outcome.was_correct,
            )
            if outcome is not None
            else None
        )
        records.append(
            TradeRecord(
                id=trade.id,
                client_request_id=trade.client_request_id,
                pair=symbol_value,
                side=trade.side.value,
                status=trade.status.value,
                volume=trade.volume,
                entry_price=trade.entry_price,
                stop_loss=trade.stop_loss,
                take_profit=trade.take_profit,
                trading_mode=trade.trading_mode,
                opened_at=trade.opened_at,
                closed_at=trade.closed_at,
                created_at=trade.created_at,
                signal=signal_summary,
                outcome=outcome_summary,
            )
        )
    return records
