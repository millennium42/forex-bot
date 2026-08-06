"""Travas que impedem o bot de empilhar posições.

Um sinal técnico persiste por vários ciclos. Sem esta trava o runner reabre a
mesma direção a cada minuto: em uma hora seriam 60 posições no mesmo par.
"""

from __future__ import annotations

from backend.collection.mt5_client import Position
from backend.execution.runner import BotRunner


class FakeClient:
    """Dublê do MT5Client no nível do cliente, não do terminal."""

    def __init__(self, posicoes: list[Position] | None = None) -> None:
        self._posicoes = posicoes or []

    def get_positions(self) -> list[Position]:
        return self._posicoes


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


# --- uma posição por símbolo ----------------------------------------------
def test_detecta_posicao_ja_aberta_no_simbolo() -> None:
    client = FakeClient([_posicao("EURUSD")])
    assert BotRunner._tem_posicao_aberta(client, "EURUSD") is True  # type: ignore[arg-type]
    assert BotRunner._tem_posicao_aberta(client, "GBPUSD") is False  # type: ignore[arg-type]


def test_sem_posicoes_o_simbolo_esta_livre() -> None:
    assert BotRunner._tem_posicao_aberta(FakeClient([]), "EURUSD") is False  # type: ignore[arg-type]
