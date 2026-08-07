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
    ThreeMacdStrategy,
    _three_macd_buy,
    _three_macd_sell,
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
        build_enabled_strategies(["inexistente"])


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


# -- história 41: ThreeMacdStrategy -------------------------------------------

# Arrays na convenção MQL5 (índice 1 = último candle fechado, crescendo para
# trás). `_three_macd_buy`/`_three_macd_sell` são a máquina de estado literal
# de `BuySignal()`/`SellSignal()` em `3MACD.mq5`; testá-las direto com arrays
# construídos à mão isola a lógica de topo/fundo/cruzamento de zero da
# computação real do MACD (intratável de calibrar por série de preço, dada a
# quantidade de barras e caminhos alternativos da fonte).
_BUFF = 10

# Caminho 1 da compra: fundo do M2 nas barras 1-3, cruzamento de zero do M1
# na barra 4->5, topo do M2 na barra 5->6->7. M3 constante positivo (não
# forma padrão no caminho 1, só precisa ser positivo).
_M1_BUY_P1 = [float("nan"), 0.1, -0.3, -0.2, -0.1, 0.1, 0.1, 0.1, 0.1, 0.1]
_M2_BUY_P1 = [float("nan"), 0.5, 0.2, 0.4, 0.3, 0.35, 0.5, 0.3, 0.2, 0.2]
_M3_BUY_P1 = [float("nan"), 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6]

# Espelho exato para venda: mesmos índices, sinais invertidos.
_M1_SELL_P1 = [float("nan"), -0.1, 0.3, 0.2, 0.1, -0.1, -0.1, -0.1, -0.1, -0.1]
_M2_SELL_P1 = [float("nan"), -0.5, -0.2, -0.4, -0.3, -0.35, -0.5, -0.3, -0.2, -0.2]
_M3_SELL_P1 = [float("nan"), -0.6, -0.6, -0.6, -0.6, -0.6, -0.6, -0.6, -0.6, -0.6]

# Caminho 2 da compra: fundo do M3 nas barras 1-3 (dispara o `elif`, porque o
# M2 das barras 1-3 não forma fundo), cruzamento de zero do M2 na barra
# 2->3, cruzamento de zero do M1 na barra 3->4, topo do M2 na barra 4->5->6.
_M1_BUY_P2 = [float("nan"), 0.05, 0.05, -0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1]
_M2_BUY_P2 = [float("nan"), 0.5, -0.2, 0.3, 0.2, 0.4, 0.25, 0.1, 0.1, 0.1]
_M3_BUY_P2 = [float("nan"), 0.6, 0.3, 0.5, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4]

# Espelho exato do caminho 2 para venda.
_M1_SELL_P2 = [float("nan"), -0.05, -0.05, 0.1, -0.1, -0.1, -0.1, -0.1, -0.1, -0.1]
_M2_SELL_P2 = [float("nan"), -0.5, 0.2, -0.3, -0.2, -0.4, -0.25, -0.1, -0.1, -0.1]
_M3_SELL_P2 = [float("nan"), -0.6, -0.3, -0.5, -0.4, -0.4, -0.4, -0.4, -0.4, -0.4]

# Nenhum padrão: M3[1]=0 não é maior nem menor que zero — compra e venda
# falham na primeira cláusula das duas condições, sem varrer o buffer.
_M1_LATERAL = [float("nan")] + [0.0] * (_BUFF - 1)
_M2_LATERAL = [float("nan")] + [0.0] * (_BUFF - 1)
_M3_LATERAL = [float("nan")] + [0.0] * (_BUFF - 1)

# Mesmo fundo do M2 do caminho 1 (dispara a varredura), mas M3 fica <=0 na
# primeira iteração do primeiro loop — cobre o `return False` por M3 inválido
# em vez de M2 ou ausência de cruzamento.
_M3_BUY_P1_M3_INVALIDO = [float("nan"), 0.6, -0.1, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6]

# M3 sempre positivo (nunca falha), mas M2 vira <=0 na barra 4 — cobre o
# `return False` por M2 inválido depois que a checagem de M3 já passou.
_M1_BUY_P1_SEM_CRUZAMENTO = [float("nan")] + [0.1] * (_BUFF - 1)
_M2_BUY_P1_M2_INVALIDO = [float("nan"), 0.5, 0.2, 0.4, -0.1, 0.3, 0.3, 0.3, 0.3, 0.3]
_M3_BUY_P1_SEMPRE_POSITIVO = [float("nan")] + [0.6] * (_BUFF - 1)

# M3 e M2 sempre positivos, M1 nunca cruza (sempre positivo) — o primeiro
# loop varre o buffer inteiro sem achar `j`, cobre o `return False` de
# ausência de cruzamento (distinto de M3/M2 inválidos acima).
_M2_BUY_P1_SEM_CRUZAMENTO = [float("nan"), 0.5, 0.2, 0.4, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3]


def test_three_macd_buy_caminho_1() -> None:
    assert _three_macd_buy(_M1_BUY_P1, _M2_BUY_P1, _M3_BUY_P1, _BUFF) is True


def test_three_macd_buy_caminho_2() -> None:
    assert _three_macd_buy(_M1_BUY_P2, _M2_BUY_P2, _M3_BUY_P2, _BUFF) is True


def test_three_macd_sell_caminho_1() -> None:
    assert _three_macd_sell(_M1_SELL_P1, _M2_SELL_P1, _M3_SELL_P1, _BUFF) is True


def test_three_macd_sell_caminho_2() -> None:
    assert _three_macd_sell(_M1_SELL_P2, _M2_SELL_P2, _M3_SELL_P2, _BUFF) is True


def test_three_macd_buy_sem_padrao_devolve_false() -> None:
    assert _three_macd_buy(_M1_LATERAL, _M2_LATERAL, _M3_LATERAL, _BUFF) is False


