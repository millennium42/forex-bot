"""Testes do Outcome Recorder (História 12)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from backend.learning.outcome_recorder import OutcomeError, record_outcome
from backend.models.enums import Direction, Side, TradeStatus
from backend.models.instrument import Instrument
from backend.models.signal import Signal
from backend.models.trade import Trade


@pytest.fixture
def base_trade(session: Session) -> Trade:
    instrument = Instrument(symbol="EURUSD", digits=5)
    session.add(instrument)
    session.flush()

    signal = Signal(
        instrument_id=instrument.id,
        direction=Direction.BUY,
        confidence=0.8,
        fused_score=0.8,
        weight_version="v1",
    )
    session.add(signal)
    session.flush()

    opened_at = datetime(2023, 1, 1, 10, 0, 0, tzinfo=UTC)
    closed_at = opened_at + timedelta(hours=1)

    trade = Trade(
        client_request_id="test_req",
        instrument_id=instrument.id,
        signal_id=signal.id,
        side=Side.BUY,
        status=TradeStatus.CLOSED,
        volume=0.1,
        entry_price=1.1000,
        stop_loss=1.0900,
        trading_mode="demo",
        opened_at=opened_at,
        closed_at=closed_at,
    )
    session.add(trade)
    session.flush()
    return trade


def test_record_outcome_buy_success(session: Session, base_trade: Trade) -> None:
    """Compra bem sucedida registra acerto e direção real BUY."""
    outcome = record_outcome(session, base_trade, exit_price=1.1050, pnl=50.0)

    assert outcome.trade_id == base_trade.id
    assert outcome.signal_id == base_trade.signal_id
    assert outcome.exit_price == 1.1050
    assert outcome.pnl == 50.0
    assert outcome.pnl_pct == pytest.approx((1.1050 - 1.1000) / 1.1000)
    assert outcome.duration_seconds == 3600
    assert outcome.actual_direction == Direction.BUY
    assert outcome.predicted_direction == Direction.BUY
    assert outcome.was_correct is True


def test_record_outcome_buy_loss(session: Session, base_trade: Trade) -> None:
    """Compra no prejuízo registra erro e direção real SELL."""
    outcome = record_outcome(session, base_trade, exit_price=1.0950, pnl=-50.0)

    assert outcome.actual_direction == Direction.SELL
    assert outcome.predicted_direction == Direction.BUY
    assert outcome.was_correct is False
    assert outcome.pnl_pct == pytest.approx((1.0950 - 1.1000) / 1.1000)


def test_record_outcome_sell_success(session: Session, base_trade: Trade) -> None:
    """Venda bem sucedida registra acerto e direção real SELL."""
    base_trade.side = Side.SELL
    signal = session.get(Signal, base_trade.signal_id)
    assert signal is not None
    signal.direction = Direction.SELL
    session.flush()

    outcome = record_outcome(session, base_trade, exit_price=1.0950, pnl=50.0)

    assert outcome.actual_direction == Direction.SELL
    assert outcome.predicted_direction == Direction.SELL
    assert outcome.was_correct is True
    assert outcome.pnl_pct == pytest.approx((1.1000 - 1.0950) / 1.1000)


def test_record_outcome_sell_loss(session: Session, base_trade: Trade) -> None:
    """Venda no prejuízo registra erro e direção real BUY."""
    base_trade.side = Side.SELL
    signal = session.get(Signal, base_trade.signal_id)
    assert signal is not None
    signal.direction = Direction.SELL
    session.flush()

    outcome = record_outcome(session, base_trade, exit_price=1.1050, pnl=-50.0)

    assert outcome.actual_direction == Direction.BUY
    assert outcome.predicted_direction == Direction.SELL
    assert outcome.was_correct is False
    assert outcome.pnl_pct == pytest.approx((1.1000 - 1.1050) / 1.1000)


def test_record_outcome_no_signal(session: Session, base_trade: Trade) -> None:
    """Trade manual (sem signal) é avaliado pelo lucro."""
    base_trade.signal_id = None
    session.flush()

    outcome = record_outcome(session, base_trade, exit_price=1.1050, pnl=50.0)

    assert outcome.predicted_direction is None
    assert outcome.actual_direction == Direction.BUY
    assert outcome.was_correct is True


def test_record_outcome_zero_pnl(session: Session, base_trade: Trade) -> None:
    """Zero PnL resulta em HOLD na direção real e erro na previsão."""
    outcome = record_outcome(session, base_trade, exit_price=1.1000, pnl=0.0)

    assert outcome.actual_direction == Direction.HOLD
    assert outcome.was_correct is False
    assert outcome.pnl_pct == 0.0


def test_record_outcome_not_closed(session: Session, base_trade: Trade) -> None:
    """Recusa gravar se o trade não estiver fechado."""
    base_trade.status = TradeStatus.OPEN
    with pytest.raises(OutcomeError, match="status=CLOSED"):
        record_outcome(session, base_trade, exit_price=1.1050, pnl=50.0)


def test_record_outcome_missing_timestamps(session: Session, base_trade: Trade) -> None:
    """Recusa gravar se faltar timestamp de entrada/saída."""
    base_trade.opened_at = None
    with pytest.raises(OutcomeError, match="opened_at e closed_at"):
        record_outcome(session, base_trade, exit_price=1.1050, pnl=50.0)


def test_record_outcome_missing_entry(session: Session, base_trade: Trade) -> None:
    """Recusa gravar se faltar preço de entrada."""
    base_trade.entry_price = None
    with pytest.raises(OutcomeError, match="entry_price preenchido"):
        record_outcome(session, base_trade, exit_price=1.1050, pnl=50.0)
