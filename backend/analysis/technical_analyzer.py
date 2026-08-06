"""Analisador técnico: OHLC → RSI, MACD, Bollinger, ATR + alpha factors → score em [-1,1].

Todos os indicadores vêm da lib `ta` (Ponytail: não reimplementar indicador que
já existe). Este módulo só faz duas coisas que a lib não faz: **normalizar** cada
indicador para a mesma faixa assinada e **combinar** os componentes num score
único com uma confiança associada.

Convenção de sinal, válida para todo componente: **positivo = viés de compra**,
negativo = viés de venda.

RSI e Bollinger entram com leitura de reversão à média (extremo esticado tende a
voltar); o MACD entra como momento de tendência. ATR **não** é direcional — serve
de escala para normalizar o MACD (histograma em pips não é comparável entre pares)
e é publicado no snapshot porque o risk manager dimensiona o stop por ele.

Além dos quatro indicadores clássicos, entram quatro alpha factors do repertório
quant (estilo Machine-Learning-for-Trading / Qlib Alpha158), montados com
pandas/`ta` sem dependência nova: momentum multi-janela, reversão à média
normalizada, volatilidade relativa e força de tendência (ADX). Cada um segue a
mesma convenção de sinal e entra na mesma média dos demais componentes.

Os pesos aqui são fixos e iguais. A ponderação aprendida e versionada é da
história 8 (`signal_fusion`); duplicá-la aqui criaria duas fontes de verdade.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd
import structlog
from ta.momentum import RSIIndicator
from ta.trend import MACD, ADXIndicator
from ta.volatility import AverageTrueRange, BollingerBands

from backend.observability import measure_latency

__all__ = [
    "ADX_WINDOW",
    "ATR_WINDOW",
    "BB_WINDOW",
    "MACD_SLOW",
    "MOMENTUM_WINDOWS",
    "REQUIRED_COLUMNS",
    "REVERSION_WINDOW",
    "RSI_WINDOW",
    "VOLATILITY_WINDOW",
    "IndicatorSnapshot",
    "TechnicalAnalyzer",
    "TechnicalScore",
    "compute_indicators",
    "minimum_candles",
    "neutral",
]

logger = structlog.get_logger(__name__)

REQUIRED_COLUMNS = ("open", "high", "low", "close")

# Janelas padrão dos indicadores — os mesmos defaults da lib `ta`.
RSI_WINDOW = 14
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
BB_WINDOW = 20
BB_DEVIATIONS = 2.0
ATR_WINDOW = 14

# Janelas dos alpha factors. Todas menores que MACD_SLOW + MACD_SIGNAL (35),
# que segue sendo o requisito mais exigente — ver `minimum_candles()`.
MOMENTUM_WINDOWS = (5, 10, 20)
REVERSION_WINDOW = 10
VOLATILITY_WINDOW = 20
ADX_WINDOW = 14

# RSI 50 é o centro da escala: metade da faixa é a distância máxima até o extremo.
RSI_NEUTRAL = 50.0

ENGINE = "ta"


def _clamp(valor: float, minimo: float, maximo: float) -> float:
    return max(minimo, min(maximo, valor))


def minimum_candles() -> int:
    """Quantidade mínima de candles para todos os indicadores terem valor.

    O MACD é o mais exigente: a linha de sinal só existe depois da EMA lenta
    mais a EMA de sinal.
    """
    return MACD_SLOW + MACD_SIGNAL


@dataclass(frozen=True, slots=True)
class IndicatorSnapshot:
    """Leitura crua dos indicadores no último candle fechado.

    Os campos de alpha factor têm `default=0.0` só para não quebrar construção
    manual em teste que não os usa (ex: `test_runner_sentimento.py`) — o
    `compute_indicators` real sempre preenche todos, nunca depende do default.
    """

    close: float
    rsi: float
    macd: float
    macd_signal: float
    macd_diff: float
    bb_high: float
    bb_low: float
    bb_pct: float
    atr: float
    momentum_5: float = 0.0
    momentum_10: float = 0.0
    momentum_20: float = 0.0
    reversion_mean: float = 0.0
    reversion_std: float = 0.0
    atr_baseline: float = 0.0
    adx: float = 0.0
    adx_pos: float = 0.0
    adx_neg: float = 0.0


@dataclass(frozen=True, slots=True)
class TechnicalScore:
    """Score assinado e confiança, já dentro das faixas contratadas.

    O clamp acontece na construção, como em `SentimentScore`: não existe
    instância fora de [-1,1] e [0,1], venha o valor de onde vier.
    """

    score: float
    confidence: float
    engine: str = ENGINE
    components: dict[str, float] = field(default_factory=dict)
    indicators: IndicatorSnapshot | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "score", _clamp(float(self.score), -1.0, 1.0))
        object.__setattr__(self, "confidence", _clamp(float(self.confidence), 0.0, 1.0))


def neutral() -> TechnicalScore:
    """Ausência de leitura: score zero com confiança zero.

    Mesma regra do sentimento — sinal sem informação não pode entrar no fusion
    como se fosse leitura de mercado. Confiança zero faz ele não pesar.
    """
    return TechnicalScore(score=0.0, confidence=0.0)


def _validate(candles: pd.DataFrame) -> None:
    faltando = [c for c in REQUIRED_COLUMNS if c not in candles.columns]
    if faltando:
        # Coluna ausente é erro de programação, não ausência de dado de mercado.
        raise ValueError(f"colunas OHLC ausentes: {', '.join(faltando)}")


def compute_indicators(candles: pd.DataFrame) -> IndicatorSnapshot | None:
    """Indicadores no último candle. `None` quando a série não os sustenta.

    Devolve `None` — em vez de zero — quando faltam candles ou quando qualquer
    indicador ainda está em NaN no fim da série: um indicador em aquecimento não
    é um indicador neutro.
    """
    _validate(candles)
    if len(candles) < minimum_candles():
        logger.warning(
            "technical.candles_insuficientes",
            recebidos=len(candles),
            minimo=minimum_candles(),
        )
        return None

    high = candles["high"].astype(float)
    low = candles["low"].astype(float)
    close = candles["close"].astype(float)

    rsi = RSIIndicator(close=close, window=RSI_WINDOW).rsi()
    macd = MACD(close=close, window_slow=MACD_SLOW, window_fast=MACD_FAST, window_sign=MACD_SIGNAL)
    bollinger = BollingerBands(close=close, window=BB_WINDOW, window_dev=BB_DEVIATIONS)
    atr = AverageTrueRange(high=high, low=low, close=close, window=ATR_WINDOW).average_true_range()
    adx = ADXIndicator(high=high, low=low, close=close, window=ADX_WINDOW)

    valores = {
        "close": close,
        "rsi": rsi,
        "macd": macd.macd(),
        "macd_signal": macd.macd_signal(),
        "macd_diff": macd.macd_diff(),
        "bb_high": bollinger.bollinger_hband(),
        "bb_low": bollinger.bollinger_lband(),
        "bb_pct": bollinger.bollinger_pband(),
        "atr": atr,
        "momentum_5": close.diff(periods=MOMENTUM_WINDOWS[0]),
        "momentum_10": close.diff(periods=MOMENTUM_WINDOWS[1]),
        "momentum_20": close.diff(periods=MOMENTUM_WINDOWS[2]),
        "reversion_mean": close.rolling(REVERSION_WINDOW).mean(),
        "reversion_std": close.rolling(REVERSION_WINDOW).std(),
        "atr_baseline": atr.rolling(VOLATILITY_WINDOW).mean(),
        "adx": adx.adx(),
        "adx_pos": adx.adx_pos(),
        "adx_neg": adx.adx_neg(),
    }

    ultimos: dict[str, float] = {}
    for nome, serie in valores.items():
        ultimo = float(serie.iloc[-1])
        if math.isnan(ultimo):
            logger.warning("technical.indicador_em_nan", indicador=nome)
            return None
        ultimos[nome] = ultimo

    return IndicatorSnapshot(**ultimos)


def _score_rsi(snapshot: IndicatorSnapshot) -> float:
    """Reversão à média: sobrevendido puxa para compra, sobrecomprado para venda."""
    return _clamp((RSI_NEUTRAL - snapshot.rsi) / RSI_NEUTRAL, -1.0, 1.0)


def _score_macd(snapshot: IndicatorSnapshot) -> float:
    """Histograma do MACD em unidades de ATR.

    Sem a divisão pelo ATR o componente ficaria em unidade de preço e um par
    volátil dominaria o score só por oscilar mais.
    """
    if snapshot.atr <= 0:
        # Série sem range (candles idênticos): não há escala para normalizar.
        return 0.0
    return _clamp(snapshot.macd_diff / snapshot.atr, -1.0, 1.0)


def _score_bollinger(snapshot: IndicatorSnapshot) -> float:
    """%B invertido: preço na banda inferior é compra, na superior é venda."""
    return _clamp(1.0 - 2.0 * snapshot.bb_pct, -1.0, 1.0)


def _score_momentum(snapshot: IndicatorSnapshot) -> float:
    """Momentum multi-janela: média da variação de preço normalizada por ATR.

    Cada janela mede se o preço andou na mesma direção nos últimos N candles;
    a média suaviza o ruído de uma única janela sem perder a reação das
    janelas curtas. Sem ATR não há escala — mesma regra do MACD.
    """
    if snapshot.atr <= 0:
        return 0.0
    variacoes = (
        snapshot.momentum_5 / snapshot.atr,
        snapshot.momentum_10 / snapshot.atr,
        snapshot.momentum_20 / snapshot.atr,
    )
    return _clamp(sum(variacoes) / len(variacoes), -1.0, 1.0)


def _score_reversion(snapshot: IndicatorSnapshot) -> float:
    """Reversão à média normalizada: z-score do preço contra a SMA curta, invertido.

    Diferente do %B do Bollinger (janela BB_WINDOW=20 com banda em desvio
    fixo), aqui a janela é mais curta (REVERSION_WINDOW=10) e o próprio
    z-score dá a intensidade — dois fatores de reversão com sensibilidade
    distinta. Desvio zero (série sem variação na janela) não tem escala.
    """
    if snapshot.reversion_std <= 0:
        return 0.0
    z = (snapshot.close - snapshot.reversion_mean) / snapshot.reversion_std
    return _clamp(-z, -1.0, 1.0)


def _score_relative_volatility(snapshot: IndicatorSnapshot) -> float:
    """Volatilidade relativa: expansão do ATR na direção do preço acima/abaixo da SMA curta.

    Volatilidade subindo (ATR acima da própria média móvel) com preço acima
    da SMA reforça o viés de compra — rompimento com força; volatilidade
    contraindo neutraliza o sinal, já que compressão não sustenta direção.
    Sem baseline (série curta) não há o que comparar.
    """
    if snapshot.atr_baseline <= 0:
        return 0.0
    direcao = snapshot.close - snapshot.reversion_mean
    if direcao == 0:
        return 0.0
    sinal = 1.0 if direcao > 0 else -1.0
    expansao = (snapshot.atr - snapshot.atr_baseline) / snapshot.atr_baseline
    return _clamp(sinal * expansao, -1.0, 1.0)


def _score_trend_strength(snapshot: IndicatorSnapshot) -> float:
    """Força de tendência: ADX pondera a direção dada por +DI/-DI.

    A diferença entre +DI e -DI dá o lado; ADX/100 dá a convicção — uma
    tendência fraca (ADX baixo) encolhe o score mesmo com DI bem separado.
    +DI e -DI ambos zerados (série sem direcional definido) não tem lado.
    """
    soma_di = snapshot.adx_pos + snapshot.adx_neg
    if soma_di <= 0:
        return 0.0
    direcao = (snapshot.adx_pos - snapshot.adx_neg) / soma_di
    return _clamp(direcao * (snapshot.adx / 100.0), -1.0, 1.0)


def _components(snapshot: IndicatorSnapshot) -> dict[str, float]:
    return {
        "rsi": _score_rsi(snapshot),
        "macd": _score_macd(snapshot),
        "bollinger": _score_bollinger(snapshot),
        "momentum": _score_momentum(snapshot),
        "mean_reversion": _score_reversion(snapshot),
        "relative_volatility": _score_relative_volatility(snapshot),
        "trend_strength": _score_trend_strength(snapshot),
    }


def _confidence(componentes: dict[str, float]) -> float:
    """Convicção = concordância entre componentes vezes intensidade média.

    Componentes que se anulam (um comprando, outro vendendo) derrubam a
    concordância para perto de zero; componentes fracos derrubam a intensidade.
    O score pode ser alto por acaso — a confiança é o que diz se ele foi
    sustentado por mais de um indicador.
    """
    if not componentes:
        return 0.0
    valores = list(componentes.values())
    soma_abs = sum(abs(v) for v in valores)
    if soma_abs == 0:
        return 0.0
    concordancia = abs(sum(valores)) / soma_abs
    intensidade = soma_abs / len(valores)
    return _clamp(concordancia * intensidade, 0.0, 1.0)


class TechnicalAnalyzer:
    """OHLC entra, `TechnicalScore` sai. Sem estado entre chamadas."""

    def analyze(self, candles: pd.DataFrame) -> TechnicalScore:
        """Score técnico do último candle da série.

        Série curta ou indicador em aquecimento devolve `neutral()`.
        """
        with measure_latency("technical_analysis"):
            snapshot = compute_indicators(candles)
        if snapshot is None:
            return neutral()

        componentes = _components(snapshot)
        score = sum(componentes.values()) / len(componentes)
        return TechnicalScore(
            score=score,
            confidence=_confidence(componentes),
            components=componentes,
            indicators=snapshot,
        )
