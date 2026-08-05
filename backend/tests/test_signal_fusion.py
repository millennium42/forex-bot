"""Testes da fusão de sinais (história 8)."""

from __future__ import annotations

import pytest

from backend.analysis.sentiment_analyzer import SentimentScore
from backend.analysis.signal_fusion import (
    DEFAULT_WEIGHTS,
    FusedSignal,
    FusionWeights,
    fuse_signals,
)
from backend.analysis.technical_analyzer import TechnicalScore
from backend.models.enums import Direction


def test_fusion_unanimous_buy() -> None:
    """Técnica e sentimento concordam perfeitamente na compra."""
    t = TechnicalScore(score=1.0, confidence=1.0, components={}, indicators=None)
    s = SentimentScore(score=1.0, confidence=1.0, engine="test")

    weights = FusionWeights(technical=0.5, sentiment=0.5, version="test")
    fused = fuse_signals(t, s, weights=weights, threshold=0.1)

    assert fused.direction is Direction.BUY
    assert fused.score == 1.0
    assert fused.confidence == 1.0
    assert fused.weight_version == "test"


def test_fusion_unanimous_sell() -> None:
    """Técnica e sentimento concordam perfeitamente na venda."""
    t = TechnicalScore(score=-1.0, confidence=1.0, components={}, indicators=None)
    s = SentimentScore(score=-1.0, confidence=1.0, engine="test")

    weights = FusionWeights(technical=0.6, sentiment=0.4, version="test2")
    fused = fuse_signals(t, s, weights=weights, threshold=0.1)

    assert fused.direction is Direction.SELL
    assert fused.score == -1.0
    assert fused.confidence == 1.0
    assert fused.weight_version == "test2"


def test_fusion_missing_sentiment() -> None:
    """Sentimento ausente não move o score, mas dilui a confiança."""
    t = TechnicalScore(score=0.8, confidence=1.0, components={}, indicators=None)

    weights = FusionWeights(technical=0.6, sentiment=0.4, version="v1")
    fused = fuse_signals(t, None, weights=weights, threshold=0.1)

    assert fused.direction is Direction.BUY
    assert fused.score == 0.8  # só técnica pesa
    assert fused.confidence == 0.6  # perdeu 40% do peso possível da info ausente


def test_fusion_disagreement() -> None:
    """Técnica manda comprar, sentimento manda vender. Peso anula e confiança cai."""
    t = TechnicalScore(score=1.0, confidence=1.0, components={}, indicators=None)
    s = SentimentScore(score=-1.0, confidence=1.0, engine="test")

    weights = FusionWeights(technical=0.5, sentiment=0.5, version="v1")
    fused = fuse_signals(t, s, weights=weights, threshold=0.1)

    assert fused.direction is Direction.HOLD
    assert fused.score == 0.0
    assert fused.confidence == 0.0  # concordância zero


def test_fusion_zero_confidence() -> None:
    """Sinais presentes mas com zero confiança."""
    t = TechnicalScore(score=1.0, confidence=0.0, components={}, indicators=None)
    s = SentimentScore(score=-1.0, confidence=0.0, engine="test")

    fused = fuse_signals(t, s, weights=DEFAULT_WEIGHTS, threshold=0.1)

    assert fused.direction is Direction.HOLD
    assert fused.score == 0.0
    assert fused.confidence == 0.0


def test_fusion_threshold() -> None:
    """Score abaixo do threshold resulta em HOLD."""
    t = TechnicalScore(score=0.05, confidence=1.0, components={}, indicators=None)
    s = SentimentScore(score=0.05, confidence=1.0, engine="test")

    weights = FusionWeights(technical=0.5, sentiment=0.5, version="v1")
    fused = fuse_signals(t, s, weights=weights, threshold=0.1)

    assert fused.direction is Direction.HOLD
    assert fused.score == 0.05  # score real fica preservado
    assert fused.confidence == 1.0


def test_fusion_zero_total_weights() -> None:
    """Proteção contra pesos zerados configurados incorretamente."""
    t = TechnicalScore(score=1.0, confidence=1.0, components={}, indicators=None)
    weights = FusionWeights(technical=0.0, sentiment=0.0, version="v_zero")

    fused = fuse_signals(t, None, weights=weights)
    assert fused.direction is Direction.HOLD
    assert fused.score == 0.0
    assert fused.confidence == 0.0
