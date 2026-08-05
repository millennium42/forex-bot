"""Módulo responsável por gravar resultados (outcomes) de trades encerrados."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.enums import Direction, Side, TradeStatus
from backend.models.outcome import Outcome
from backend.models.signal import Signal
from backend.models.trade import Trade


class OutcomeError(Exception):
    """Exceção levantada para erros de validação ao gravar um outcome."""

    pass


def _utc(momento: datetime) -> datetime:
    """Garante datetime timezone-aware em UTC.

    O Postgres devolve os timestamps com tzinfo, o SQLite dos testes de unidade
    não. Subtrair um naive de um aware levanta TypeError, então a duração do
    trade quebraria só no teste — ou só em produção, dependendo de onde o valor
    nasceu.
    """
    return momento if momento.tzinfo is not None else momento.replace(tzinfo=UTC)


def record_outcome(session: Session, trade: Trade, exit_price: float, pnl: float) -> Outcome:
    """Grava o resultado real de um trade encerrado comparado com sua previsão original.

    Args:
        session: Sessão do SQLAlchemy.
        trade: Objeto Trade previamente encerrado (status=CLOSED).
        exit_price: Preço de saída do trade.
        pnl: Lucro ou prejuízo líquido realizado.

    Returns:
        Outcome persistido no banco de dados.

    Raises:
        OutcomeError: Se o trade não possuir os atributos necessários ou não estiver encerrado.
    """
    if trade.status != TradeStatus.CLOSED:
        raise OutcomeError("Trade deve estar encerrado (status=CLOSED) para gravar outcome")

    if trade.opened_at is None or trade.closed_at is None:
        raise OutcomeError("Trade precisa ter opened_at e closed_at preenchidos")

    if trade.entry_price is None:
        raise OutcomeError("Trade precisa ter entry_price preenchido")

    duration_seconds = int((_utc(trade.closed_at) - _utc(trade.opened_at)).total_seconds())
    if duration_seconds < 0:
        duration_seconds = 0

    price_diff = exit_price - trade.entry_price
    if trade.side == Side.BUY:
        pnl_pct = price_diff / trade.entry_price
    else:
        pnl_pct = -price_diff / trade.entry_price

    if pnl > 0:
        actual_direction = Direction.BUY if trade.side == Side.BUY else Direction.SELL
    elif pnl < 0:
        actual_direction = Direction.SELL if trade.side == Side.BUY else Direction.BUY
    else:
        actual_direction = Direction.HOLD

    predicted_direction = None
    if trade.signal_id is not None:
        signal = session.scalar(select(Signal).where(Signal.id == trade.signal_id))
        if signal:
            predicted_direction = signal.direction

    was_correct = predicted_direction == actual_direction if predicted_direction else pnl > 0

    outcome = Outcome(
        trade_id=trade.id,
        signal_id=trade.signal_id,
        exit_price=exit_price,
        pnl=pnl,
        pnl_pct=pnl_pct,
        duration_seconds=duration_seconds,
        predicted_direction=predicted_direction,
        actual_direction=actual_direction,
        was_correct=was_correct,
    )

    session.add(outcome)
    session.flush()

    return outcome
