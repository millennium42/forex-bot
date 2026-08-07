"""História 39 — Protocol de estratégia técnica.

`TechnicalStrategy` é a leitura que já existia (technical_analyzer + alpha
factors) atrás do novo Protocol `Strategy`. Os testes de direção usam um
analisador falso para controlar o score de forma determinística — a
aritmética do técnico em si já é coberta por `test_technical_analyzer.py`.

`BBRSIStrategy` (história 40) usa períodos pequenos (BB(20), RSI(3)) nos
testes de sinal para manter as fixtures tratáveis à mão — a AC de "período
configurável" é exercitada exatamente por isso. Um teste à parte usa os
defaults reais (BB(500)) só para o caminho de série curta.
"""

from __future__ import annotations

import pandas as pd
import pytest

import backend.analysis.strategy as strategy_module
from backend.analysis.strategy import (
    STRATEGY_DIRECTION_THRESHOLD,
    STRATEGY_REGISTRY,
    BBRSIStrategy,
    TechnicalStrategy,
    build_enabled_strategies,
)
from backend.analysis.technical_analyzer import IndicatorSnapshot, TechnicalScore
from backend.models.enums import Direction


def _candles(n: int = 5) -> pd.DataFrame:
    return pd.DataFrame(
        {"open": [1.0] * n, "high": [1.0] * n, "low": [1.0] * n, "close": [1.0] * n}
    )


def _candles_from_closes(closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"open": closes, "high": closes, "low": closes, "close": closes})


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
        build_enabled_strategies(["3macd"])


# -- história 40: BBRSIStrategy ----------------------------------------------

# Fixtures com BB(20)/RSI(3) (períodos configuráveis, não os defaults reais
# BB(500)/RSI(7)) para caber à mão: sawtooth de baixa amplitude (mantém RSI
# perto de 50 e a banda estreita) seguido de um movimento brusco que fura a
# banda e um recuo parcial que cumpre as seis condições exatas da fonte.
_BUY_CLOSES = [
    101.0, 100.0, 101.0, 100.0, 101.0, 100.0, 101.0, 100.0, 101.0, 100.0,
    101.0, 100.0, 101.0, 100.0, 101.0, 100.0, 101.0, 100.0, 101.0, 100.0,
    96.0, 98.0,
]  # fmt: skip
_SELL_CLOSES = [
    99.0, 100.0, 99.0, 100.0, 99.0, 100.0, 99.0, 100.0, 99.0, 100.0,
    99.0, 100.0, 99.0, 100.0, 99.0, 100.0, 99.0, 100.0, 99.0, 100.0,
    104.0, 102.0,
]  # fmt: skip
_NEUTRAL_CLOSES = [
    101.0, 100.0, 101.0, 100.0, 101.0, 100.0, 101.0, 100.0, 101.0, 100.0,
    101.0, 100.0, 101.0, 100.0, 101.0, 100.0, 101.0, 100.0, 101.0, 100.0, 101.0,
]  # fmt: skip


def test_bbrsi_serie_curta_para_bb500_devolve_none() -> None:
    """AC: série curta demais para BB(500) — os defaults reais — devolve None."""
    strategy = BBRSIStrategy()
    assert strategy.evaluate(_candles_from_closes([100.0] * 100)) is None


def test_bbrsi_serie_curta_para_periodo_configurado_devolve_none() -> None:
    """bb_len=20 exige 21 candles (bb_len + 1); com só 20, ainda é curta demais."""
    strategy = BBRSIStrategy(bb_len=20, rsi_len=3)
    assert strategy.evaluate(_candles_from_closes(_BUY_CLOSES[:-2])) is None


def test_bbrsi_dispara_compra() -> None:
    strategy = BBRSIStrategy(bb_len=20, bb_dev=2.0, rsi_len=3, sl_coef=0.9)

    resultado = strategy.evaluate(_candles_from_closes(_BUY_CLOSES))

    assert resultado is not None
    assert resultado.direction is Direction.BUY
    assert resultado.score == pytest.approx(1.0)
    assert resultado.confidence == pytest.approx(1.0)
    # bb_l_1=97.7604393709303, bb_m_1=100.15 (BB(20,2.0) sobre _BUY_CLOSES).
    bb_l_1, bb_m_1 = 97.7604393709303, 100.15
    stop_esperado = bb_l_1 - 0.9 * (bb_m_1 - bb_l_1)
    assert resultado.stop_loss == pytest.approx(stop_esperado)


def test_bbrsi_dispara_venda() -> None:
    strategy = BBRSIStrategy(bb_len=20, bb_dev=2.0, rsi_len=3, sl_coef=0.9)

    resultado = strategy.evaluate(_candles_from_closes(_SELL_CLOSES))

    assert resultado is not None
    assert resultado.direction is Direction.SELL
    assert resultado.score == pytest.approx(-1.0)
    assert resultado.confidence == pytest.approx(1.0)
    # bb_u_1=102.2395606290697, bb_m_1=99.85 (BB(20,2.0) sobre _SELL_CLOSES).
    bb_u_1, bb_m_1 = 102.2395606290697, 99.85
    stop_esperado = bb_u_1 + 0.9 * (bb_u_1 - bb_m_1)
    assert resultado.stop_loss == pytest.approx(stop_esperado)


def test_bbrsi_sem_padrao_devolve_hold() -> None:
    strategy = BBRSIStrategy(bb_len=20, bb_dev=2.0, rsi_len=3)

    resultado = strategy.evaluate(_candles_from_closes(_NEUTRAL_CLOSES))

    assert resultado is not None
    assert resultado.direction is Direction.HOLD
    assert resultado.confidence == pytest.approx(0.0)
    assert resultado.stop_loss is None


def test_bbrsi_name_e_bbrsi() -> None:
    assert BBRSIStrategy().name == "bbrsi"


def test_registry_contem_bbrsi() -> None:
    assert "bbrsi" in STRATEGY_REGISTRY


def test_build_enabled_strategies_bbrsi() -> None:
    estrategias = build_enabled_strategies(["bbrsi"])

    assert len(estrategias) == 1
    assert estrategias[0].name == "bbrsi"
    assert isinstance(estrategias[0], BBRSIStrategy)
