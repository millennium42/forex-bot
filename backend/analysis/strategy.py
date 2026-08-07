"""Protocol de estratégia técnica — substitui a leitura técnica única por um
registro de estratégias independentes (história 39).

Antes desta história existia uma única leitura técnica (`technical_analyzer` +
alpha factors) por símbolo e ciclo. A partir daqui, cada estratégia habilitada
recebe o mesmo DataFrame OHLC e decide por conta própria, **sem saber das
outras** — isolamento total, tanto de lógica quanto de falha (uma estratégia
que lança exceção não derruba as demais no mesmo ciclo; ver `BotRunner`).

`TechnicalStrategy` é a leitura que já existia, preservada como a estratégia
"technical" para trás-compatibilidade — é o default de `STRATEGIES_ENABLED` e
o valor legado da coluna `signals.strategy`/`trades.strategy`. As próximas
estratégias (BBRSI, 3MACD, 2MACDSTO — histórias 40-42) implementam o mesmo
Protocol com lógica de padrão gráfico própria, sem tocar aqui.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

import pandas as pd

from backend.analysis.technical_analyzer import TechnicalAnalyzer, compute_indicators
from backend.models.enums import Direction

__all__ = [
    "STRATEGY_DIRECTION_THRESHOLD",
    "STRATEGY_REGISTRY",
    "Strategy",
    "StrategySignal",
    "TechnicalStrategy",
    "build_enabled_strategies",
]

# Mesmo limiar categórico que `signal_fusion.fuse_signals` usa por padrão: a
# direção que uma estratégia puramente técnica atribui ao seu próprio score,
# antes de qualquer fusão com sentimento, segue a mesma régua.
STRATEGY_DIRECTION_THRESHOLD = 0.1


def _direction_from_score(
    score: float, threshold: float = STRATEGY_DIRECTION_THRESHOLD
) -> Direction:
    if score >= threshold:
        return Direction.BUY
    if score <= -threshold:
        return Direction.SELL
    return Direction.HOLD


@dataclass(frozen=True, slots=True)
class StrategySignal:
    """Sinal que uma estratégia produz a partir de um único DataFrame OHLC.

    `score` acompanha `direction`/`confidence`/`components` para que o runner
    reaproveite `signal_fusion.fuse_signals` (peso técnico x peso sentimento)
    sem duplicar a aritmética de combinação — nenhuma estratégia nova precisa
    conhecer sentimento, é o runner que aplica isso por fora.
    """

    direction: Direction
    confidence: float
    components: dict[str, float] = field(default_factory=dict)
    score: float = 0.0


class Strategy(Protocol):
    """Estratégia independente: só enxerga o OHLC, nunca as outras estratégias."""

    name: str

    def evaluate(self, candles: pd.DataFrame) -> StrategySignal | None:
        """Devolve o sinal da estratégia, ou `None` quando não há setup.

        `None` é reservado para ausência de informação (série curta, indicador
        em aquecimento) — nunca para "não vale a pena operar agora", que é
        `StrategySignal(direction=Direction.HOLD, ...)`.
        """
        ...


class TechnicalStrategy:
    """A leitura técnica que já existia (histórias 7/35), agora atrás do Protocol."""

    name = "technical"

    def __init__(self, analyzer: TechnicalAnalyzer | None = None) -> None:
        self._analyzer = analyzer or TechnicalAnalyzer()

    def evaluate(self, candles: pd.DataFrame) -> StrategySignal | None:
        if compute_indicators(candles) is None:
            # Série curta ou indicador em NaN: ausência de informação, não HOLD.
            return None

        technical = self._analyzer.analyze(candles)
        return StrategySignal(
            direction=_direction_from_score(technical.score),
            confidence=technical.confidence,
            components=technical.components,
            score=technical.score,
        )


# Registro de estratégias conhecidas, chaveado pelo nome usado em
# `STRATEGIES_ENABLED` e persistido em `signals.strategy`/`trades.strategy`.
STRATEGY_REGISTRY: dict[str, Callable[[], Strategy]] = {
    "technical": TechnicalStrategy,
}


def build_enabled_strategies(names: list[str]) -> list[Strategy]:
    """Instancia as estratégias habilitadas, na ordem configurada.

    Nome desconhecido falha na construção do runner (boot), não em runtime no
    meio de um ciclo — mesma postura de `Settings._valida_timeframe`.
    """
    estrategias: list[Strategy] = []
    for nome in names:
        fabrica = STRATEGY_REGISTRY.get(nome)
        if fabrica is None:
            opcoes = ", ".join(sorted(STRATEGY_REGISTRY))
            raise ValueError(f"estrategia desconhecida: {nome!r}. Opcoes validas: {opcoes}")
        estrategias.append(fabrica())
    return estrategias
