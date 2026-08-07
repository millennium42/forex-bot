"""História 39 — Protocol de estratégia técnica.

`TechnicalStrategy` é a leitura que já existia (technical_analyzer + alpha
factors) atrás do novo Protocol `Strategy`. Os testes de direção usam um
analisador falso para controlar o score de forma determinística — a
aritmética do técnico em si já é coberta por `test_technical_analyzer.py`.
"""

from __future__ import annotations

import pandas as pd
import pytest

import backend.analysis.strategy as strategy_module
from backend.analysis.strategy import (
    STRATEGY_DIRECTION_THRESHOLD,
    STRATEGY_REGISTRY,
    TechnicalStrategy,
    build_enabled_strategies,
)
from backend.analysis.technical_analyzer import IndicatorSnapshot, TechnicalScore
from backend.models.enums import Direction


def _candles(n: int = 5) -> pd.DataFrame:
    return pd.DataFrame(
        {"open": [1.0] * n, "high": [1.0] * n, "low": [1.0] * n, "close": [1.0] * n}
    )


class _FakeAnalyzer:
    """Analisador determinístico: sempre devolve o `TechnicalScore` cadastrado."""

    def __init__(self, score: TechnicalScore) -> None:
        self._score = score

    def analyze(self, candles: pd.DataFrame) -> TechnicalScore:
        return self._score


def _indicadores_validos(_candles: pd.DataFrame) -> IndicatorSnapshot:
    return IndicatorSnapshot(
        close=1.0,
        rsi=50.0,
        macd=0.0,
        macd_signal=0.0,
        macd_diff=0.0,
        bb_high=1.0,
        bb_low=1.0,
        bb_pct=0.5,
        atr=0.01,
    )


def test_evaluate_serie_curta_devolve_none() -> None:
    """Sem candles suficientes, não há setup — `None`, não HOLD."""
    assert TechnicalStrategy().evaluate(_candles(5)) is None


@pytest.mark.parametrize(
    ("score", "direcao_esperada"),
    [
        (STRATEGY_DIRECTION_THRESHOLD, Direction.BUY),
        (-STRATEGY_DIRECTION_THRESHOLD, Direction.SELL),
        (0.0, Direction.HOLD),
    ],
)
def test_evaluate_deriva_direcao_do_score(
    monkeypatch: pytest.MonkeyPatch, score: float, direcao_esperada: Direction
) -> None:
    monkeypatch.setattr(strategy_module, "compute_indicators", _indicadores_validos)
    fake_score = TechnicalScore(score=score, confidence=0.8, components={"rsi": score})
    strategy = TechnicalStrategy(analyzer=_FakeAnalyzer(fake_score))  # type: ignore[arg-type]

    resultado = strategy.evaluate(_candles(60))

    assert resultado is not None
    assert resultado.direction is direcao_esperada
    assert resultado.confidence == pytest.approx(0.8)
    assert resultado.components == {"rsi": score}
    assert resultado.score == pytest.approx(score)


def test_strategy_name_e_technical() -> None:
    assert TechnicalStrategy().name == "technical"


def test_registry_contem_technical() -> None:
    assert "technical" in STRATEGY_REGISTRY


def test_build_enabled_strategies_technical() -> None:
    estrategias = build_enabled_strategies(["technical"])

    assert len(estrategias) == 1
    assert estrategias[0].name == "technical"
    assert isinstance(estrategias[0], TechnicalStrategy)


def test_build_enabled_strategies_nome_desconhecido_falha() -> None:
    with pytest.raises(ValueError, match="estrategia desconhecida"):
        build_enabled_strategies(["bbrsi"])
