"""Testes dos endpoints que alimentam o dashboard rico (história 22).

Segue o padrão de `test_kill_switch.py`: engine SQLite in-memory com
StaticPool + `check_same_thread=False`, compartilhado entre a sessão de setup
e as threads do TestClient via `app.dependency_overrides`.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.dependencies import get_db
from backend.api.main import app
from backend.models import Base
from backend.models.audit_log import AuditLog
from backend.models.enums import AuditEventType, Direction, Side, TradeStatus
from backend.models.instrument import Instrument
from backend.models.outcome import Outcome
from backend.models.signal import Signal
from backend.models.trade import Trade


@pytest.fixture
def api_session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session_local = sessionmaker(bind=engine)

    def override_get_db() -> Iterator[Session]:
        db = testing_session_local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    with testing_session_local() as db:
        yield db

    app.dependency_overrides.clear()


@pytest.fixture
def client(api_session: Session) -> TestClient:
    return TestClient(app)


def _make_trade(
    session: Session,
    instrument: Instrument,
    *,
    client_request_id: str,
    status: TradeStatus = TradeStatus.CLOSED,
    signal: Signal | None = None,
) -> Trade:
    trade = Trade(
        client_request_id=client_request_id,
        instrument_id=instrument.id,
        signal_id=signal.id if signal is not None else None,
        side=Side.BUY,
        status=status,
        volume=0.01,
        entry_price=1.1000,
        stop_loss=1.0950,
        take_profit=1.1100,
        trading_mode="demo",
    )
    session.add(trade)
    session.commit()
    session.refresh(trade)
    return trade


def test_trades_history_empty_by_default(client: TestClient) -> None:
    resp = client.get("/trades/history")
    assert resp.status_code == 200
    assert resp.json() == []


def test_trades_history_filters_and_embeds_signal_and_outcome(
    client: TestClient, api_session: Session
) -> None:
    eurusd = Instrument(symbol="EURUSD", digits=5, point=0.00001, contract_size=100_000)
    gbpusd = Instrument(symbol="GBPUSD", digits=5, point=0.00001, contract_size=100_000)
    api_session.add_all([eurusd, gbpusd])
    api_session.commit()

    signal = Signal(
        instrument_id=eurusd.id,
        direction=Direction.BUY,
        confidence=0.8,
        fused_score=0.6,
        sentiment_score=0.5,
        technical_score=0.7,
        weight_version="v1",
        inputs={"rsi": 42.0},
    )
    api_session.add(signal)
    api_session.commit()
    api_session.refresh(signal)

    trade = _make_trade(
        api_session, eurusd, client_request_id="req-1", status=TradeStatus.CLOSED, signal=signal
    )
    outcome = Outcome(
        trade_id=trade.id,
        signal_id=signal.id,
        exit_price=1.1050,
        pnl=5.0,
        pnl_pct=0.0045,
        duration_seconds=120,
        predicted_direction=Direction.BUY,
        actual_direction=Direction.BUY,
        was_correct=True,
    )
    api_session.add(outcome)
    api_session.commit()

    _make_trade(api_session, gbpusd, client_request_id="req-2", status=TradeStatus.OPEN)

    resp = client.get("/trades/history")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2

    eurusd_record = next(r for r in body if r["pair"] == "EURUSD")
    assert eurusd_record["signal"]["weight_version"] == "v1"
    assert eurusd_record["outcome"]["was_correct"] is True

    gbpusd_record = next(r for r in body if r["pair"] == "GBPUSD")
    assert gbpusd_record["signal"] is None
    assert gbpusd_record["outcome"] is None

    resp_filtered = client.get("/trades/history", params={"symbol": "EURUSD"})
    assert [r["pair"] for r in resp_filtered.json()] == ["EURUSD"]

    resp_status = client.get("/trades/history", params={"status": "open"})
    assert [r["pair"] for r in resp_status.json()] == ["GBPUSD"]


def test_signals_endpoint_returns_real_data(client: TestClient, api_session: Session) -> None:
    instrument = Instrument(symbol="USDJPY", digits=3, point=0.001, contract_size=100_000)
    api_session.add(instrument)
    api_session.commit()

    signal = Signal(
        instrument_id=instrument.id,
        direction=Direction.SELL,
        confidence=0.65,
        fused_score=-0.4,
        sentiment_score=None,
        technical_score=-0.4,
        weight_version="v2",
        inputs={},
    )
    api_session.add(signal)
    api_session.commit()

    resp = client.get("/signals/", params={"limit": 10})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["pair"] == "USDJPY"
    assert body[0]["direction"] == "sell"
    assert body[0]["sentiment_score"] is None


def test_audit_endpoint_filters_by_event_type(client: TestClient, api_session: Session) -> None:
    api_session.add_all(
        [
            AuditLog(
                event_type=AuditEventType.KILL_SWITCH_TRIGGERED,
                actor="system",
                payload={"reason": "perda diaria"},
            ),
            AuditLog(
                event_type=AuditEventType.ORDER_PLACED,
                actor="system",
                payload={"client_request_id": "req-1"},
            ),
        ]
    )
    api_session.commit()

    resp = client.get("/audit/")
    assert resp.status_code == 200
    assert len(resp.json()) == 2

    resp_filtered = client.get("/audit/", params={"event_type": "kill_switch_triggered"})
    body = resp_filtered.json()
    assert len(body) == 1
    assert body[0]["event_type"] == "kill_switch_triggered"


def test_promotion_status_without_outcomes_has_null_values(client: TestClient) -> None:
    resp = client.get("/promotion/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_trades"] == 0
    win_rate_criterion = next(c for c in body["criteria"] if c["key"] == "win_rate")
    assert win_rate_criterion["value"] is None
    assert win_rate_criterion["target"] == 55.0
    assert win_rate_criterion["passed"] is False


def test_promotion_status_computes_real_metrics_from_outcomes(
    client: TestClient, api_session: Session
) -> None:
    instrument = Instrument(symbol="EURUSD", digits=5, point=0.00001, contract_size=100_000)
    api_session.add(instrument)
    api_session.commit()

    for i in range(3):
        trade = _make_trade(api_session, instrument, client_request_id=f"req-{i}")
        outcome = Outcome(
            trade_id=trade.id,
            signal_id=None,
            exit_price=1.1050,
            pnl=5.0,
            pnl_pct=0.01,
            duration_seconds=60,
            predicted_direction=None,
            actual_direction=Direction.BUY,
            was_correct=True,
        )
        api_session.add(outcome)
    api_session.commit()

    resp = client.get("/promotion/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_trades"] == 3
    win_rate_criterion = next(c for c in body["criteria"] if c["key"] == "win_rate")
    assert win_rate_criterion["value"] == 100.0
    assert win_rate_criterion["passed"] is True


# -- história 39: performance por estratégia ----------------------------------


def _signal_com_estrategia(session: Session, instrument: Instrument, strategy: str) -> Signal:
    signal = Signal(
        instrument_id=instrument.id,
        strategy=strategy,
        direction=Direction.BUY,
        confidence=0.7,
        fused_score=0.5,
        weight_version="v1.0",
        inputs={},
    )
    session.add(signal)
    session.commit()
    return signal


def _outcome_para(
    session: Session, instrument: Instrument, signal: Signal, *, pnl: float, was_correct: bool
) -> None:
    trade = Trade(
        client_request_id=f"req-{signal.id}-{pnl}",
        instrument_id=instrument.id,
        signal_id=signal.id,
        side=Side.BUY,
        status=TradeStatus.CLOSED,
        volume=0.01,
        stop_loss=1.0,
        trading_mode="demo",
    )
    session.add(trade)
    session.commit()
    outcome = Outcome(
        trade_id=trade.id,
        signal_id=signal.id,
        exit_price=1.1,
        pnl=pnl,
        pnl_pct=pnl / 100.0,
        duration_seconds=60,
        actual_direction=Direction.BUY,
        was_correct=was_correct,
    )
    session.add(outcome)
    session.commit()


def test_strategy_performance_empty_by_default(client: TestClient) -> None:
    resp = client.get("/strategies/performance")
    assert resp.status_code == 200
    assert resp.json() == []


def test_strategy_performance_agrega_por_estrategia(
    client: TestClient, api_session: Session
) -> None:
    instrument = Instrument(symbol="EURUSD", digits=5, point=0.00001, contract_size=100_000)
    api_session.add(instrument)
    api_session.commit()

    tecnica_1 = _signal_com_estrategia(api_session, instrument, "technical")
    tecnica_2 = _signal_com_estrategia(api_session, instrument, "technical")
    bbrsi = _signal_com_estrategia(api_session, instrument, "bbrsi")

    _outcome_para(api_session, instrument, tecnica_1, pnl=10.0, was_correct=True)
    _outcome_para(api_session, instrument, tecnica_2, pnl=-5.0, was_correct=False)
    _outcome_para(api_session, instrument, bbrsi, pnl=20.0, was_correct=True)

    resp = client.get("/strategies/performance")
    assert resp.status_code == 200
    body = {row["strategy"]: row for row in resp.json()}

    assert body["technical"]["trades"] == 2
    assert body["technical"]["win_rate"] == pytest.approx(50.0)
    assert body["technical"]["net_pnl"] == pytest.approx(5.0)

    assert body["bbrsi"]["trades"] == 1
    assert body["bbrsi"]["win_rate"] == pytest.approx(100.0)
    assert body["bbrsi"]["net_pnl"] == pytest.approx(20.0)
