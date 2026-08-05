"""O reconciliador fecha o ciclo previsão → resultado.

Quando uma posição some do broker, a explicação mais provável é que ela fechou —
no stop, no alvo ou por intervenção manual. O tracker precisa reconhecer isso,
encerrar o trade com o preço **real** do deal de saída e gravar o outcome.

Sem esse elo, trades ficam `open` para sempre, nenhum `Outcome` é criado, e o
weight optimizer e o promotion gate — que contam outcomes — nunca se alimentam.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.collection.mt5_client import MT5Client
from backend.config import Settings
from backend.execution.position_tracker import PositionTracker
from backend.models.audit_log import AuditLog
from backend.models.enums import AuditEventType, Direction, Side, TradeStatus
from backend.models.instrument import Instrument
from backend.models.outcome import Outcome
from backend.models.signal import Signal
from backend.models.trade import Trade

ABERTURA = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
FECHAMENTO = ABERTURA + timedelta(minutes=30)


class FakeTerminal:
    """Terminal sem posições abertas e com histórico de deals configurável."""

    def __init__(self, deals: list[Any] | None = None) -> None:
        self._deals = deals
        self.consultas: list[int] = []

    def initialize(self, *a: Any, **k: Any) -> bool:
        return True

    def login(self, *a: Any, **k: Any) -> bool:
        return True

    def shutdown(self) -> None: ...

    def last_error(self) -> tuple[int, str]:
        return (0, "")

    def account_info(self) -> Any:
        return None

    def symbol_info_tick(self, symbol: str) -> Any:
        return None

    def symbol_select(self, symbol: str, enable: bool) -> bool:
        return True

    def order_send(self, request: dict[str, Any]) -> Any:
        return None

    def positions_get(self) -> Any:
        return ()  # o broker não conhece mais nenhuma posição

    def copy_rates_from_pos(self, symbol: str, tf: int, start: int, count: int) -> Any:
        return None

    def history_deals_get(self, *args: Any, **kwargs: Any) -> Any:
        self.consultas.append(int(kwargs.get("position", 0)))
        return self._deals


def _deal(price: float, profit: float, entry: int = 1, minutos: int = 30) -> SimpleNamespace:
    return SimpleNamespace(
        ticket=999,
        order=555,
        position_id=456,
        symbol="EURUSD",
        price=price,
        profit=profit,
        volume=0.01,
        entry=entry,
        time=int((ABERTURA + timedelta(minutes=minutos)).timestamp()),
    )


def _tracker(session: Session, deals: list[Any] | None) -> tuple[PositionTracker, FakeTerminal]:
    terminal = FakeTerminal(deals)
    client = MT5Client(terminal=terminal, settings=Settings(_env_file=None))  # type: ignore[arg-type]
    client._connected = True
    return PositionTracker(client, session), terminal


def _trade_aberto(session: Session, *, com_sinal: bool = False, opened: bool = True) -> Trade:
    inst = Instrument(symbol="EURUSD")
    session.add(inst)
    session.commit()

    signal_id = None
    if com_sinal:
        sinal = Signal(
            instrument_id=inst.id,
            direction=Direction.BUY,
            confidence=0.6,
            fused_score=0.4,
            weight_version="v1",
            inputs={},
        )
        session.add(sinal)
        session.commit()
        signal_id = sinal.id

    trade = Trade(
        client_request_id="req-fechado",
        instrument_id=inst.id,
        signal_id=signal_id,
        side=Side.BUY,
        status=TradeStatus.OPEN,
        volume=0.01,
        entry_price=1.1000,
        stop_loss=1.0980,
        take_profit=1.1040,
        trading_mode="demo",
        mt5_position_id=456,
        opened_at=ABERTURA if opened else None,
    )
    session.add(trade)
    session.commit()
    return trade


# --------------------------------------------------------------------------- #
def test_posicao_fechada_vira_outcome(session: Session) -> None:
    trade = _trade_aberto(session, com_sinal=True)
    tracker, _ = _tracker(session, [_deal(price=1.1040, profit=4.0)])

    tracker.reconcile()
    session.refresh(trade)

    assert trade.status is TradeStatus.CLOSED
    assert trade.closed_at is not None
    # SQLite descarta o tzinfo na volta; o Postgres preserva. Comparar sem ele
    # mantém o teste válido nos dois dialetos.
    assert trade.closed_at.replace(tzinfo=None) == FECHAMENTO.replace(tzinfo=None)

    outcome = session.scalar(select(Outcome).where(Outcome.trade_id == trade.id))
    assert outcome is not None
    assert outcome.exit_price == pytest.approx(1.1040)
    assert outcome.pnl == pytest.approx(4.0)
    assert outcome.duration_seconds == 30 * 60


def test_preco_de_saida_e_o_do_deal_nao_o_alvo_configurado(session: Session) -> None:
    """Slippage e gap fazem a execução sair do nível pedido.

    Gravar o TP configurado como se fosse o preço realizado inventaria um P&L
    que nunca existiu e envenenaria o aprendizado.
    """
    trade = _trade_aberto(session)
    executado = 1.1032  # saiu antes do TP de 1.1040
    tracker, _ = _tracker(session, [_deal(price=executado, profit=3.2)])

    tracker.reconcile()

    outcome = session.scalar(select(Outcome).where(Outcome.trade_id == trade.id))
    assert outcome is not None
    assert outcome.exit_price == pytest.approx(executado)
    assert outcome.exit_price != trade.take_profit


def test_stop_batido_grava_prejuizo_e_direcao_contraria(session: Session) -> None:
    trade = _trade_aberto(session, com_sinal=True)
    tracker, _ = _tracker(session, [_deal(price=1.0980, profit=-2.0)])

    tracker.reconcile()

    outcome = session.scalar(select(Outcome).where(Outcome.trade_id == trade.id))
    assert outcome is not None
    assert outcome.pnl < 0
    assert outcome.predicted_direction is Direction.BUY
    assert outcome.actual_direction is Direction.SELL
    assert outcome.was_correct is False


def test_sem_deal_de_saida_nao_inventa_fechamento(session: Session) -> None:
    """Atraso de propagação do broker não pode virar outcome fabricado."""
    trade = _trade_aberto(session)
    tracker, _ = _tracker(session, [])

    tracker.reconcile()
    session.refresh(trade)

    assert trade.status is TradeStatus.OPEN
    assert session.scalar(select(Outcome).where(Outcome.trade_id == trade.id)) is None

    divergencia = session.scalar(
        select(AuditLog).where(AuditLog.event_type == AuditEventType.RECONCILIATION_MISMATCH)
    )
    assert divergencia is not None


def test_deal_de_abertura_nao_conta_como_saida(session: Session) -> None:
    """`entry=0` é a abertura da posição, não o fechamento."""
    trade = _trade_aberto(session)
    tracker, _ = _tracker(session, [_deal(price=1.1000, profit=0.0, entry=0)])

    tracker.reconcile()
    session.refresh(trade)

    assert trade.status is TradeStatus.OPEN


def test_fechamento_parcial_usa_o_ultimo_deal(session: Session) -> None:
    """Vários deals de saída: o que encerra a posição é o mais recente."""
    trade = _trade_aberto(session)
    tracker, _ = _tracker(
        session,
        [
            _deal(price=1.1020, profit=1.0, minutos=10),
            _deal(price=1.1035, profit=2.5, minutos=30),
        ],
    )

    tracker.reconcile()

    outcome = session.scalar(select(Outcome).where(Outcome.trade_id == trade.id))
    assert outcome is not None
    assert outcome.exit_price == pytest.approx(1.1035)


def test_trade_legado_sem_opened_at_ainda_gera_outcome(session: Session) -> None:
    """`opened_at` nulo é dado legado; descartar o outcome perderia informação real."""
    trade = _trade_aberto(session, opened=False)
    tracker, _ = _tracker(session, [_deal(price=1.1040, profit=4.0)])

    tracker.reconcile()
    session.refresh(trade)

    assert trade.opened_at is not None
    assert session.scalar(select(Outcome).where(Outcome.trade_id == trade.id)) is not None


def test_encerramento_gera_evento_de_auditoria(session: Session) -> None:
    trade = _trade_aberto(session)
    tracker, _ = _tracker(session, [_deal(price=1.1040, profit=4.0)])

    tracker.reconcile()

    evento = session.scalar(
        select(AuditLog).where(AuditLog.event_type == AuditEventType.ORDER_CLOSED)
    )
    assert evento is not None
    assert evento.payload["origem"] == "reconciliacao"
    assert evento.payload["pnl"] == pytest.approx(4.0)
    assert evento.payload["trade_id"] == trade.id


def test_reconciliacao_consulta_o_historico_da_posicao_certa(session: Session) -> None:
    _trade_aberto(session)
    tracker, terminal = _tracker(session, [_deal(price=1.1040, profit=4.0)])

    tracker.reconcile()

    assert terminal.consultas == [456]
