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

`ThreeMacdStrategy` (história 41) é seguidora de tendência: porta fiel de
`3MACD.mq5` do mesmo repositório, três MACDs simultâneos ((5,8), (13,21),
(34,144)). Diferente de BBRSI (condição de barra única), a fonte varre até
`BuffSize` barras à procura de um cruzamento de zero do MACD rápido seguido
de um topo/fundo do MACD médio — uma máquina de estados sobre a janela, não
uma comparação pontual. Sem stop próprio: usa o ATR global do runner.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import MACD
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
    "THREE_MACD_BUFF_SIZE",
    "THREE_MACD_M1_FAST",
    "THREE_MACD_M1_SLOW",
    "THREE_MACD_M2_FAST",
    "THREE_MACD_M2_SLOW",
    "THREE_MACD_M3_FAST",
    "THREE_MACD_M3_SLOW",
    "BBRSIStrategy",
    "Strategy",
    "StrategySignal",
    "TechnicalStrategy",
    "ThreeMacdStrategy",
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


# Defaults de `3MACD.mq5` (geraked/metatrader5): três MACDs simultâneos e
# BuffSize=32 (profundidade da varredura de topo/fundo/cruzamento de zero).
THREE_MACD_M1_FAST = 5
THREE_MACD_M1_SLOW = 8
THREE_MACD_M2_FAST = 13
THREE_MACD_M2_SLOW = 21
THREE_MACD_M3_FAST = 34
THREE_MACD_M3_SLOW = 144
THREE_MACD_BUFF_SIZE = 32


def _macd_series(close: pd.Series, fast: int, slow: int, buff_size: int) -> list[float]:
    """Linha principal do MACD (`ema_fast - ema_slow`) em convenção MQL5.

    `serie[i]` (i>=1) é a barra `i` posições atrás do último candle fechado
    (`close.iloc[-i]`) — mesma convenção de índice de `BBRSIStrategy`. O
    índice 0 fica com `nan` de propósito: nunca é lido pelas condições
    (espelha o índice 0 do array MQL5, a barra ainda em formação, que não
    existe no nosso DataFrame de candles fechados).
    """
    macd_line = MACD(close=close, window_slow=slow, window_fast=fast).macd()
    return [float("nan")] + [float(macd_line.iloc[-i]) for i in range(1, buff_size)]


def _three_macd_buy(m1: list[float], m2: list[float], m3: list[float], buff_size: int) -> bool:
    """Porta literal de `BuySignal()` em `3MACD.mq5` — dois caminhos alternativos."""
    if m3[1] > 0 and m2[1] > 0 and m2[2] > 0 and m2[3] > 0 and m2[1] > m2[2] and m2[2] < m2[3]:
        j = 0
        for i in range(2, buff_size - 1):
            if m3[i] <= 0 or m3[i + 1] <= 0:
                return False
            if m2[i] <= 0 or m2[i + 1] <= 0:
                return False
            if m1[i] < 0 and m1[i + 1] > 0:
                j = i + 1
                break
        if j == 0:
            return False

        k = 0
        for i in range(j, buff_size - 2):
            if m3[i] <= 0 or m3[i + 1] <= 0 or m3[i + 2] <= 0:
                return False
            if m2[i] <= 0 or m2[i + 1] <= 0 or m2[i + 2] <= 0:
                return False
            if m2[i] < m2[i + 1] and m2[i + 1] > m2[i + 2]:
                k = i + 1
                break
        return k != 0

    if m3[1] > 0 and m3[2] > 0 and m3[3] > 0 and m3[1] > m3[2] and m3[2] < m3[3]:
        j = 0
        for i in range(2, buff_size - 1):
            if m3[i] <= 0 or m3[i + 1] <= 0:
                return False
            if m2[i] < 0 and m2[i + 1] > 0:
                j = i + 1
                break
        if j == 0:
            return False

        k = 0
        for i in range(j, buff_size - 1):
            if m3[i] <= 0 or m3[i + 1] <= 0:
                return False
            if m2[i] <= 0 or m2[i + 1] <= 0:
                return False
            if m1[i] < 0 and m1[i + 1] > 0:
                k = i + 1
                break
        if k == 0:
            return False

        m = 0
        for i in range(k, buff_size - 2):
            if m3[i] <= 0 or m3[i + 1] <= 0 or m3[i + 2] <= 0:
                return False
            if m2[i] <= 0 or m2[i + 1] <= 0 or m2[i + 2] <= 0:
                return False
            if m2[i] < m2[i + 1] and m2[i + 1] > m2[i + 2]:
                m = i + 1
                break
        return m != 0

    return False


