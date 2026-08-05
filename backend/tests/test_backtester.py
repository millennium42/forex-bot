"""Testes do backtester.

O backtester emula a passagem de tempo e a execução de ordens com o mesmo
pipeline de decisão usado em produção, calculando métricas idênticas às do
promotion gate.
"""

from __future__ import annotations

import math

import pandas as pd

from backend.analysis.technical_analyzer import minimum_candles
from backend.learning.backtester import Backtester
from backend.tests.test_technical_analyzer import downtrend, flat, uptrend


def test_serie_curta_nao_gera_trades() -> None:
    # Menos candles do que o necessário para o TechnicalAnalyzer
    curta = flat(minimum_candles() - 1)
    metrics = Backtester().run(curta)

    assert metrics.total_trades == 0
    assert metrics.win_rate == 0.0
    assert metrics.max_drawdown_pct == 0.0


def test_backtester_em_serie_plana() -> None:
    # Série plana gera score neutro, portanto hold, nenhum trade
    metrics = Backtester().run(flat(200))
    assert metrics.total_trades == 0
    assert metrics.profit_factor == 0.0


def test_backtester_em_tendencia() -> None:
    # Numa tendência forte e contínua, os indicadores (RSI/Bollinger)
    # vão apontar overbought/oversold e tentar operar contra a tendência,
    # gerando sinais de SELL na alta e BUY na baixa.
    # O stop loss será atingido repetidamente, gerando loss trades.
    # Isso testa perfeitamente se o mecanismo de SL e tracking de ordens funciona.

    # 200 candles em uptrend constante
    df = uptrend(200)

    bt = Backtester(initial_balance=10000.0)
    metrics = bt.run(df)

    # Houveram trades porque a tendência gerou sinais definitivos
    assert metrics.total_trades > 0
    # Como operou contra a tendência que não para de subir, o SL foi pego
    # (win rate será muito baixo ou 0)
    assert metrics.win_rate < 50.0
    assert metrics.max_drawdown_pct > 0.0

    # Em downtrend constante, tentará comprar e cairá no stop de novo.
    df_down = downtrend(200)
    metrics_down = Backtester(initial_balance=10000.0).run(df_down)

    assert metrics_down.total_trades > 0


def test_backtester_metrics_sane() -> None:
    # Uma série oscilante para ver win rate e lucro variados
    # Simulando um zig-zag perfeito que casa com o indicador
    closes = []
    base = 100.0
    for i in range(200):
        # Onda senoidal com período de 30 candles
        closes.append(base + math.sin(i / 30.0 * 2 * math.pi) * 5.0)

    df = pd.DataFrame(
        {
            "open": closes,
            "high": [c + 0.5 for c in closes],
            "low": [c - 0.5 for c in closes],
            "close": closes,
        }
    )

    bt = Backtester(initial_balance=10000.0)
    metrics = bt.run(df)

    # Deve haver alguns trades
    assert metrics.total_trades > 0
    # Deve reportar métricas em formato adequado
    assert 0.0 <= metrics.win_rate <= 100.0
    assert 0.0 <= metrics.max_drawdown_pct <= 100.0
    assert metrics.profit_factor >= 0.0
