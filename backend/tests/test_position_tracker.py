from typing import Any

from sqlalchemy.orm import Session

from backend.collection.mt5_client import MT5Client
from backend.config import Settings
from backend.execution.position_tracker import PositionTracker
from backend.models.audit_log import AuditLog
from backend.models.enums import AuditEventType, Side, TradeStatus
from backend.models.instrument import Instrument
from backend.models.trade import Trade


class FakeTerminalForTracker:
    def __init__(self, positions: list[Any] | None = None, fail: bool = False) -> None:
        self._positions = positions if positions is not None else []
        self._fail = fail

    def positions_get(self) -> Any:
        if self._fail:
            return None
        return self._positions

    def last_error(self) -> tuple[int, str]:
        return (2, "fake error")

    def shutdown(self) -> None:
        pass


class FakeRawPosition:
    def __init__(
        self,
        ticket: int,
        identifier: int,
        symbol: str,
        volume: float,
        type_: int,
        sl: float,
        tp: float,
        price_open: float,
    ) -> None:
        self.ticket = ticket
        self.identifier = identifier
        self.symbol = symbol
        self.volume = volume
        self.type = type_
        self.sl = sl
        self.tp = tp
        self.price_open = price_open


def test_reconcile_offline(session: Session) -> None:
    fake_term = FakeTerminalForTracker(fail=True)
    client = MT5Client(terminal=fake_term, settings=Settings())  # type: ignore
    client._connected = True
    tracker = PositionTracker(client, session)
    tracker.reconcile()

    logs = session.query(AuditLog).all()
    assert len(logs) == 0


def test_reconcile_match(session: Session) -> None:
    instrument = Instrument(symbol="EURUSD", digits=5)
    session.add(instrument)
    session.commit()

    trade = Trade(
        client_request_id="req1",
        instrument_id=instrument.id,
        side=Side.BUY,
        status=TradeStatus.OPEN,
        volume=0.1,
        stop_loss=1.0,
        trading_mode="demo",
        mt5_order_id=100,
        mt5_position_id=100,
    )
    session.add(trade)
    session.commit()

    fake_pos = FakeRawPosition(
        ticket=100,
        identifier=100,
        symbol="EURUSD",
        volume=0.1,
        type_=0,
        sl=1.0,
        tp=0.0,
        price_open=1.1,
    )
    fake_term = FakeTerminalForTracker(positions=[fake_pos])
    client = MT5Client(terminal=fake_term, settings=Settings())  # type: ignore
    client._connected = True

    tracker = PositionTracker(client, session)
    tracker.reconcile()

    logs = (
        session.query(AuditLog).filter_by(event_type=AuditEventType.RECONCILIATION_MISMATCH).all()
    )
    assert len(logs) == 0


def test_reconcile_missing_in_mt5(session: Session) -> None:
    instrument = Instrument(symbol="EURUSD", digits=5)
    session.add(instrument)
    session.commit()

    trade = Trade(
        client_request_id="req2",
        instrument_id=instrument.id,
        side=Side.BUY,
        status=TradeStatus.OPEN,
        volume=0.1,
        stop_loss=1.0,
        trading_mode="demo",
        mt5_order_id=200,
    )
    session.add(trade)
    session.commit()

    fake_term = FakeTerminalForTracker(positions=[])
    client = MT5Client(terminal=fake_term, settings=Settings())  # type: ignore
    client._connected = True

    tracker = PositionTracker(client, session)
    tracker.reconcile()

    log = (
        session.query(AuditLog).filter_by(event_type=AuditEventType.RECONCILIATION_MISMATCH).first()
    )
    assert log is not None
    assert log.payload["reason"] == "missing_in_mt5"
    assert log.payload["trade_id"] == trade.id


def test_reconcile_missing_in_db(session: Session) -> None:
    fake_pos = FakeRawPosition(
        ticket=300,
        identifier=300,
        symbol="EURUSD",
        volume=0.1,
        type_=0,
        sl=1.0,
        tp=0.0,
        price_open=1.1,
    )
    fake_term = FakeTerminalForTracker(positions=[fake_pos])
    client = MT5Client(terminal=fake_term, settings=Settings())  # type: ignore
    client._connected = True

    tracker = PositionTracker(client, session)
    tracker.reconcile()

    log = (
        session.query(AuditLog).filter_by(event_type=AuditEventType.RECONCILIATION_MISMATCH).first()
    )
    assert log is not None
    assert log.payload["reason"] == "missing_in_db"
    assert log.payload["mt5_ticket"] == 300
