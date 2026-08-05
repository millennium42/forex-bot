"""Validação dos critérios para promoção de conta demo para real.

Verifica win rate, sharpe, max drawdown, profit factor e o desvio entre
backtest e forward test para aprovar a promoção de modo automático (que
sempre exigirá intervenção manual para efetivar).
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.learning.backtester import BacktestMetrics


@dataclass(frozen=True, slots=True)
class GateStatus:
    win_rate_ok: bool
    sharpe_ok: bool
    max_drawdown_ok: bool
    profit_factor_ok: bool
    deviation_ok: bool
    trades_ok: bool

    @property
    def passed(self) -> bool:
        """Indica se todos os gates foram aprovados."""
        return (
            self.win_rate_ok
            and self.sharpe_ok
            and self.max_drawdown_ok
            and self.profit_factor_ok
            and self.deviation_ok
            and self.trades_ok
        )


def evaluate_promotion_gates(
    forward_metrics: BacktestMetrics,
    backtest_metrics: BacktestMetrics,
) -> GateStatus:
    """
    Avalia se os resultados do forward test (demo) satisfazem os requisitos
    para liberar o modo de trading real.

    Critérios:
    - Mínimo de 200 trades
    - Win rate >= 55%
    - Sharpe >= 1.0
    - Max drawdown <= 10%
    - Profit factor >= 1.3
    - Desvio backtest vs forward test (Profit Factor) < 15%
    """
    # trades >= 200
    trades_ok = forward_metrics.total_trades >= 200

    # win_rate >= 55.0
    win_rate_ok = forward_metrics.win_rate >= 55.0

    # sharpe >= 1.0
    sharpe_ok = forward_metrics.sharpe >= 1.0

    # max_drawdown <= 10.0%
    max_drawdown_ok = forward_metrics.max_drawdown_pct <= 10.0

    # profit_factor >= 1.3
    profit_factor_ok = forward_metrics.profit_factor >= 1.3

    # desvio < 15%
    if backtest_metrics.profit_factor > 0:
        diff = abs(forward_metrics.profit_factor - backtest_metrics.profit_factor)
        pf_deviation = diff / backtest_metrics.profit_factor
    else:
        # Se backtest tem PF 0, consideramos desvio 0 se forward também é 0, ou infinito.
        pf_deviation = 0.0 if forward_metrics.profit_factor == 0.0 else float("inf")

    deviation_ok = pf_deviation < 0.15

    return GateStatus(
        win_rate_ok=win_rate_ok,
        sharpe_ok=sharpe_ok,
        max_drawdown_ok=max_drawdown_ok,
        profit_factor_ok=profit_factor_ok,
        deviation_ok=deviation_ok,
        trades_ok=trades_ok,
    )