def test_three_macd_sell_sem_padrao_devolve_false() -> None:
    assert _three_macd_sell(_M1_LATERAL, _M2_LATERAL, _M3_LATERAL, _BUFF) is False


def test_three_macd_buy_m3_invalido_durante_varredura_devolve_false() -> None:
    assert (
        _three_macd_buy(_M1_BUY_P1_SEM_CRUZAMENTO, _M2_BUY_P1, _M3_BUY_P1_M3_INVALIDO, _BUFF)
        is False
    )


def test_three_macd_buy_m2_invalido_durante_varredura_devolve_false() -> None:
    assert (
        _three_macd_buy(
            _M1_BUY_P1_SEM_CRUZAMENTO,
            _M2_BUY_P1_M2_INVALIDO,
            _M3_BUY_P1_SEMPRE_POSITIVO,
            _BUFF,
        )
        is False
    )


def test_three_macd_buy_sem_cruzamento_do_m1_devolve_false() -> None:
    assert (
        _three_macd_buy(
            _M1_BUY_P1_SEM_CRUZAMENTO,
            _M2_BUY_P1_SEM_CRUZAMENTO,
            _M3_BUY_P1_SEMPRE_POSITIVO,
            _BUFF,
        )
        is False
    )


def test_three_macd_serie_curta_para_macd_34_144_devolve_none() -> None:
    """AC: série curta demais para o MACD(34,144) — default real — devolve None."""
    strategy = ThreeMacdStrategy()
    assert strategy.evaluate(_candles_from_closes([100.0] * 100)) is None


def test_three_macd_serie_lateral_real_devolve_hold() -> None:
    """Preço constante: MACD real fica em 0 nos três períodos — HOLD genuíno,
    sem monkeypatch de `_macd_series` (exercita a computação real do MACD)."""
    strategy = ThreeMacdStrategy(
        m1_fast=2, m1_slow=3, m2_fast=3, m2_slow=5, m3_fast=5, m3_slow=8, buff_size=10
    )

    resultado = strategy.evaluate(_candles_from_closes([100.0] * 30))

    assert resultado is not None
    assert resultado.direction is Direction.HOLD
    assert resultado.confidence == pytest.approx(0.0)


def _patch_macd_series_por_slow(
    monkeypatch: pytest.MonkeyPatch,
    estrategia: ThreeMacdStrategy,
    m1: list[float],
    m2: list[float],
    m3: list[float],
) -> None:
    por_slow = {
        estrategia._m1_slow: m1,
        estrategia._m2_slow: m2,
        estrategia._m3_slow: m3,
    }

    def fake_macd_series(close: pd.Series, fast: int, slow: int, buff_size: int) -> list[float]:
        return por_slow[slow]

    monkeypatch.setattr(strategy_module, "_macd_series", fake_macd_series)


def test_three_macd_tendencia_de_alta_dispara_compra(monkeypatch: pytest.MonkeyPatch) -> None:
    strategy = ThreeMacdStrategy(buff_size=_BUFF)
    _patch_macd_series_por_slow(monkeypatch, strategy, _M1_BUY_P1, _M2_BUY_P1, _M3_BUY_P1)

    resultado = strategy.evaluate(_candles_from_closes([100.0] * 200))

    assert resultado is not None
    assert resultado.direction is Direction.BUY
    assert resultado.score == pytest.approx(1.0)
    assert resultado.confidence == pytest.approx(1.0)
    assert resultado.stop_loss is None
    assert resultado.components == {"m1_1": 0.1, "m2_1": 0.5, "m3_1": 0.6}


def test_three_macd_tendencia_de_baixa_dispara_venda(monkeypatch: pytest.MonkeyPatch) -> None:
    strategy = ThreeMacdStrategy(buff_size=_BUFF)
    _patch_macd_series_por_slow(monkeypatch, strategy, _M1_SELL_P1, _M2_SELL_P1, _M3_SELL_P1)

    resultado = strategy.evaluate(_candles_from_closes([100.0] * 200))

    assert resultado is not None
    assert resultado.direction is Direction.SELL
    assert resultado.score == pytest.approx(-1.0)
    assert resultado.confidence == pytest.approx(1.0)
    assert resultado.stop_loss is None


def test_three_macd_lateral_devolve_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    strategy = ThreeMacdStrategy(buff_size=_BUFF)
    _patch_macd_series_por_slow(monkeypatch, strategy, _M1_LATERAL, _M2_LATERAL, _M3_LATERAL)

    resultado = strategy.evaluate(_candles_from_closes([100.0] * 200))

    assert resultado is not None
    assert resultado.direction is Direction.HOLD
    assert resultado.confidence == pytest.approx(0.0)
    assert resultado.stop_loss is None


def test_three_macd_com_nan_devolve_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Indicador em aquecimento apesar do comprimento mínimo: ausência de informação."""
    strategy = ThreeMacdStrategy(buff_size=_BUFF)
    m3_com_nan = list(_M3_BUY_P1)
    m3_com_nan[5] = float("nan")
    _patch_macd_series_por_slow(monkeypatch, strategy, _M1_BUY_P1, _M2_BUY_P1, m3_com_nan)

    assert strategy.evaluate(_candles_from_closes([100.0] * 200)) is None


def test_three_macd_name_e_3macd() -> None:
    assert ThreeMacdStrategy().name == "3macd"


def test_registry_contem_3macd() -> None:
    assert "3macd" in STRATEGY_REGISTRY


def test_build_enabled_strategies_3macd() -> None:
    estrategias = build_enabled_strategies(["3macd"])

    assert len(estrategias) == 1
    assert estrategias[0].name == "3macd"
    assert isinstance(estrategias[0], ThreeMacdStrategy)
