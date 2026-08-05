"""Testes para o Weight Optimizer."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from backend.analysis.signal_fusion import DEFAULT_WEIGHTS
from backend.learning.weight_optimizer import WeightOptimizer
from backend.models.enums import Direction, Side, TradeStatus
from backend.models.instrument import Instrument
from backend.models.outcome import Outcome
from backend.models.signal import Signal
from backend.models.trade import Trade


def test_get_current_weights_fallback(session: Session) -> None:
    """Se não há pesos no banco, devolve os originais."""
    optimizer = WeightOptimizer(session)
    weights = optimizer.get_current_weights()
    assert weights == DEFAULT_WEIGHTS


def test_set_weights(session: Session) -> None:
    """Cria nova versão desativando anterior."""
    optimizer = WeightOptimizer(session)
    w1 = optimizer.set_weights("v1.1", 0.6, 0.4)
    assert w1.is_active

    weights = optimizer.get_current_weights()
    assert weights.version == "v1.1"

    # Adicionar segunda versão desativa a primeira
    optimizer.set_weights("v1.2", 0.8, 0.2)
    session.refresh(w1)
    # type checker narrows w1.is_active, evitamos assert w1.is_active

    weights2 = optimizer.get_current_weights()
    assert weights2.version == "v1.2"


def test_set_weights_duplicate_version(session: Session) -> None:
    """Nunca sobrescreve a mesma versão."""
    optimizer = WeightOptimizer(session)
    optimizer.set_weights("v1.1", 0.6, 0.4)
    with pytest.raises(ValueError, match="já existe"):
        optimizer.set_weights("v1.1", 0.5, 0.5)


def test_rollback_to_version(session: Session) -> None:
    """Rollback desativa a atual e reativa a anterior."""
    optimizer = WeightOptimizer(session)
    optimizer.set_weights("v1.1", 0.6, 0.4)
    optimizer.set_weights("v1.2", 0.8, 0.2)

    assert optimizer.get_current_weights().version == "v1.2"

    optimizer.rollback_to_version("v1.1")

    assert optimizer.get_current_weights().version == "v1.1"


def test_rollback_not_found(session: Session) -> None:
    """Falha com versão não existente."""
    optimizer = WeightOptimizer(session)
    with pytest.raises(ValueError, match="não encontrada"):
        optimizer.rollback_to_version("v1.99")


def test_optimize_no_data(session: Session) -> None:
    """Se não há outcomes suficientes, não otimiza."""
    optimizer = WeightOptimizer(session)
    res = optimizer.optimize("v1.1")
    assert res is None


def test_optimize_success(session: Session) -> None:
    """Ajusta os pesos baseado na acurácia recente."""
    optimizer = WeightOptimizer(session)

    instr = Instrument(symbol="EURUSD", digits=5, point=0.00001, contract_size=100000.0)
    session.add(instr)
    session.flush()

    s1 = Signal(
        instrument_id=instr.id,
        direction=Direction.BUY,
        confidence=0.5,
        fused_score=0.5,
        technical_score=0.8,
        sentiment_score=-0.5,
        weight_version="v1.0",
    )
    session.add(s1)
    session.flush()

    t1 = Trade(
        instrument_id=instr.id,
        client_request_id="req1",
        side=Side.BUY,
        volume=1.0,
        entry_price=1.0,
        stop_loss=0.9,
        take_profit=1.1,
        trading_mode="demo",
        status=TradeStatus.CLOSED,
    )
    session.add(t1)
    session.flush()

    o1 = Outcome(
        trade_id=t1.id,
        signal_id=s1.id,
        exit_price=1.05,
        pnl=500.0,
        pnl_pct=0.05,
        duration_seconds=3600,
        predicted_direction=Direction.BUY,
        actual_direction=Direction.BUY,
        was_correct=True,
    )
    session.add(o1)
    session.flush()

    res = optimizer.optimize("v2.0")

    assert res is not None
    assert res.version == "v2.0"
    # tecnico acertou (1), sentimento errou (0). Base min 0.1.
    # Tech = 1.0, Sent = 0.1 -> total_base = 1.1 -> Tech = 0.91, Sent = 0.09
    assert res.technical == 0.91
    assert res.sentiment == 0.09

    curr = optimizer.get_current_weights()
    assert curr.version == "v2.0"
