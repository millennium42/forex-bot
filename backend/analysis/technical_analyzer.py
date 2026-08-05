"""Analisador técnico: OHLC → RSI, MACD, Bollinger, ATR → score em [-1,1].

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

Os pesos aqui são fixos e iguais. A ponderação aprendida e versionada é da
história 8 (`signal_fusion`); duplicá-la aqui criaria duas fontes de verdade.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd
import structlog
from ta.momentum import RSIIndicator
from ta.trend import MACD
from ta.volatility import AverageTrueRange, BollingerBands

__all__ = [
    "ATR_WINDOW",
    "BB_WINDOW",
    "MACD_SLOW",
    "REQUIRED_COLUMNS",
    "RSI_WINDOW",
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
    """Leitura crua dos indicadores no último candle fechado."""

    close: float
    rsi: float
    macd: float
    macd_signal: float
    macd_diff: float
    bb_high: float
    bb_low: float
    bb_pct: float
    atr: float


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


def _components(snapshot: IndicatorSnapshot) -> dict[str, float]:
    return {
        "rsi": _score_rsi(snapshot),
        "macd": _score_macd(snapshot),
        "bollinger": _score_bollinger(snapshot),
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
