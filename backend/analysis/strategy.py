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

`TwoMacdStoStrategy` (história 42) é reversão em tendência: porta fiel de
`2MACDSTO.mq5` do mesmo repositório, dois MACDs ((13,21), (34,144)) mais um
Estocástico lento (7,3,3). Volta a ser condição de barra única, como BBRSI —
as cinco condições da fonte comparam só as barras -1 e -2, sem varredura.
Sem stop próprio: a fonte usa `SL_SWING`, fora do escopo desta história.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol, cast

import pandas as pd
from ta.momentum import RSIIndicator, StochasticOscillator
from ta.trend import MACD
from ta.volatility import BollingerBands

from backend.analysis.technical_analyzer import TechnicalAnalyzer, compute_indicators
from backend.models.enums import Direction

__all__ = [
    "BBRSI_BB_DEV",
    "BBRSI_BB_LEN",
    "BBRSI_RISK_PCT",
    "BBRSI_RSI_LEN",
    "BBRSI_SL_COEF",
    "BBRSI_TP_COEF",
    "BBRSI_VOLUME_MAX_LOTS",
    "STRATEGY_DIRECTION_THRESHOLD",
    "STRATEGY_REGISTRY",
    "THREE_MACD_BUFF_SIZE",
    "THREE_MACD_M1_FAST",
    "THREE_MACD_M1_SLOW",
    "THREE_MACD_M2_FAST",
    "THREE_MACD_M2_SLOW",
    "THREE_MACD_M3_FAST",
    "THREE_MACD_M3_SLOW",
    "THREE_MACD_RISK_PCT",
    "THREE_MACD_TP_COEF",
    "THREE_MACD_VOLUME_MAX_LOTS",
    "TWO_MACD_STO_D_PERIOD",
    "TWO_MACD_STO_K_PERIOD",
    "TWO_MACD_STO_M1_FAST",
    "TWO_MACD_STO_M1_SLOW",
    "TWO_MACD_STO_M2_FAST",
    "TWO_MACD_STO_M2_SLOW",
    "TWO_MACD_STO_RISK_PCT",
    "TWO_MACD_STO_SLOWING",
    "TWO_MACD_STO_TP_COEF",
    "TWO_MACD_STO_VOLUME_MAX_LOTS",
    "BBRSIStrategy",
    "Strategy",
    "StrategySignal",
    "TechnicalStrategy",
    "ThreeMacdStrategy",
    "TwoMacdStoStrategy",
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

    `take_profit_rr` segue a mesma lógica para o ALVO: `None` deixa o runner
    usar o RR global derivado da config (`take_profit_pct` /
    `max_loss_pct_per_trade`). Uma estratégia portada de uma fonte que define
    o próprio `TPCoef` preenche este campo — é o coeficiente que multiplica a
    distância do stop, exatamente como `tp = in + TPCoef * |in - sl|` no MQL5
    original. Cada estratégia foi desenhada e testada pelo autor com um RR
    específico; forçar todas ao mesmo RR global quebra a expectância de quem
    acerta pouco e ganha muito (ver `THREE_MACD_TP_COEF`).

    `risk_pct` é o percentual do equity arriscado por trade — o `Risk` da
    fonte, que lá vira `ea.risk = Risk * 0.01` e alimenta
    `vol = (balance * risk) / |in - sl| * point / tv`, a mesma fórmula de
    `BotRunner._calcular_volume`. `None` usa `max_loss_pct_per_trade` da
    config. O autor calibrou um valor por estratégia (0,5% na de tendência,
    2,25% na de pullback) porque a frequência e a qualidade dos sinais
    diferem — ver o AVISO de agregação em `THREE_MACD_RISK_PCT`.
    """

    direction: Direction
    confidence: float
    components: dict[str, float] = field(default_factory=dict)
    score: float = 0.0
    stop_loss: float | None = None
    take_profit_rr: float | None = None
    risk_pct: float | None = None
    volume_max_lots: float | None = None


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
# `TPCoef = 1` na fonte: alvo à mesma distância do stop (RR 1.0), o que implica
# 50% de win rate para breakeven. Reversão à média acerta com frequência mas o
# movimento de volta à média é limitado — daí o alvo simétrico, não esticado.
# A literatura de mean reversion (win rate 60-75%) recomenda RR 1:1 a 2:1;
# 1.0 é o piso conservador dessa faixa.
BBRSI_TP_COEF = 1.0
# `Risk = 1.0` na fonte -> 1% do equity por trade (`ea.risk = Risk * 0.01`).
BBRSI_RISK_PCT = 1.0
# Teto de lotes por ordem, proporcional ao `Risk` da fonte (2x). Ver o bloco de
# TETO POR ESTRATEGIA em `THREE_MACD_VOLUME_MAX_LOTS`.
BBRSI_VOLUME_MAX_LOTS = 2.0
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
        tp_coef: float = BBRSI_TP_COEF,
        risk_pct: float = BBRSI_RISK_PCT,
        volume_max_lots: float = BBRSI_VOLUME_MAX_LOTS,
    ) -> None:
        self._bb_len = bb_len
        self._bb_dev = bb_dev
        self._rsi_len = rsi_len
        self._sl_coef = sl_coef
        self._tp_coef = tp_coef
        self._risk_pct = risk_pct
        self._volume_max_lots = volume_max_lots

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
                take_profit_rr=self._tp_coef,
                risk_pct=self._risk_pct,
                volume_max_lots=self._volume_max_lots,
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
                take_profit_rr=self._tp_coef,
                risk_pct=self._risk_pct,
                volume_max_lots=self._volume_max_lots,
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
# `TPCoef = 2.0` na fonte: alvo ao DOBRO da distância do stop (RR 2.0), que
# implica apenas 33,3% de win rate para breakeven. É seguidora de tendência —
# desenhada para errar a maioria das entradas e compensar nas que pegam o
# movimento. Rodar esta estratégia com RR baixo inverte a lógica dela: com o
# RR global de 0,333 ela precisaria de 75% de acerto, contra os ~60% que uma
# trend follower entrega por natureza.
# A literatura de trend following (win rate 30-40%) prescreve RR 1:2 a 1:3+;
# 2.0 é exatamente o mínimo dessa faixa.
THREE_MACD_TP_COEF = 2.0
# `Risk = 0.5` na fonte -> 0,5% do equity por trade. É o MENOR risco das três,
# apesar de ser a estratégia de maior RR: o autor compensa a baixa taxa de
# acerto da tendência com tamanho menor, não maior.
#
# AVISO DE AGREGAÇÃO: estes percentuais vêm de EAs que rodam UM por vez. Este
# bot avalia N estratégias x M símbolos por ciclo, então o risco simultâneo é
# a SOMA dos slots abertos, não o valor de uma linha. Com `MAX_OPEN_POSITIONS`
# em 12 e o maior valor da tabela (2,25%), o pior caso agregado passa de 25%
# do equity — muito acima do kill switch diário de 5%, que só barra ordem
# nova e não fecha posição já aberta. Os limites que efetivamente contêm isso
# hoje são `MAX_OPEN_POSITIONS`, a margem livre do broker e o teto de lotes
# por ordem. Reduza estes percentuais se aumentar o número de slots.
THREE_MACD_RISK_PCT = 0.5
# TETO POR ESTRATEGIA: com stop de ~1,5 pip (ATR da sessao asiatica), 2,25% de
# risco pede 151 lotes e 0,1% pede 5,8 - em TODOS os casos o teto de volume
# binda primeiro, entao e ELE, nao o `risk_pct`, que decide o tamanho real.
# Medido em producao: 2macdsto abriu 5,0 lotes (risco real US) enquanto o
# risco configurado pediria US.250.
#
# Enquanto o teto vinha da confianca do sinal, estrategia binaria (confianca
# sempre 1.0 -> 0.7 fundida -> 70%) pegava sempre o maximo da curva, e a de
# score continuo (confianca ~0,09) pegava o minimo: o maior tamanho ia para a
# de pior expectancia observada. Derivar o teto do `Risk` da fonte restaura a
# hierarquia do autor num ponto que efetivamente binda.
THREE_MACD_VOLUME_MAX_LOTS = 1.0


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
        tp_coef: float = THREE_MACD_TP_COEF,
        risk_pct: float = THREE_MACD_RISK_PCT,
        volume_max_lots: float = THREE_MACD_VOLUME_MAX_LOTS,
    ) -> None:
        self._m1_fast = m1_fast
        self._m1_slow = m1_slow
        self._m2_fast = m2_fast
        self._m2_slow = m2_slow
        self._m3_fast = m3_fast
        self._m3_slow = m3_slow
        self._buff_size = buff_size
        self._tp_coef = tp_coef
        self._risk_pct = risk_pct
        self._volume_max_lots = volume_max_lots

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
                direction=Direction.BUY,
                confidence=1.0,
                components=componentes,
                score=1.0,
                take_profit_rr=self._tp_coef,
                risk_pct=self._risk_pct,
                volume_max_lots=self._volume_max_lots,
            )
        if _three_macd_sell(m1, m2, m3, self._buff_size):
            return StrategySignal(
                direction=Direction.SELL,
                confidence=1.0,
                components=componentes,
                score=-1.0,
                take_profit_rr=self._tp_coef,
                risk_pct=self._risk_pct,
                volume_max_lots=self._volume_max_lots,
            )
        return StrategySignal(
            direction=Direction.HOLD, confidence=0.0, components=componentes, score=0.0
        )


# Defaults de `2MACDSTO.mq5` (geraked/metatrader5): MACD(13,21) + MACD(34,144)
# + Estocástico lento(7,3,3). Níveis 20/80 são fixos na fonte, não parâmetros.
TWO_MACD_STO_M1_FAST = 13
TWO_MACD_STO_M1_SLOW = 21
TWO_MACD_STO_M2_FAST = 34
TWO_MACD_STO_M2_SLOW = 144
TWO_MACD_STO_K_PERIOD = 7
TWO_MACD_STO_SLOWING = 3
TWO_MACD_STO_D_PERIOD = 3
# `TPCoef = 1.0` na fonte: alvo simétrico ao stop (RR 1.0), breakeven em 50%.
# É reversão DENTRO de uma tendência: pega o repique contra o movimento maior,
# que por definição tem fôlego curto — alvo esticado não seria alcançado.
# A literatura de pullback (win rate 55-65%) sugere RR 1:1,5 a 2:1 — a fonte
# usa 1.0, abaixo dessa faixa. Mantido o valor da fonte por ser o efetivamente
# backtestado NESTA estratégia; o parâmetro é configurável para calibrar com
# dado real quando houver amostra suficiente.
TWO_MACD_STO_TP_COEF = 1.0
# `Risk = 2.25` na fonte -> 2,25% do equity por trade, o MAIOR das três (4,5x
# o do 3MACD). Ver o AVISO DE AGREGAÇÃO em `THREE_MACD_RISK_PCT`: este é o
# valor que domina o pior caso quando vários slots abrem ao mesmo tempo.
TWO_MACD_STO_RISK_PCT = 2.25
# Teto de lotes por ordem, proporcional ao `Risk` da fonte (2x) - o maior dos
# tres, mantendo a razao 0,5:1,0:2,25 do autor.
TWO_MACD_STO_VOLUME_MAX_LOTS = 4.5
_TWO_MACD_STO_K_LOWER = 20.0
_TWO_MACD_STO_K_UPPER = 80.0


def _macd_line(close: pd.Series, fast: int, slow: int) -> pd.Series:
    """Linha principal do MACD (`ema_fast - ema_slow`), via `ta.trend.MACD`."""
    return cast("pd.Series", MACD(close=close, window_slow=slow, window_fast=fast).macd())


def _stochastic_k_d(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    k_period: int,
    slowing: int,
    d_period: int,
) -> tuple[pd.Series, pd.Series]:
    """%K/%D do "estocástico lento" na convenção MQL5 (`iStochastic`, Method=SMA).

    `ta.momentum.StochasticOscillator` só expõe um estágio de suavização
    (`smooth_window` sobre o %K bruto). O MQL5 aplica dois: `Slowing` sobre o
    %K bruto vira o buffer 0 ("%K" exibido, `STO_M` na fonte), e `DPeriod`
    sobre esse resultado vira o buffer 1 (`STO_S`). `stoch_signal` com
    `smooth_window=slowing` já devolve exatamente o primeiro estágio — o
    segundo é só mais uma média móvel simples por cima, sem reimplementar o
    oscilador em si.
    """
    k = StochasticOscillator(
        high=high, low=low, close=close, window=k_period, smooth_window=slowing
    ).stoch_signal()
    d = k.rolling(window=d_period).mean()
    return k, d


def _two_macd_sto_buy(
    m1_2: float, m2_2: float, k_1: float, k_2: float, d_1: float, d_2: float
) -> bool:
    """Porta literal de `BuySignal()` em `2MACDSTO.mq5`."""
    return m2_2 > 0 and m1_2 < 0 and k_2 < _TWO_MACD_STO_K_LOWER and k_2 <= d_2 and k_1 > d_1


def _two_macd_sto_sell(
    m1_2: float, m2_2: float, k_1: float, k_2: float, d_1: float, d_2: float
) -> bool:
    """Porta literal de `SellSignal()` em `2MACDSTO.mq5` — espelho exato."""
    return m2_2 < 0 and m1_2 > 0 and k_2 > _TWO_MACD_STO_K_UPPER and k_2 >= d_2 and k_1 < d_1


class TwoMacdStoStrategy:
    """Reversão em tendência: dois MACDs + Estocástico lento — porta de `2MACDSTO.mq5`.

    Sinal binário, como `BBRSIStrategy`: as cinco condições da fonte batem
    (compra ou venda) ou não (HOLD) — não há posição intermediária. Sem stop
    próprio: `StrategySignal.stop_loss` fica `None` e o runner deriva do ATR
    global (a fonte usa `SL_SWING`, fora do escopo desta história).
    """

    name = "2macdsto"

    def __init__(
        self,
        m1_fast: int = TWO_MACD_STO_M1_FAST,
        m1_slow: int = TWO_MACD_STO_M1_SLOW,
        m2_fast: int = TWO_MACD_STO_M2_FAST,
        m2_slow: int = TWO_MACD_STO_M2_SLOW,
        sto_k_period: int = TWO_MACD_STO_K_PERIOD,
        sto_slowing: int = TWO_MACD_STO_SLOWING,
        sto_d_period: int = TWO_MACD_STO_D_PERIOD,
        tp_coef: float = TWO_MACD_STO_TP_COEF,
        risk_pct: float = TWO_MACD_STO_RISK_PCT,
        volume_max_lots: float = TWO_MACD_STO_VOLUME_MAX_LOTS,
    ) -> None:
        self._m1_fast = m1_fast
        self._m1_slow = m1_slow
        self._m2_fast = m2_fast
        self._m2_slow = m2_slow
        self._sto_k_period = sto_k_period
        self._sto_slowing = sto_slowing
        self._sto_d_period = sto_d_period
        self._tp_coef = tp_coef
        self._risk_pct = risk_pct
        self._volume_max_lots = volume_max_lots

    def evaluate(self, candles: pd.DataFrame) -> StrategySignal | None:
        high = candles["high"].astype(float)
        low = candles["low"].astype(float)
        close = candles["close"].astype(float)

        # MACD só deixa de ser NaN a partir do candle `slow`-ésimo (gotcha já
        # registrado: `ta` usa `min_periods=window` no ewm). O estocástico
        # precisa de `k_period` candles para o %K bruto, mais `slowing` e
        # `d_period` para os dois estágios de suavização MQL5 por cima.
        minimo = (
            max(self._m1_slow, self._m2_slow)
            + self._sto_k_period
            + self._sto_slowing
            + self._sto_d_period
        )
        if len(close) < minimo:
            return None

        m1 = _macd_line(close, self._m1_fast, self._m1_slow)
        m2 = _macd_line(close, self._m2_fast, self._m2_slow)
        k, d = _stochastic_k_d(
            high, low, close, self._sto_k_period, self._sto_slowing, self._sto_d_period
        )

        m1_2, m2_2 = float(m1.iloc[-2]), float(m2.iloc[-2])
        k_1, k_2 = float(k.iloc[-1]), float(k.iloc[-2])
        d_1, d_2 = float(d.iloc[-1]), float(d.iloc[-2])

        if any(math.isnan(v) for v in (m1_2, m2_2, k_1, k_2, d_1, d_2)):
            # Indicador em aquecimento apesar do comprimento mínimo (ex.:
            # estocástico com máximo == mínimo numa janela) — ausência de
            # informação, mesma regra de `BBRSIStrategy`/`ThreeMacdStrategy`.
            return None

        componentes = {"m1_2": m1_2, "m2_2": m2_2, "k_1": k_1, "k_2": k_2, "d_1": d_1, "d_2": d_2}

        if _two_macd_sto_buy(m1_2, m2_2, k_1, k_2, d_1, d_2):
            return StrategySignal(
                direction=Direction.BUY,
                confidence=1.0,
                components=componentes,
                score=1.0,
                take_profit_rr=self._tp_coef,
                risk_pct=self._risk_pct,
                volume_max_lots=self._volume_max_lots,
            )
        if _two_macd_sto_sell(m1_2, m2_2, k_1, k_2, d_1, d_2):
            return StrategySignal(
                direction=Direction.SELL,
                confidence=1.0,
                components=componentes,
                score=-1.0,
                take_profit_rr=self._tp_coef,
                risk_pct=self._risk_pct,
                volume_max_lots=self._volume_max_lots,
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
    "2macdsto": TwoMacdStoStrategy,
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
