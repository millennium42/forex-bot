"""História 2 — os invariantes de dados vivem no schema, não na aplicação."""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.models import (
    AuditEventType,
    AuditLog,
    Direction,
    Instrument,
    Outcome,
    Side,
    Signal,
    Trade,
    TradeStatus,
)


def _instrument(session: Session, symbol: str = "EURUSD") -> Instrument:
    inst = Instrument(symbol=symbol, digits=5, point=0.00001, contract_size=100_000)
    session.add(inst)
    session.commit()
    return inst


def _signal(session: Session, inst: Instrument, **kw: object) -> Signal:
    defaults: dict[str, object] = {
        "instrument_id": inst.id,
        "direction": Direction.BUY,
        "confidence": 0.7,
        "fused_score": 0.42,
        "weight_version": "v1",
        "inputs": {"rsi": 28.4},
    }
    defaults.update(kw)
    sig = Signal(**defaults)
    session.add(sig)
    session.commit()
    return sig


def _trade(session: Session, inst: Instrument, crid: str, **kw: object) -> Trade:
    defaults: dict[str, object] = {
        "client_request_id": crid,
        "instrument_id": inst.id,
        "side": Side.BUY,
        "status": TradeStatus.PENDING,
        "volume": 0.1,
        "stop_loss": 1.0820,
        "trading_mode": "demo",
    }
    defaults.update(kw)
    trade = Trade(**defaults)
    session.add(trade)
    session.commit()
    return trade


# --- instruments -----------------------------------------------------------
def test_symbol_e_unico(session: Session) -> None:
    _instrument(session)
    session.add(Instrument(symbol="EURUSD"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_created_at_vem_do_banco(session: Session) -> None:
    inst = _instrument(session)
    assert inst.created_at is not None


# --- trades ----------------------------------------------------------------
def test_client_request_id_e_unico(session: Session) -> None:
    """Idempotência: um retry de rede não pode virar duas posições."""
    inst = _instrument(session)
    _trade(session, inst, "req-abc")
    session.rollback()

    with pytest.raises(IntegrityError):
        _trade(session, inst, "req-abc")


def test_ordem_sem_stop_loss_e_rejeitada_pelo_banco(session: Session) -> None:
    """§4: stop loss obrigatório. NOT NULL vale mesmo se o risk manager for contornado."""
    inst = _instrument(session)
    with pytest.raises(IntegrityError):
        _trade(session, inst, "req-sem-sl", stop_loss=None)


def test_volume_precisa_ser_positivo(session: Session) -> None:
    inst = _instrument(session)
    with pytest.raises(IntegrityError):
        _trade(session, inst, "req-vol", volume=0)


def test_trade_referencia_instrumento_existente(session: Session) -> None:
    inst = _instrument(session)
    session.expunge_all()
    trade = Trade(
        client_request_id="req-fk",
        instrument_id=inst.id + 999,
        side=Side.SELL,
        status=TradeStatus.PENDING,
        volume=0.1,
        stop_loss=1.1,
        trading_mode="demo",
    )
    session.add(trade)
    with pytest.raises(IntegrityError):
        session.commit()


def test_trading_mode_e_gravado_por_linha(session: Session) -> None:
    """A config muda; o histórico do que foi executado não pode mudar junto."""
    inst = _instrument(session)
    trade = _trade(session, inst, "req-modo", trading_mode="demo")
    assert trade.trading_mode == "demo"


# --- signals ---------------------------------------------------------------
def test_confidence_fora_de_0_1_e_rejeitada(session: Session) -> None:
    inst = _instrument(session)
    with pytest.raises(IntegrityError):
        _signal(session, inst, confidence=1.5)


def test_fused_score_fora_de_menos1_1_e_rejeitado(session: Session) -> None:
    inst = _instrument(session)
    with pytest.raises(IntegrityError):
        _signal(session, inst, fused_score=-2.0)


def test_signal_guarda_versao_de_pesos_e_entradas(session: Session) -> None:
    """Sem weight_version não dá para auditar qual configuração gerou a decisão."""
    inst = _instrument(session)
    sig = _signal(session, inst, weight_version="v3", inputs={"rsi": 71.2, "macd": -0.0004})
    assert sig.weight_version == "v3"
    assert sig.inputs["macd"] == pytest.approx(-0.0004)


# --- outcomes --------------------------------------------------------------
def test_outcome_liga_previsao_a_resultado(session: Session) -> None:
    inst = _instrument(session)
    sig = _signal(session, inst)
    trade = _trade(session, inst, "req-out", signal_id=sig.id, status=TradeStatus.CLOSED)

    outcome = Outcome(
        trade_id=trade.id,
        signal_id=sig.id,
        exit_price=1.0910,
        pnl=42.5,
        pnl_pct=0.42,
        duration_seconds=3600,
        predicted_direction=Direction.BUY,
        actual_direction=Direction.BUY,
        was_correct=True,
    )
    session.add(outcome)
    session.commit()

    assert outcome.signal_id == sig.id
    assert outcome.was_correct is True


def test_um_trade_gera_um_unico_outcome(session: Session) -> None:
    """Reprocessar o fechamento não pode duplicar a estatística de performance."""
    inst = _instrument(session)
    trade = _trade(session, inst, "req-dup", status=TradeStatus.CLOSED)

    def make() -> Outcome:
        return Outcome(
            trade_id=trade.id,
            exit_price=1.09,
            pnl=1.0,
            pnl_pct=0.01,
            duration_seconds=60,
            actual_direction=Direction.BUY,
            was_correct=True,
        )

    session.add(make())
    session.commit()
    session.add(make())
    with pytest.raises(IntegrityError):
        session.commit()


# --- audit_log -------------------------------------------------------------
def test_audit_log_aceita_insert(session: Session) -> None:
    entry = AuditLog(
        event_type=AuditEventType.ORDER_REJECTED,
        client_request_id="req-xyz",
        actor="system",
        payload={"reason": "stop loss ausente"},
    )
    session.add(entry)
    session.commit()

    assert entry.id is not None
    assert entry.payload["reason"] == "stop loss ausente"


def test_audit_log_aceita_evento_sem_ordem(session: Session) -> None:
    """Kill switch e promoção de modo não pertencem a nenhuma ordem."""
    entry = AuditLog(
        event_type=AuditEventType.KILL_SWITCH_TRIGGERED,
        actor="system",
        payload={"daily_loss_pct": 5.2},
    )
    session.add(entry)
    session.commit()
    assert entry.client_request_id is None
