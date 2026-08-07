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

`BBRSIStrategy` (história 40) é a primeira estratégia de padrão gráfico:
porta fiel de `BBRSI.mq5` do repositório
[geraked/metatrader5](https://github.com/geraked/metatrader5), Bollinger(500,
2.0) + RSI(7) de reversão à média. Diferente de `TechnicalStrategy` (score
contínuo), aqui o sinal é binário — as seis condições da fonte batem ou não —
e o stop sai da própria banda, não do ATR global (ver `StrategySignal.stop_loss`).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

import pandas as pd
from ta.momentum import RSIIndicator
from ta.volatility import BollingerBands

from backend.analysis.technical_analyzer import TechnicalAnalyzer, compute_indicators
from backend.models.enums import Direction

__all__ = [
    "BBRSI_BB_DEV",
    "BBRSI_BB_LEN",
    "BBRSI_RSI_LEN",
    "BBRSI_SL_COEF",
    "STRATEGY_DIRECTION_THRESHOLD",
    "STRATEGY_REGISTRY",
    "BBRSIStrategy",
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

    `stop_loss` é `None` por padrão: o runner deriva o stop do ATR global
    (comportamento de `TechnicalStrategy`). Uma estratégia de padrão gráfico
    (ex.: `BBRSIStrategy`) que defina o próprio stop a partir da sua leitura —
    aqui, a banda de Bollinger — preenche este campo, e o runner usa esse
    valor em vez do ATR (história 40).
    """

    direction: Direction
    confidence: float
    components: dict[str, float] = field(default_factory=dict)
    score: float = 0.0
    stop_loss: float | None = None


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


# Defaults de `BBRSI.mq5` (geraked/metatrader5): BBLen=500, BBDev=2, RSILen=7,
# SLCoef=0.9. Níveis de RSI (30/50/70) são fixos na fonte, não parâmetros de input.
BBRSI_BB_LEN = 500
BBRSI_BB_DEV = 2.0
BBRSI_RSI_LEN = 7
BBRSI_SL_COEF = 0.9
_BBRSI_RSI_LOWER = 30.0
_BBRSI_RSI_MIDDLE = 50.0
_BBRSI_RSI_UPPER = 70.0


class BBRSIStrategy:
    """Reversão à média: Bollinger(500, 2.0) + RSI(7) — porta de `BBRSI.mq5`.

    A barra -1 é o último candle fechado (`iloc[-1]`, mesma convenção de
    `compute_indicators`); a barra -2 é o candle anterior a esse
    (`iloc[-2]`) — mesmo par de índices que `RSI[1]`/`RSI[2]` da fonte MQL5
    (índice 0 ali é a barra ainda em formação, fora do nosso DataFrame).

    Sinal binário, não score contínuo: as seis condições da fonte batem
    (compra ou venda) ou não batem (HOLD) — não há posição intermediária.
    """

    name = "bbrsi"

    def __init__(
        self,
        bb_len: int = BBRSI_BB_LEN,
        bb_dev: float = BBRSI_BB_DEV,
        rsi_len: int = BBRSI_RSI_LEN,
        sl_coef: float = BBRSI_SL_COEF,
    ) -> None:
        self._bb_len = bb_len
        self._bb_dev = bb_dev
        self._rsi_len = rsi_len
        self._sl_coef = sl_coef

    def evaluate(self, candles: pd.DataFrame) -> StrategySignal | None:
        close = candles["close"].astype(float)

        # BB(bb_len) só tem valor a partir do bb_len-ésimo candle; a barra -2
        # precisa da mesma janela cheia um candle antes — daí o +1.
        minimo = self._bb_len + 1
        if len(close) < minimo:
            return None

        bollinger = BollingerBands(close=close, window=self._bb_len, window_dev=self._bb_dev)
        rsi = RSIIndicator(close=close, window=self._rsi_len).rsi()
        bb_m = bollinger.bollinger_mavg()
        bb_u = bollinger.bollinger_hband()
        bb_l = bollinger.bollinger_lband()

        close_1, close_2 = float(close.iloc[-1]), float(close.iloc[-2])
        rsi_1, rsi_2 = float(rsi.iloc[-1]), float(rsi.iloc[-2])
        bb_m_1, bb_u_1, bb_l_1 = float(bb_m.iloc[-1]), float(bb_u.iloc[-1]), float(bb_l.iloc[-1])
        bb_m_2, bb_u_2, bb_l_2 = float(bb_m.iloc[-2]), float(bb_u.iloc[-2]), float(bb_l.iloc[-2])

        if any(
            math.isnan(v)
            for v in (
                close_1,
                close_2,
                rsi_1,
                rsi_2,
                bb_m_1,
                bb_u_1,
                bb_l_1,
                bb_m_2,
                bb_u_2,
                bb_l_2,
            )
        ):
            # Indicador em aquecimento apesar do comprimento mínimo (ex.: RSI
            # sem ganho/perda numa janela plana) — ausência de informação.
            return None

        largura_banda = bb_u_1 - bb_l_1
        bb_pct_1 = (close_1 - bb_l_1) / largura_banda if largura_banda > 0 else 0.5
        componentes = {"rsi_1": rsi_1, "rsi_2": rsi_2, "bb_pct_1": bb_pct_1}

        compra = (
            rsi_2 < _BBRSI_RSI_LOWER
            and close_2 < bb_l_2
            and rsi_1 > _BBRSI_RSI_LOWER
            and close_1 > bb_l_1
            and rsi_1 < _BBRSI_RSI_MIDDLE
            and close_1 < bb_m_1
        )
        if compra:
            stop_loss = bb_l_1 - self._sl_coef * (bb_m_1 - bb_l_1)
            return StrategySignal(
                direction=Direction.BUY,
                confidence=1.0,
                components=componentes,
                score=1.0,
                stop_loss=stop_loss,
            )

        venda = (
            rsi_2 > _BBRSI_RSI_UPPER
            and close_2 > bb_u_2
            and rsi_1 < _BBRSI_RSI_UPPER
            and close_1 < bb_u_1
            and rsi_1 > _BBRSI_RSI_MIDDLE
            and close_1 > bb_m_1
        )
        if venda:
            stop_loss = bb_u_1 + self._sl_coef * (bb_u_1 - bb_m_1)
            return StrategySignal(
                direction=Direction.SELL,
                confidence=1.0,
                components=componentes,
                score=-1.0,
                stop_loss=stop_loss,
            )

        return StrategySignal(
            direction=Direction.HOLD, confidence=0.0, components=componentes, score=0.0
        )


# Registro de estratégias conhecidas, chaveado pelo nome usado em
# `STRATEGIES_ENABLED` e persistido em `signals.strategy`/`trades.strategy`.
STRATEGY_REGISTRY: dict[str, Callable[[], Strategy]] = {
    "technical": TechnicalStrategy,
    "bbrsi": BBRSIStrategy,
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
