"""Testes para a avaliação dos critérios de promoção de modo demo para real."""

from __future__ import annotations

import pytest

from backend.learning.backtester import BacktestMetrics
from backend.learning.promotion_gate import evaluate_promotion_gates


@pytest.fixture
def default_backtest_metrics() -> BacktestMetrics:
    return BacktestMetrics(
        win_rate=55.0,
        sharpe=1.2,
        max_drawdown_pct=8.0,
        profit_factor=1.4,
        total_trades=500,
    )


def test_promotion_gate_approved(default_backtest_metrics: BacktestMetrics) -> None:
    """Deve passar quando todos os critérios forem estritamente ou superiormente atingidos."""
    forward_metrics = BacktestMetrics(
        win_rate=56.0,
        sharpe=1.5,
        max_drawdown_pct=9.0,
        profit_factor=1.4,
        total_trades=205,
    )

    status = evaluate_promotion_gates(forward_metrics, default_backtest_metrics)

    assert status.win_rate_ok is True
    assert status.sharpe_ok is True
    assert status.max_drawdown_ok is True
    assert status.profit_factor_ok is True
    assert status.deviation_ok is True
    assert status.trades_ok is True

    assert status.passed is True


def test_promotion_gate_fails_on_win_rate(default_backtest_metrics: BacktestMetrics) -> None:
    """Deve falhar se o win rate for menor que 55%."""
    forward_metrics = BacktestMetrics(
        win_rate=54.9,
        sharpe=1.5,
        max_drawdown_pct=9.0,
        profit_factor=1.4,
        total_trades=205,
    )

    status = evaluate_promotion_gates(forward_metrics, default_backtest_metrics)
    assert status.win_rate_ok is False
    assert status.passed is False


def test_promotion_gate_fails_on_sharpe(default_backtest_metrics: BacktestMetrics) -> None:
    """Deve falhar se o sharpe for menor que 1.0."""
    forward_metrics = BacktestMetrics(
        win_rate=56.0,
        sharpe=0.99,
        max_drawdown_pct=9.0,
        profit_factor=1.4,
        total_trades=205,
    )

    status = evaluate_promotion_gates(forward_metrics, default_backtest_metrics)
    assert status.sharpe_ok is False
    assert status.passed is False


def test_promotion_gate_fails_on_drawdown(default_backtest_metrics: BacktestMetrics) -> None:
    """Deve falhar se o max drawdown ultrapassar 10%."""
    forward_metrics = BacktestMetrics(
        win_rate=56.0,
        sharpe=1.5,
        max_drawdown_pct=10.1,
        profit_factor=1.4,
        total_trades=205,
    )

    status = evaluate_promotion_gates(forward_metrics, default_backtest_metrics)
    assert status.max_drawdown_ok is False
    assert status.passed is False


def test_promotion_gate_fails_on_profit_factor(default_backtest_metrics: BacktestMetrics) -> None:
    """Deve falhar se o profit factor for menor que 1.3."""
    forward_metrics = BacktestMetrics(
        win_rate=56.0,
        sharpe=1.5,
        max_drawdown_pct=9.0,
        profit_factor=1.29,
        total_trades=205,
    )

    status = evaluate_promotion_gates(forward_metrics, default_backtest_metrics)
    assert status.profit_factor_ok is False
    assert status.passed is False


def test_promotion_gate_fails_on_total_trades(default_backtest_metrics: BacktestMetrics) -> None:
    """Deve falhar se houver menos de 200 trades (invalidação estatística)."""
    forward_metrics = BacktestMetrics(
        win_rate=60.0,
        sharpe=2.0,
        max_drawdown_pct=5.0,
        profit_factor=1.8,
        total_trades=199,
    )

    status = evaluate_promotion_gates(forward_metrics, default_backtest_metrics)
    assert status.trades_ok is False
    assert status.passed is False


def test_promotion_gate_fails_on_deviation(default_backtest_metrics: BacktestMetrics) -> None:
    """Deve falhar se o profit factor desviar mais de 15% do backtest."""
    # Profit factor no backtest = 1.4
    # Margem de tolerância (15%): ~0.21 -> limite inferior 1.19, limite superior 1.61

    # Pf muito alto (desvio para cima)
    forward_metrics_high = BacktestMetrics(
        win_rate=56.0,
        sharpe=1.5,
        max_drawdown_pct=9.0,
        profit_factor=1.62,
        total_trades=205,
    )
    status = evaluate_promotion_gates(forward_metrics_high, default_backtest_metrics)
    assert status.deviation_ok is False
    assert status.passed is False

    # Pf muito baixo (desvio para baixo, mas ainda satisfaz PF >= 1.3 -> 1.31 desvia menos que 15%?
    # 1.4 * 0.85 = 1.19. Como o PF tem que ser >= 1.3, o limite duro é 1.3 de qualquer forma.

    # Se o backtest teve PF = 2.0 (15% margem = 1.7)
    high_pf_backtest = BacktestMetrics(
        win_rate=55.0, sharpe=1.2, max_drawdown_pct=8.0, profit_factor=2.0, total_trades=500
    )
    forward_metrics_low = BacktestMetrics(
        win_rate=56.0,
        sharpe=1.5,
        max_drawdown_pct=9.0,
        profit_factor=1.69,  # PF > 1.3, ok. Mas desvia 15.5% (2.0 a 1.69)
        total_trades=205,
    )
    status2 = evaluate_promotion_gates(forward_metrics_low, high_pf_backtest)
    assert status2.profit_factor_ok is True
    assert status2.deviation_ok is False
    assert status2.passed is False