def _three_macd_sell(m1: list[float], m2: list[float], m3: list[float], buff_size: int) -> bool:
    """Porta literal de `SellSignal()` em `3MACD.mq5` — espelho exato da compra."""
    if m3[1] < 0 and m2[1] < 0 and m2[2] < 0 and m2[3] < 0 and m2[1] < m2[2] and m2[2] > m2[3]:
        j = 0
        for i in range(2, buff_size - 1):
            if m3[i] >= 0 or m3[i + 1] >= 0:
                return False
            if m2[i] >= 0 or m2[i + 1] >= 0:
                return False
            if m1[i] > 0 and m1[i + 1] < 0:
                j = i + 1
                break
        if j == 0:
            return False

        k = 0
        for i in range(j, buff_size - 2):
            if m3[i] >= 0 or m3[i + 1] >= 0 or m3[i + 2] >= 0:
                return False
            if m2[i] >= 0 or m2[i + 1] >= 0 or m2[i + 2] >= 0:
                return False
            if m2[i] > m2[i + 1] and m2[i + 1] < m2[i + 2]:
                k = i + 1
                break
        return k != 0

    if m3[1] < 0 and m3[2] < 0 and m3[3] < 0 and m3[1] < m3[2] and m3[2] > m3[3]:
        j = 0
        for i in range(2, buff_size - 1):
            if m3[i] >= 0 or m3[i + 1] >= 0:
                return False
            if m2[i] > 0 and m2[i + 1] < 0:
                j = i + 1
                break
        if j == 0:
            return False

        k = 0
        for i in range(j, buff_size - 1):
            if m3[i] >= 0 or m3[i + 1] >= 0:
                return False
            if m2[i] >= 0 or m2[i + 1] >= 0:
                return False
            if m1[i] > 0 and m1[i + 1] < 0:
                k = i + 1
                break
        if k == 0:
            return False

        m = 0
        for i in range(k, buff_size - 2):
            if m3[i] >= 0 or m3[i + 1] >= 0 or m3[i + 2] >= 0:
                return False
            if m2[i] >= 0 or m2[i + 1] >= 0 or m2[i + 2] >= 0:
                return False
            if m2[i] > m2[i + 1] and m2[i + 1] < m2[i + 2]:
                m = i + 1
                break
        return m != 0

    return False


class ThreeMacdStrategy:
    """Seguidora de tendência: três MACDs simultâneos — porta de `3MACD.mq5`.

    Sinal binário, como `BBRSIStrategy`: as condições da fonte batem (compra
    ou venda) ou não (HOLD). Sem stop próprio — `StrategySignal.stop_loss`
    fica `None` e o runner deriva do ATR global, comportamento padrão.
    """

    name = "3macd"

    def __init__(
        self,
        m1_fast: int = THREE_MACD_M1_FAST,
        m1_slow: int = THREE_MACD_M1_SLOW,
        m2_fast: int = THREE_MACD_M2_FAST,
        m2_slow: int = THREE_MACD_M2_SLOW,
        m3_fast: int = THREE_MACD_M3_FAST,
        m3_slow: int = THREE_MACD_M3_SLOW,
        buff_size: int = THREE_MACD_BUFF_SIZE,
    ) -> None:
        self._m1_fast = m1_fast
        self._m1_slow = m1_slow
        self._m2_fast = m2_fast
        self._m2_slow = m2_slow
        self._m3_fast = m3_fast
        self._m3_slow = m3_slow
        self._buff_size = buff_size

    def evaluate(self, candles: pd.DataFrame) -> StrategySignal | None:
        close = candles["close"].astype(float)

        # A EMA lenta do MACD(34,144) só deixa de ser NaN a partir do
        # candle `m3_slow`; a varredura de topo/fundo ainda precisa de
        # `buff_size` barras fechadas depois disso.
        minimo = max(self._m1_slow, self._m2_slow, self._m3_slow) + self._buff_size
        if len(close) < minimo:
            return None

        m1 = _macd_series(close, self._m1_fast, self._m1_slow, self._buff_size)
        m2 = _macd_series(close, self._m2_fast, self._m2_slow, self._buff_size)
        m3 = _macd_series(close, self._m3_fast, self._m3_slow, self._buff_size)

        if any(math.isnan(v) for serie in (m1, m2, m3) for v in serie[1:]):
            # Indicador em aquecimento apesar do comprimento mínimo — ausência
            # de informação, mesma regra de `BBRSIStrategy`.
            return None

        componentes = {"m1_1": m1[1], "m2_1": m2[1], "m3_1": m3[1]}

        if _three_macd_buy(m1, m2, m3, self._buff_size):
            return StrategySignal(
                direction=Direction.BUY, confidence=1.0, components=componentes, score=1.0
            )
        if _three_macd_sell(m1, m2, m3, self._buff_size):
            return StrategySignal(
                direction=Direction.SELL, confidence=1.0, components=componentes, score=-1.0
            )
        return StrategySignal(
            direction=Direction.HOLD, confidence=0.0, components=componentes, score=0.0
        )


# Registro de estratégias conhecidas, chaveado pelo nome usado em
# `STRATEGIES_ENABLED` e persistido em `signals.strategy`/`trades.strategy`.
STRATEGY_REGISTRY: dict[str, Callable[[], Strategy]] = {
    "technical": TechnicalStrategy,
    "bbrsi": BBRSIStrategy,
    "3macd": ThreeMacdStrategy,
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
