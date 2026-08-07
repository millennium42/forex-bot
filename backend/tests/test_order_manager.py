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
    def __init__(self, retcode: int | None = 10009, fill_offset: float = 0.0) -> None:
        self.retcode = retcode
        # Diferença entre o preço pedido e o preço de preenchimento. O broker
        # real preenche a mercado, então o fill quase nunca é o preço enviado —
        # e como SL/TP são níveis absolutos, essa diferença desloca as
        # distâncias reais (ver `_realinhar_stops_ao_fill`).
        self.fill_offset = fill_offset
        self.last_order: dict[str, Any] | None = None
        self.orders: list[dict[str, Any]] = []
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

    def symbol_info(self, symbol: str) -> None:
        return None

    def symbol_select(self, symbol: str, enable: bool) -> bool:
        return True

    def positions_get(self) -> Any:
        return ()

    def history_deals_get(self, *args: Any, **kwargs: Any) -> Any:
        return ()

    def copy_rates_from_pos(self, symbol: str, timeframe: int, start_pos: int, count: int) -> Any:
        return None

    def order_send(self, request: dict[str, Any]) -> Any:
        self._action = request.get("action")
        self.last_order = request
        self.orders.append(dict(request))
        if self.retcode is None:
            return None

        preco_fill = request.get("price", 1.1) + self.fill_offset

        class Result:
            retcode = self.retcode
            order = 123
            deal = 456
            price = preco_fill
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

    t1 = order_manager.place_order(req, client_req_id, instrument.id, 10000.0, 0.0)
    t2 = order_manager.place_order(req, client_req_id, instrument.id, 10000.0, 0.0)

    assert t1 is not None
    assert t2 is not None
    assert t1.id == t2.id


def test_place_order_risk_rejected(session: Session, order_manager: OrderManager) -> None:
    """Volume acima do teto de tamanho é o motivo de rejeição aqui.

    Risco por trade e exposição agregada não bloqueiam mais ordem (história
    32). O teto que resta é `OrderRequest.max_volume`, calculado pelo runner a
    partir da confiança do sinal (história 46) e validado no gate — um volume
    de 10.0 contra um teto de 2.0 exercita esse caminho.
    """
    # O instrumento precisa existir: desde a história 32 a rejeição também
    # grava um `Trade` com status REJECTED, e ele referencia `instrument_id`.
    instrument = Instrument(symbol="EURUSD", digits=5)
    session.add(instrument)
    session.commit()

    req = OrderRequest(
        symbol="EURUSD",
        side=Side.BUY,
        volume=10.0,
        price=1.1,
        stop_loss=1.0,
        take_profit=1.2,
        max_volume=2.0,
    )

    trade = order_manager.place_order(req, "test_rej", instrument.id, 10000.0, 0.0)

    assert trade is None
    log = session.query(AuditLog).filter_by(event_type=AuditEventType.ORDER_REJECTED).first()
    assert log is not None
    assert "excede o teto" in str(log.payload.get("reason", ""))


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

    trade = order_manager.place_order(req, "test_mt5_rej", instrument.id, 10000.0, 0.0)
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


# -- realinhamento de SL/TP ao preço de preenchimento -------------------------


def _order_manager_com_fill(
    session: Session, risk_manager: RiskManager, fill_offset: float
) -> tuple[OrderManager, FakeTerminal]:
    terminal = FakeTerminal(fill_offset=fill_offset)
    client = MT5Client(terminal=terminal, settings=Settings())
    client._connected = True
    return OrderManager(client, session, risk_manager), terminal


def test_stops_sao_realinhados_quando_o_fill_difere_do_pedido(
    session: Session, risk_manager: RiskManager
) -> None:
    """As DISTÂNCIAS pretendidas são preservadas em volta do preço real.

    Bug observado em produção: 2MACDSTO com RR pretendido 1.0 abriu com 1.27
    porque o fill veio 0,2 pip melhor. SL/TP são calculados do preço SOLICITADO
    mas são níveis ABSOLUTOS, então qualquer diferença no fill desloca as
    distâncias — e com elas o risco monetário real.
    """
    instrument = Instrument(symbol="EURUSD", digits=5)
    session.add(instrument)
    session.commit()

    order_manager, terminal = _order_manager_com_fill(session, risk_manager, fill_offset=0.01)

    req = OrderRequest(
        symbol="EURUSD",
        side=Side.BUY,
        volume=0.1,
        price=1.10,
        stop_loss=1.00,  # distância pretendida: 0.10
        take_profit=1.20,  # distância pretendida: 0.10
    )
    trade = order_manager.place_order(req, "req_fill", instrument.id, 10_000.0, 0.0)

    assert trade is not None
    assert trade.entry_price == pytest.approx(1.11)

    # A última ordem enviada tem que ser o SLTP com as distâncias recolocadas
    # em volta do fill — não os níveis originais.
    ultima = terminal.orders[-1]
    assert ultima["action"] == terminal.TRADE_ACTION_SLTP
    assert ultima["sl"] == pytest.approx(1.01)
    assert ultima["tp"] == pytest.approx(1.21)


def test_stops_realinhados_preservam_o_rr_pretendido(
    session: Session, risk_manager: RiskManager
) -> None:
    """O RR medido contra o preço real volta a bater com o configurado."""
    instrument = Instrument(symbol="EURUSD", digits=5)
    session.add(instrument)
    session.commit()

    order_manager, terminal = _order_manager_com_fill(session, risk_manager, fill_offset=-0.02)

    # RR pretendido 2.0: stop a 0.10, alvo a 0.20.
    req = OrderRequest(
        symbol="EURUSD",
        side=Side.SELL,
        volume=0.1,
        price=1.10,
        stop_loss=1.20,
        take_profit=0.90,
    )
    trade = order_manager.place_order(req, "req_rr", instrument.id, 10_000.0, 0.0)

    assert trade is not None
    fill = trade.entry_price
    assert fill is not None

    ultima = terminal.orders[-1]
    d_sl = abs(ultima["sl"] - fill)
    d_tp = abs(ultima["tp"] - fill)
    assert d_tp / d_sl == pytest.approx(2.0)


def test_fill_igual_ao_pedido_nao_gera_modificacao(
    session: Session, risk_manager: RiskManager
) -> None:
    """Sem diferença no fill não há ida ao broker para reescrever o mesmo número."""
    instrument = Instrument(symbol="EURUSD", digits=5)
    session.add(instrument)
    session.commit()

    order_manager, terminal = _order_manager_com_fill(session, risk_manager, fill_offset=0.0)

    req = OrderRequest(
        symbol="EURUSD",
        side=Side.BUY,
        volume=0.1,
        price=1.10,
        stop_loss=1.00,
        take_profit=1.20,
    )
    order_manager.place_order(req, "req_sem_slip", instrument.id, 10_000.0, 0.0)

    assert all(o["action"] != terminal.TRADE_ACTION_SLTP for o in terminal.orders)
