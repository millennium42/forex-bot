"""Testes do analisador técnico.

Todas as séries são construídas por fórmula — nada de random e nada de dado de
broker. Rodar duas vezes tem que dar exatamente o mesmo número.
"""

from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pytest

from backend.analysis.technical_analyzer import (
    ENGINE,
    IndicatorSnapshot,
    TechnicalAnalyzer,
    TechnicalScore,
    _confidence,
    _score_macd,
    _score_momentum,
    _score_relative_volatility,
    _score_reversion,
    _score_trend_strength,
    compute_indicators,
    minimum_candles,
    neutral,
)

CANDLES = 80


def build_frame(closes: list[float], amplitude: float = 0.5) -> pd.DataFrame:
    """OHLC determinístico em volta de uma lista de fechamentos."""
    return pd.DataFrame(
        {
            "open": [c - amplitude / 2 for c in closes],
            "high": [c + amplitude for c in closes],
            "low": [c - amplitude for c in closes],
            "close": closes,
        }
    )


def uptrend(n: int = CANDLES) -> pd.DataFrame:
    return build_frame([100.0 + i * 0.5 for i in range(n)])


def downtrend(n: int = CANDLES) -> pd.DataFrame:
    return build_frame([100.0 - i * 0.5 for i in range(n)])


def flat(n: int = CANDLES) -> pd.DataFrame:
    return build_frame([100.0] * n)


def expanding_trend(sinal: float, n: int = CANDLES) -> pd.DataFrame:
    """Alta/baixa com volatilidade em expansão: amplitude cresce a cada candle.

    Exercita `relative_volatility`, que precisa de ATR subindo em relação à
    própria média — o `uptrend`/`downtrend` de amplitude fixa não move esse
    fator (fica em ~0), então precisa de uma fixture dedicada.
    """
    closes = [100.0 + sinal * i * 0.5 for i in range(n)]
    amplitudes = [0.1 + i * 0.05 for i in range(n)]
    return pd.DataFrame(
        {
            "open": [c - a / 2 for c, a in zip(closes, amplitudes, strict=True)],
            "high": [c + a for c, a in zip(closes, amplitudes, strict=True)],
            "low": [c - a for c, a in zip(closes, amplitudes, strict=True)],
            "close": closes,
        }
    )


BASE_SNAPSHOT = IndicatorSnapshot(
    close=100.0,
    rsi=50.0,
    macd=0.0,
    macd_signal=0.0,
    macd_diff=0.0,
    bb_high=102.0,
    bb_low=98.0,
    bb_pct=0.5,
    atr=1.0,
    momentum_5=0.0,
    momentum_10=0.0,
    momentum_20=0.0,
    reversion_mean=100.0,
    reversion_std=1.0,
    atr_baseline=1.0,
    adx=0.0,
    adx_pos=0.0,
    adx_neg=0.0,
)


def snapshot(**overrides: float) -> IndicatorSnapshot:
    return replace(BASE_SNAPSHOT, **overrides)


# --------------------------------------------------------------------------- #
# Faixas contratadas
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("frame", [uptrend(), downtrend(), flat()], ids=["alta", "baixa", "plana"])
def test_score_sempre_dentro_da_faixa(frame: pd.DataFrame) -> None:
    resultado = TechnicalAnalyzer().analyze(frame)
    assert -1.0 <= resultado.score <= 1.0
    assert 0.0 <= resultado.confidence <= 1.0


def test_construcao_faz_clamp() -> None:
    """Nem valor fora de faixa injetado direto escapa — igual ao SentimentScore."""
    assert TechnicalScore(score=7.5, confidence=9.0).score == 1.0
    assert TechnicalScore(score=-7.5, confidence=9.0).score == -1.0
    assert TechnicalScore(score=0.0, confidence=-3.0).confidence == 0.0


def test_neutral_nao_pesa() -> None:
    resultado = neutral()
    assert resultado.score == 0.0
    assert resultado.confidence == 0.0
    assert resultado.engine == ENGINE


# --------------------------------------------------------------------------- #
# Direção dos componentes
# --------------------------------------------------------------------------- #
def test_alta_sustentada_da_momento_positivo_e_reversao_negativa() -> None:
    """Tendência de alta: MACD compra o momento, RSI e Bollinger dizem esticado."""
    componentes = TechnicalAnalyzer().analyze(uptrend()).components
    assert componentes["macd"] > 0
    assert componentes["rsi"] < 0
    assert componentes["bollinger"] < 0


