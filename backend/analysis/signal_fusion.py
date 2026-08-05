"""Combinação de sinais (Technical + Sentiment) → FusedSignal.

O módulo junta os sinais das duas pontas da análise e produz a decisão final
que será apresentada ao risk manager. Pesos de combinação são explícitos e
versionados, para que resultados passados possam ser reproduzidos e otimizados
pelo peso sem recoletar nada (história 13).
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.analysis.sentiment_analyzer import SentimentScore
from backend.analysis.technical_analyzer import TechnicalScore
from backend.models.enums import Direction

__all__ = [
    "DEFAULT_WEIGHTS",
    "FusedSignal",
    "FusionWeights",
    "fuse_signals",
]


@dataclass(frozen=True, slots=True)
class FusionWeights:
    """Pesos versionados para a combinação de sinais."""

    technical: float
    sentiment: float
    version: str


# Pesos default fixos. O weight optimizer (história 13) poderá
# gerar versões novas lendo os outcomes.
DEFAULT_WEIGHTS = FusionWeights(technical=0.7, sentiment=0.3, version="v1.0")


@dataclass(frozen=True, slots=True)
class FusedSignal:
    """Resultado final da fusão de sinais."""

    direction: Direction
    score: float
    confidence: float
    weight_version: str


def fuse_signals(
    technical: TechnicalScore,
    sentiment: SentimentScore | None,
    weights: FusionWeights = DEFAULT_WEIGHTS,
    threshold: float = 0.1,
) -> FusedSignal:
    """Combina os scores ponderando pelo peso base e pela confiança de cada um.

    Ausência de informação (sentiment=None) ou sinal com confiança 0 não movem o
    score da fusão, mas diluem a confiança final — o sistema sabe que está
    tomando uma decisão com visão parcial.

    Se os componentes discordam (ex: técnica manda comprar, sentimento manda
    vender), o score resultante tenderá a zero (dependendo dos pesos), e a
    confiança será penalizada pela falta de concordância.
    """
    w_t = weights.technical
    w_s = weights.sentiment

    t_conf = technical.confidence
    s_conf = sentiment.confidence if sentiment is not None else 0.0

    t_score = technical.score
    s_score = sentiment.score if sentiment is not None else 0.0

    # Peso efetivo: peso base * quão confiante o componente está.
    eff_w_t = w_t * t_conf
    eff_w_s = w_s * s_conf
    total_eff_w = eff_w_t + eff_w_s

    total_w = w_t + w_s
    if total_w <= 0:
        return FusedSignal(
            direction=Direction.HOLD,
            score=0.0,
            confidence=0.0,
            weight_version=weights.version,
        )

    if total_eff_w <= 0:
        # Nenhuma ponta tem confiança alguma.
        return FusedSignal(
            direction=Direction.HOLD,
            score=0.0,
            confidence=0.0,
            weight_version=weights.version,
        )

    # Score ponderado apenas pelas pontas que têm alguma convicção.
    fused_score = (t_score * eff_w_t + s_score * eff_w_s) / total_eff_w

    # Confiança final penaliza dois cenários:
    # 1. Informação ausente ou de baixa qualidade (intensidade_info < 1).
    # 2. Componentes puxando para lados opostos (concordância < 1).
    soma_abs_w = (abs(t_score) * eff_w_t) + (abs(s_score) * eff_w_s)
    if soma_abs_w > 0:
        concordancia = abs(t_score * eff_w_t + s_score * eff_w_s) / soma_abs_w
    else:
        concordancia = 0.0

    intensidade_info = total_eff_w / total_w
    fused_confidence = concordancia * intensidade_info

    # Decisão categórica por limiar
    direction = Direction.HOLD
    if fused_score >= threshold:
        direction = Direction.BUY
    elif fused_score <= -threshold:
        direction = Direction.SELL

    return FusedSignal(
        direction=direction,
        score=fused_score,
        confidence=fused_confidence,
        weight_version=weights.version,
    )
