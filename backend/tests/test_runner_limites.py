"""Travas que impedem o bot de empilhar posições.

Um sinal técnico persiste por vários ciclos. Sem estas travas o runner reabre a
mesma direção a cada minuto: em uma hora seriam 60 posições no mesmo par, e a
exposição real da conta cresce sem que nada dispare.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.orm import Session

from backend.collection.mt5_client import MT5ConnectionError, Position
from backend.config import Settings
from backend.execution.runner import BotRunner
from backend.models.enums import Side, TradeStatus
from backend.models.instrument import Instrument
from backend.models.trade import Trade

CONTRACT_SIZE = 100_000.0


class FakeClient:
    """Dublê do MT5Client no nível do cliente, não do terminal."""

    def __init__(
        self,
        posicoes: list[Position] | None = None,
        *,
        symbol_info_falha: bool = False,
    ) -> None:
        self._posicoes = posicoes or []
        self._symbol_info_falha = symbol_info_falha

    def get_positions(self) -> list[Position]:
        return self._posicoes

    def get_symbol_info(self, symbol: str) -> Any:
        if self._symbol_info_falha:
            raise MT5ConnectionError("symbol_info indisponível")
        return SimpleNamespace(symbol=symbol, volume_min=0.01, contract_size=CONTRACT_SIZE)


def _posicao(symbol: str = "EURUSD", volume: float = 0.01, preco: float = 1.1545) -> Position:
    return Position(
        ticket=1,
        identifier=1,
        symbol=symbol,
        volume=volume,
        type=0,
        sl=1.15,
        tp=1.16,
        price_open=preco,
    )


def _runner() -> BotRunner:
    return BotRunner(symbols=["EURUSD"], settings=Settings(_env_file=None, max_trades_per_day=3))


# --- exposição -------------------------------------------------------------
def test_exposicao_usa_contract_size() -> None:
    """0.01 lote de EURUSD é US$ 1.154 de exposição, não US$ 0,0115.

    Sem o contract_size a exposição sai subestimada em cinco ordens de grandeza
    e o limite de 3% do equity nunca dispara.
    """
    runner = _runner()
    client = FakeClient([_posicao()])

    pct = runner._exposicao_aberta(client, equity=100_000.0)  # type: ignore[arg-type]

    esperado = (0.01 * CONTRACT_SIZE * 1.1545) / 100_000.0 * 100
    assert pct == pytest.approx(esperado)
    assert pct == pytest.approx(1.1545, abs=1e-3)


def test_exposicao_soma_todas_as_posicoes() -> None:
    runner = _runner()
    client = FakeClient([_posicao(), _posicao("GBPUSD", preco=1.3467)])

    pct = runner._exposicao_aberta(client, equity=100_000.0)  # type: ignore[arg-type]

    assert pct == pytest.approx(1.1545 + 1.3467, abs=1e-3)


def test_exposicao_assume_contrato_padrao_quando_broker_nao_responde() -> None:
    """Subestimar exposição é o erro perigoso; ignorar a posição seria isso."""
    runner = _runner()
    client = FakeClient([_posicao()], symbol_info_falha=True)

    pct = runner._exposicao_aberta(client, equity=100_000.0)  # type: ignore[arg-type]

    assert pct == pytest.approx(1.1545, abs=1e-3)


def test_equity_nao_positivo_barra_tudo() -> None:
    runner = _runner()
    assert runner._exposicao_aberta(FakeClient([]), equity=0.0) == 100.0  # type: ignore[arg-type]


# --- uma posição por símbolo ----------------------------------------------
def test_detecta_posicao_ja_aberta_no_simbolo() -> None:
    client = FakeClient([_posicao("EURUSD")])
    assert BotRunner._tem_posicao_aberta(client, "EURUSD") is True  # type: ignore[arg-type]
    assert BotRunner._tem_posicao_aberta(client, "GBPUSD") is False  # type: ignore[arg-type]


def test_sem_posicoes_o_simbolo_esta_livre() -> None:
    assert BotRunner._tem_posicao_aberta(FakeClient([]), "EURUSD") is False  # type: ignore[arg-type]


# --- teto diário -----------------------------------------------------------
def _trade(session: Session, inst: Instrument, crid: str, **kw: Any) -> Trade:
    base: dict[str, Any] = {
        "client_request_id": crid,
        "instrument_id": inst.id,
        "side": Side.BUY,
        "status": TradeStatus.OPEN,
        "volume": 0.01,
        "stop_loss": 1.0,
        "trading_mode": "demo",
    }
    base.update(kw)
    trade = Trade(**base)
    session.add(trade)
    session.commit()
    return trade


def test_conta_trades_do_dia(session: Session) -> None:
    inst = Instrument(symbol="EURUSD")
    session.add(inst)
    session.commit()

    _trade(session, inst, "a")
    _trade(session, inst, "b")

    assert BotRunner._trades_de_hoje(session) == 2


def test_ordem_rejeitada_nao_conta_no_teto(session: Session) -> None:
    """O teto é de ordens enviadas; barrada pelo risco não consumiu cota."""
    inst = Instrument(symbol="EURUSD")
    session.add(inst)
    session.commit()

    _trade(session, inst, "ok")
    _trade(session, inst, "rejeitada", status=TradeStatus.REJECTED)

    assert BotRunner._trades_de_hoje(session) == 1


def test_trade_de_ontem_nao_conta(session: Session) -> None:
    inst = Instrument(symbol="EURUSD")
    session.add(inst)
    session.commit()

    antigo = _trade(session, inst, "ontem")
    antigo.created_at = datetime.now(UTC) - timedelta(days=1)
    session.commit()

    assert BotRunner._trades_de_hoje(session) == 0