def test_baixa_sustentada_e_o_espelho_da_alta() -> None:
    componentes = TechnicalAnalyzer().analyze(downtrend()).components
    assert componentes["macd"] < 0
    assert componentes["rsi"] > 0
    assert componentes["bollinger"] > 0


# --------------------------------------------------------------------------- #
# Alpha factors (história 35)
# --------------------------------------------------------------------------- #
def test_componentes_incluem_os_quatro_alpha_factors() -> None:
    componentes = TechnicalAnalyzer().analyze(uptrend()).components
    assert set(componentes) == {
        "rsi",
        "macd",
        "bollinger",
        "momentum",
        "mean_reversion",
        "relative_volatility",
        "trend_strength",
    }


def test_alpha_factors_alta_sustentada() -> None:
    """Alta linear: momento positivo, esticado (reversão vende), tendência forte."""
    componentes = TechnicalAnalyzer().analyze(uptrend()).components
    assert componentes["momentum"] > 0
    assert componentes["mean_reversion"] < 0
    assert componentes["trend_strength"] > 0


def test_alpha_factors_baixa_e_o_espelho_da_alta() -> None:
    componentes = TechnicalAnalyzer().analyze(downtrend()).components
    assert componentes["momentum"] < 0
    assert componentes["mean_reversion"] > 0
    assert componentes["trend_strength"] < 0


def test_lateral_nao_produz_fator_forjado() -> None:
    """Série sem variação (RSI indefinido) já vira `neutral()` — vale para todo
    fator novo também, porque todos passam pelo mesmo laço de checagem de NaN.
    """
    assert compute_indicators(flat()) is None
    resultado = TechnicalAnalyzer().analyze(flat())
    assert resultado == neutral()
    assert resultado.components == {}


def test_volatilidade_relativa_precisa_de_amplitude_em_expansao() -> None:
    """Alta/baixa com amplitude fixa (uptrend/downtrend) não move o fator."""
    componentes = TechnicalAnalyzer().analyze(uptrend()).components
    assert componentes["relative_volatility"] == pytest.approx(0.0, abs=1e-9)


def test_volatilidade_relativa_expande_na_direcao_do_preco() -> None:
    alta = TechnicalAnalyzer().analyze(expanding_trend(sinal=1.0)).components
    baixa = TechnicalAnalyzer().analyze(expanding_trend(sinal=-1.0)).components
    assert alta["relative_volatility"] > 0
    assert baixa["relative_volatility"] < 0
    assert alta["relative_volatility"] == pytest.approx(-baixa["relative_volatility"])


def test_momentum_multi_janela_direcao_e_simetria() -> None:
    positivo = _score_momentum(snapshot(momentum_5=1.0, momentum_10=2.0, momentum_20=4.0, atr=1.0))
    negativo = _score_momentum(
        snapshot(momentum_5=-1.0, momentum_10=-2.0, momentum_20=-4.0, atr=1.0)
    )
    assert positivo > 0
    assert negativo < 0
    assert positivo == pytest.approx(-negativo)


def test_momentum_sem_atr_nao_inventa_escala() -> None:
    valor = _score_momentum(snapshot(momentum_5=5.0, momentum_10=5.0, momentum_20=5.0, atr=0.0))
    assert valor == 0.0


def test_reversao_extremo_acima_da_media_puxa_para_venda() -> None:
    venda = _score_reversion(snapshot(close=110.0, reversion_mean=100.0, reversion_std=5.0))
    compra = _score_reversion(snapshot(close=90.0, reversion_mean=100.0, reversion_std=5.0))
    assert venda < 0
    assert compra > 0
    assert venda == pytest.approx(-compra)


def test_reversao_sem_desvio_nao_inventa_escala() -> None:
    assert _score_reversion(snapshot(reversion_std=0.0)) == 0.0


def test_relative_volatility_sem_baseline_nao_inventa_escala() -> None:
    assert _score_relative_volatility(snapshot(atr_baseline=0.0)) == 0.0


def test_relative_volatility_no_preco_da_media_fica_neutro() -> None:
    """Sem direção (preço exatamente na média), não há lado para a expansão puxar."""
    assert _score_relative_volatility(snapshot(close=100.0, reversion_mean=100.0)) == 0.0


