"""Travas que impedem o bot de reabrir a MESMA leitura de mercado (história 34).

Múltiplas posições no mesmo símbolo são permitidas: a trava antiga de "uma
posição por símbolo" saiu. O que continua proibido é empilhar a mesma direção
a cada ciclo enquanto o sinal técnico persistir. Direção oposta às posições já
abertas é, por definição, uma leitura diferente e nunca precisa de cooldown.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from backend.collection.mt5_client import Position
from backend.config import Settings
from backend.execution.runner import BotRunner
from backend.models.enums import Side, TradeStatus
from backend.models.instrument import Instrument
from backend.models.trade import Trade


class FakeClient:
    """Dublê do MT5Client no nível do cliente, não do terminal."""

    def __init__(self, posicoes: list[Position] | None = None) -> None:
        self._posicoes = posicoes or []

    def get_positions(self) -> list[Position]:
        return self._posicoes


def _posicao(symbol: str = "EURUSD", tipo: int = 0, preco: float = 1.1545) -> Position:
    return Position(
        ticket=1,
        identifier=1,
        symbol=symbol,
        volume=0.01,
        type=tipo,
        sl=1.15,
        tp=1.16,
        price_open=preco,
    )


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {"trading_mode": "demo", "real_trading_unlocked": False}
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


def _runner(**overrides: object) -> BotRunner:
    return BotRunner(["EURUSD"], settings=_settings(**overrides))


def _instrumento(session: Session, symbol: str = "EURUSD") -> Instrument:
    instrumento = Instrument(
        symbol=symbol,
        contract_size=100_000.0,
        min_volume=0.01,
        volume_step=0.01,
        volume_max=100.0,
    )
    session.add(instrumento)
    session.commit()
    return instrumento


def _trade(
    instrumento: Instrument,
    side: Side,
    created_at: datetime,
    status: TradeStatus = TradeStatus.OPEN,
) -> Trade:
    return Trade(
        client_request_id=f"trade-{created_at.timestamp()}-{side.value}",
        instrument_id=instrumento.id,
        side=side,
        status=status,
        volume=0.01,
        stop_loss=1.0,
        trading_mode="demo",
        created_at=created_at,
    )


# --- sem posição aberta: sempre permite -------------------------------------
def test_sem_posicao_aberta_permite_abrir(session: Session) -> None:
    runner = _runner()

    assert (
        runner._pode_abrir_posicao(session, FakeClient([]), "EURUSD", Side.BUY) is True  # type: ignore[arg-type]
    )


# --- direção oposta às abertas é sempre permitida, sem cooldown -------------
def test_direcao_oposta_a_aberta_e_sempre_permitida(session: Session) -> None:
    runner = _runner()
    client = FakeClient([_posicao("EURUSD", tipo=0)])  # BUY aberto

    assert runner._pode_abrir_posicao(session, client, "EURUSD", Side.SELL) is True  # type: ignore[arg-type]


# --- mesma direção sem Trade registrado: nada a proteger, permite ----------
def test_mesma_direcao_sem_trade_registrado_permite(session: Session) -> None:
    runner = _runner()
    client = FakeClient([_posicao("EURUSD", tipo=0)])  # BUY aberto, sem Trade no banco

    assert runner._pode_abrir_posicao(session, client, "EURUSD", Side.BUY) is True  # type: ignore[arg-type]


# --- mesma direção dentro do cooldown: bloqueia -----------------------------
def test_mesma_direcao_dentro_do_cooldown_bloqueia(session: Session) -> None:
    runner = _runner(signal_repeat_cooldown_minutes=30)
    client = FakeClient([_posicao("EURUSD", tipo=0)])
    instrumento = _instrumento(session)
    session.add(_trade(instrumento, Side.BUY, datetime.now(UTC) - timedelta(minutes=5)))
    session.commit()

    assert runner._pode_abrir_posicao(session, client, "EURUSD", Side.BUY) is False  # type: ignore[arg-type]


# --- mesma direção após o cooldown: permite ---------------------------------
def test_mesma_direcao_apos_cooldown_permite(session: Session) -> None:
    runner = _runner(signal_repeat_cooldown_minutes=30)
    client = FakeClient([_posicao("EURUSD", tipo=0)])
    instrumento = _instrumento(session)
    session.add(_trade(instrumento, Side.BUY, datetime.now(UTC) - timedelta(minutes=31)))
    session.commit()

    assert runner._pode_abrir_posicao(session, client, "EURUSD", Side.BUY) is True  # type: ignore[arg-type]


# --- Trade REJECTED não conta como sinal executado --------------------------
def test_trade_rejeitado_nao_conta_para_o_cooldown(session: Session) -> None:
    runner = _runner(signal_repeat_cooldown_minutes=30)
    client = FakeClient([_posicao("EURUSD", tipo=0)])
    instrumento = _instrumento(session)
    session.add(
        _trade(
            instrumento,
            Side.BUY,
            datetime.now(UTC) - timedelta(seconds=1),
            status=TradeStatus.REJECTED,
        )
    )
    session.commit()

    assert runner._pode_abrir_posicao(session, client, "EURUSD", Side.BUY) is True  # type: ignore[arg-type]


# --- outro símbolo não interfere ---------------------------------------------
def test_posicao_aberta_em_outro_simbolo_nao_bloqueia(session: Session) -> None:
    runner = _runner()
    client = FakeClient([_posicao("GBPUSD", tipo=0)])

    assert runner._pode_abrir_posicao(session, client, "EURUSD", Side.BUY) is True  # type: ignore[arg-type]
