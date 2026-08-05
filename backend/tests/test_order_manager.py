import uuid
from typing import Any

import pytest
from sqlalchemy.orm import Session

from backend.collection.mt5_client import MT5Client
from backend.config import Settings
from backend.execution.order_manager import OrderManager
from backend.execution.risk_manager import OrderRequest, RiskManager
from backend.models.audit_log import AuditLog
from backend.models.enums import AuditEventType, Side, TradeStatus
from backend.models.instrument import Instrument
from backend.models.trade import Trade


class FakeTerminal:
    def __init__(self, retcode: int | None = 10009) -> None:
        self.retcode = retcode
        self.last_order: dict[str, Any] | None = None
        self._action: int | None = None

        self.TRADE_ACTION_DEAL = 1
        self.ORDER_TYPE_BUY = 0
        self.ORDER_TYPE_SELL = 1
        self.TRADE_ACTION_SLTP = 6
        self.TRADE_RETCODE_DONE = 10009
        self.TRADE_RETCODE_NO_CHANGES = 10025

    def initialize(self, *args: Any, **kwargs: Any) -> bool:
        return True

    def login(self, *args: Any, **kwargs: Any) -> bool:
        return True

    def shutdown(self) -> None:
        pass

    def last_error(self) -> tuple[int, str]:
        return (1, "fake error")

    def account_info(self) -> None:
        return None

    def symbol_info_tick(self, symbol: str) -> None:
        return None

    def symbol_select(self, symbol: str, enable: bool) -> bool:
        return True

    def order_send(self, request: dict[str, Any]) -> Any:
        self._action = request.get("action")
        self.last_order = request
        if self.retcode is None:
            return None

        class Result:
            retcode = self.retcode
            order = 123
            deal = 456
            price = request.get("price", 1.1)
            volume = request.get("volume", 0.1)
            comment = request.get("comment", "")

        return Result()


@pytest.fixture
def risk_manager() -> RiskManager:
    return RiskManager(Settings(trading_mode="demo"))


@pytest.fixture
def fake_client() -> MT5Client:
    client = MT5Client(terminal=FakeTerminal(), settings=Settings())
    client._connected = True
    return client


@pytest.fixture
def order_manager(
    session: Session, fake_client: MT5Client, risk_manager: RiskManager
) -> OrderManager:
    return OrderManager(fake_client, session, risk_manager)


def test_place_order_success(session: Session, order_manager: OrderManager) -> None:
    instrument = Instrument(symbol="EURUSD", digits=5)
    session.add(instrument)
    session.commit()

    req = OrderRequest(
        symbol="EURUSD",
        side=Side.BUY,
        volume=0.1,
        price=1.1,
        stop_loss=1.0,
        take_profit=1.2,
    )
    client_req_id = str(uuid.uuid4())

    trade = order_manager.place_order(
        request=req,
        client_request_id=client_req_id,
        instrument_id=instrument.id,
        equity=10000.0,
        daily_loss=0.0,
        current_exposure=0.0,
        trade_monetary_risk=50.0,
    )

    assert trade is not None
    assert trade.status == TradeStatus.OPEN
    assert trade.mt5_order_id == 123
    assert trade.entry_price == 1.1

    logs = session.query(AuditLog).all()
    event_types = {log.event_type for log in logs}
    assert AuditEventType.ORDER_REQUESTED in event_types
    assert AuditEventType.ORDER_PLACED in event_types


def test_place_order_idempotent(session: Session, order_manager: OrderManager) -> None:
    instrument = Instrument(symbol="EURUSD", digits=5)
    session.add(instrument)
    session.commit()

    req = OrderRequest(
        symbol="EURUSD",
        side=Side.BUY,
        volume=0.1,
        price=1.1,
        stop_loss=1.0,
        take_profit=1.2,
    )
    client_req_id = str(uuid.uuid4())

    t1 = order_manager.place_order(req, client_req_id, instrument.id, 10000.0, 0.0, 0.0, 50.0)
    t2 = order_manager.place_order(req, client_req_id, instrument.id, 10000.0, 0.0, 0.0, 50.0)

    assert t1 is not None
    assert t2 is not None
    assert t1.id == t2.id


def test_place_order_risk_rejected(session: Session, order_manager: OrderManager) -> None:
    req = OrderRequest(
        symbol="EURUSD",
        side=Side.BUY,
        volume=10.0,
        price=1.1,
        stop_loss=1.0,
        take_profit=1.2,
    )

    trade = order_manager.place_order(req, "test_rej", 1, 10000.0, 0.0, 0.0, 500.0)

    assert trade is None
    log = session.query(AuditLog).filter_by(event_type=AuditEventType.ORDER_REJECTED).first()
    assert log is not None
    assert "excede limite" in str(log.payload.get("reason", ""))


def test_place_order_mt5_rejected(
    session: Session, order_manager: OrderManager, fake_client: MT5Client
) -> None:
    instrument = Instrument(symbol="EURUSD", digits=5)
    session.add(instrument)
    session.commit()

    fake_client.terminal.retcode = 10004  # type: ignore
    req = OrderRequest(
        symbol="EURUSD",
        side=Side.BUY,
        volume=0.1,
        price=1.1,
        stop_loss=1.0,
        take_profit=1.2,
    )

    trade = order_manager.place_order(req, "test_mt5_rej", instrument.id, 10000.0, 0.0, 0.0, 50.0)
    assert trade is not None
    assert trade.status == TradeStatus.REJECTED


def test_modify_trade_success(session: Session, order_manager: OrderManager) -> None:
    instrument = Instrument(symbol="EURUSD", digits=5)
    session.add(instrument)
    session.commit()

    trade = Trade(
        client_request_id="mod_test",
        instrument_id=instrument.id,
        side=Side.BUY,
        status=TradeStatus.OPEN,
        volume=0.1,
        stop_loss=1.0,
        trading_mode="demo",
        mt5_position_id=456,
    )
    session.add(trade)
    session.commit()

    success = order_manager.modify_trade(trade, sl=1.05, tp=1.15)
    assert success is True
    assert trade.stop_loss == 1.05
    assert trade.take_profit == 1.15

    log = session.query(AuditLog).filter_by(event_type=AuditEventType.ORDER_MODIFIED).first()
    assert log is not None


def test_close_trade_success(session: Session, order_manager: OrderManager) -> None:
    instrument = Instrument(symbol="EURUSD", digits=5)
    session.add(instrument)
    session.commit()

    trade = Trade(
        client_request_id="close_test",
        instrument_id=instrument.id,
        side=Side.BUY,
        status=TradeStatus.OPEN,
        volume=0.1,
        stop_loss=1.0,
        trading_mode="demo",
        mt5_position_id=456,
    )
    session.add(trade)
    session.commit()

    success = order_manager.close_trade(trade, price=1.2)
    assert success is True
    assert trade.status == TradeStatus.CLOSED

    log = session.query(AuditLog).filter_by(event_type=AuditEventType.ORDER_CLOSED).first()
    assert log is not None