def test_forca_de_tendencia_segue_o_di_dominante() -> None:
    compra = _score_trend_strength(snapshot(adx=40.0, adx_pos=30.0, adx_neg=10.0))
    venda = _score_trend_strength(snapshot(adx=40.0, adx_pos=10.0, adx_neg=30.0))
    assert compra > 0
    assert venda < 0
    assert compra == pytest.approx(-venda)


def test_forca_de_tendencia_sem_di_nao_inventa_escala() -> None:
    assert _score_trend_strength(snapshot(adx_pos=0.0, adx_neg=0.0)) == 0.0


def test_alta_e_baixa_produzem_scores_simetricos() -> None:
    alta = TechnicalAnalyzer().analyze(uptrend())
    baixa = TechnicalAnalyzer().analyze(downtrend())
    assert alta.score == pytest.approx(-baixa.score, abs=1e-9)


def test_mesma_serie_produz_o_mesmo_resultado() -> None:
    primeiro = TechnicalAnalyzer().analyze(uptrend())
    segundo = TechnicalAnalyzer().analyze(uptrend())
    assert primeiro.score == segundo.score
    assert primeiro.confidence == segundo.confidence


# --------------------------------------------------------------------------- #
# Indicadores
# --------------------------------------------------------------------------- #
def test_snapshot_traz_os_quatro_indicadores() -> None:
    indicadores = compute_indicators(uptrend())
    assert indicadores is not None
    assert indicadores.rsi == pytest.approx(100.0)  # série monotônica: só ganhos
    assert indicadores.macd_diff > 0
    assert indicadores.bb_low < indicadores.close < indicadores.bb_high
    assert indicadores.atr > 0
    assert indicadores.close == pytest.approx(100.0 + (CANDLES - 1) * 0.5)


def test_serie_curta_nao_produz_indicador() -> None:
    curta = uptrend(minimum_candles() - 1)
    assert compute_indicators(curta) is None
    assert TechnicalAnalyzer().analyze(curta) == neutral()


def test_serie_plana_deixa_indicador_em_nan_e_vira_neutro() -> None:
    """RSI sem ganho nem perda é indefinido; indicador em NaN não é indicador zero."""
    assert compute_indicators(flat()) is None
    assert TechnicalAnalyzer().analyze(flat()) == neutral()


def test_nan_no_ultimo_candle_vira_neutro() -> None:
    frame = uptrend()
    frame.loc[frame.index[-1], "close"] = float("nan")
    assert TechnicalAnalyzer().analyze(frame) == neutral()


def test_coluna_ohlc_ausente_levanta() -> None:
    """Schema errado é bug de programação, não ausência de dado de mercado."""
    frame = uptrend().drop(columns=["low"])
    with pytest.raises(ValueError, match="low"):
        compute_indicators(frame)


# --------------------------------------------------------------------------- #
# Normalização e confiança
# --------------------------------------------------------------------------- #
def test_macd_e_normalizado_por_atr() -> None:
    """Mesmo histograma em pares de volatilidade diferente não vale o mesmo."""
    volatil = _score_macd(snapshot(macd_diff=1.0, atr=4.0))
    calmo = _score_macd(snapshot(macd_diff=1.0, atr=1.0))
    assert volatil == pytest.approx(0.25)
    assert calmo == pytest.approx(1.0)
    assert volatil < calmo


def test_macd_sem_atr_nao_inventa_escala() -> None:
    assert _score_macd(snapshot(macd_diff=5.0, atr=0.0)) == 0.0


def test_componentes_opostos_derrubam_a_confianca() -> None:
    assert _confidence({"a": 1.0, "b": -1.0}) == 0.0
    assert _confidence({"a": 0.0, "b": 0.0}) == 0.0
    assert _confidence({}) == 0.0


def test_componentes_concordantes_sustentam_a_confianca() -> None:
    concordante = _confidence({"a": 0.8, "b": 0.8, "c": 0.8})
    fraco = _confidence({"a": 0.1, "b": 0.1, "c": 0.1})
    assert concordante == pytest.approx(0.8)
    assert fraco < concordante


def test_confianca_alta_exige_indicadores_no_mesmo_lado() -> None:
    """Na alta linear os indicadores brigam entre si — a confiança tem que cair."""
    resultado = TechnicalAnalyzer().analyze(uptrend())
    assert resultado.confidence < 1.0
    assert resultado.indicators is not None
