"""Backtester de estratégias: replay de histórico MT5 com o mesmo pipeline do live."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from backend.analysis.signal_fusion import fuse_signals
from backend.analysis.technical_analyzer import TechnicalAnalyzer, minimum_candles
from backend.models.enums import Direction, Side


@dataclass(frozen=True, slots=True)
class BacktestMetrics:
    win_rate: float
    sharpe: float
    max_drawdown_pct: float
    profit_factor: float
    total_trades: int


@dataclass(frozen=True, slots=True)
class SimulatedTrade:
    side: Side
    entry_price: float
    entry_index: int
    exit_price: float
    exit_index: int
    pnl_pct: float
    was_correct: bool


class Backtester:
    """Executa o backtest de uma série histórica usando o pipeline de decisão real."""

    def __init__(self, initial_balance: float = 100000.0) -> None:
        self.initial_balance = initial_balance
        self.analyzer = TechnicalAnalyzer()

    def run(self, candles: pd.DataFrame) -> BacktestMetrics:
        """
        Roda o backtest numa série OHLC.
        Requisito: as mesmas métricas de saída do promotion gate.
        Requisito: sem branch de código específico de teste no pipeline (usa analyzer real).
        """
        if len(candles) < minimum_candles():
            return BacktestMetrics(0.0, 0.0, 0.0, 0.0, 0)

        trades: list[SimulatedTrade] = []
        equity = self.initial_balance
        peak_equity = equity
        max_dd = 0.0

        current_position: dict[str, Any] | None = None

        # Iterar a partir do momento em que temos candles suficientes
        min_idx = minimum_candles()

        # O backtest precisa iterar simulando o tempo passando.
        # Em produção, o TechnicalAnalyzer recebe a janela até o momento atual.
        for i in range(min_idx, len(candles)):
            # Pega janela de candles até i (inclusive)
            window = candles.iloc[: i + 1]

            # 1. Pipeline de decisão do live
            tech_score = self.analyzer.analyze(window)
            fused = fuse_signals(tech_score, sentiment=None)

            current_row = candles.iloc[i]
            current_low = float(current_row["low"])
            current_high = float(current_row["high"])
            current_close = float(current_row["close"])

            # 2. Emulação do fechamento
            if current_position is not None:
                closed = False
                exit_price = 0.0

                if current_position["side"] == Side.BUY:
                    if current_position["sl"] is not None and current_low <= current_position["sl"]:
                        closed = True
                        exit_price = current_position["sl"]
                    elif (
                        current_position["tp"] is not None
                        and current_high >= current_position["tp"]
                    ):
                        closed = True
                        exit_price = current_position["tp"]
                    elif fused.direction == Direction.SELL:
                        closed = True
                        exit_price = current_close
                else:
                    if (
                        current_position["sl"] is not None
                        and current_high >= current_position["sl"]
                    ):
                        closed = True
                        exit_price = current_position["sl"]
                    elif (
                        current_position["tp"] is not None and current_low <= current_position["tp"]
                    ):
                        closed = True
                        exit_price = current_position["tp"]
                    elif fused.direction == Direction.BUY:
                        closed = True
                        exit_price = current_close

                if closed:
                    entry_price = current_position["entry_price"]
                    if current_position["side"] == Side.BUY:
                        pnl_pct = (exit_price - entry_price) / entry_price
                    else:
                        pnl_pct = (entry_price - exit_price) / entry_price

                    was_correct = pnl_pct > 0.0
                    trades.append(
                        SimulatedTrade(
                            side=current_position["side"],
                            entry_price=entry_price,
                            entry_index=current_position["entry_index"],
                            exit_price=exit_price,
                            exit_index=i,
                            pnl_pct=pnl_pct,
                            was_correct=was_correct,
                        )
                    )

                    equity += equity * pnl_pct
                    if equity > peak_equity:
                        peak_equity = equity
                    dd = (peak_equity - equity) / peak_equity
                    if dd > max_dd:
                        max_dd = dd

                    current_position = None

            # 3. Emulação da abertura
            if current_position is None:
                if fused.direction == Direction.BUY:
                    atr = tech_score.indicators.atr if tech_score.indicators else 0.0
                    sl = current_close - (atr * 1.5) if atr > 0 else None
                    tp = current_close + (atr * 3.0) if atr > 0 else None
                    current_position = {
                        "side": Side.BUY,
                        "entry_price": current_close,
                        "entry_index": i,
                        "sl": sl,
                        "tp": tp,
                    }
                elif fused.direction == Direction.SELL:
                    atr = tech_score.indicators.atr if tech_score.indicators else 0.0
                    sl = current_close + (atr * 1.5) if atr > 0 else None
                    tp = current_close - (atr * 3.0) if atr > 0 else None
                    current_position = {
                        "side": Side.SELL,
                        "entry_price": current_close,
                        "entry_index": i,
                        "sl": sl,
                        "tp": tp,
                    }

        return self._compute_metrics(trades, max_dd)

    def _compute_metrics(self, trades: list[SimulatedTrade], max_dd: float) -> BacktestMetrics:
        if not trades:
            return BacktestMetrics(0.0, 0.0, 0.0, 0.0, 0)

        wins = sum(1 for t in trades if t.was_correct)
        win_rate = (wins / len(trades)) * 100.0

        gross_profit = sum(t.pnl_pct for t in trades if t.pnl_pct > 0)
        gross_loss = sum(abs(t.pnl_pct) for t in trades if t.pnl_pct < 0)
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")

        returns = [t.pnl_pct for t in trades]
        avg_ret = float(np.mean(returns))
        std_ret = float(np.std(returns)) if len(returns) > 1 else 0.0

        # Sharpe ratio simplificado anualizado? Como não temos datetime no trade,
        # vamos fazer um sharpe simples por trade: avg_ret / std_ret (assumindo risk free = 0)
        sharpe = avg_ret / std_ret if std_ret > 0 else 0.0

        return BacktestMetrics(
            win_rate=win_rate,
            sharpe=sharpe,
            max_drawdown_pct=max_dd * 100.0,
            profit_factor=profit_factor,
            total_trades=len(trades),
        )
