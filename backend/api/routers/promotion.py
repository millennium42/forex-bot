"""Endpoint para verificação do status dos gates de promoção."""

from __future__ import annotations

import statistics

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.api.dependencies import get_db
from backend.learning.backtester import BacktestMetrics
from backend.learning.promotion_gate import GateStatus, evaluate_promotion_gates
from backend.models.outcome import Outcome
from backend.models.trade import Trade

router = APIRouter(prefix="/promotion", tags=["Promotion"])


class GateCriterion(BaseModel):
    """Um critério de promoção com valor atual, alvo e se foi atingido.

    `value=None` quando ainda não há dado suficiente para calculá-lo (nenhum
    trade encerrado) — o dashboard mostra travessão, não zero.
    """

    key: str
    label: str
    value: float | None
    target: float
    unit: str
    higher_is_better: bool
    passed: bool


class PromotionStatusResponse(BaseModel):
    gates: GateStatus
    criteria: list[GateCriterion]
    total_trades: int


def _forward_metrics_from_db(db: Session) -> BacktestMetrics:
    """Métricas do forward test (conta demo) a partir dos outcomes reais.

    Mesma aritmética do `Backtester._compute_metrics` (história 14): não
    reimplementa a fórmula, só troca a fonte dos retornos — trade simulado no
    backtest, outcome real no forward test.
    """
    outcomes = (
        db.query(Outcome)
        .join(Trade, Outcome.trade_id == Trade.id)
        .filter(Trade.trading_mode == "demo")
        .order_by(Outcome.created_at.asc())
        .all()
    )
    if not outcomes:
        return BacktestMetrics(0.0, 0.0, 0.0, 0.0, 0)

    wins = sum(1 for o in outcomes if o.was_correct)
    win_rate = (wins / len(outcomes)) * 100.0

    gross_profit = sum(o.pnl_pct for o in outcomes if o.pnl_pct > 0)
    gross_loss = sum(abs(o.pnl_pct) for o in outcomes if o.pnl_pct < 0)
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")

    returns = [o.pnl_pct for o in outcomes]
    avg_ret = statistics.fmean(returns)
    std_ret = statistics.pstdev(returns) if len(returns) > 1 else 0.0
    sharpe = avg_ret / std_ret if std_ret > 0 else 0.0

    equity = 1.0
    peak = equity
    max_dd = 0.0
    for outcome in outcomes:
        equity *= 1 + outcome.pnl_pct
        peak = max(peak, equity)
        drawdown = (peak - equity) / peak if peak > 0 else 0.0
        max_dd = max(max_dd, drawdown)

    return BacktestMetrics(
        win_rate=win_rate,
        sharpe=sharpe,
        max_drawdown_pct=max_dd * 100.0,
        profit_factor=profit_factor,
        total_trades=len(outcomes),
    )


@router.get("/status", response_model=PromotionStatusResponse)
def get_promotion_status(db: Session = Depends(get_db)) -> PromotionStatusResponse:  # noqa: B008
    """
    Retorna o status atual dos gates de promoção de demo para real.

    A promoção não ocorre automaticamente; a API apenas reporta
    se as condições para promoção manual foram satisfeitas.
    """
    forward_metrics = _forward_metrics_from_db(db)
    # Sem backtest persistido para comparar (história 14 não grava o resultado
    # em banco): o desvio fica indefinido até essa lacuna ser fechada, então
    # comparamos com um profit factor nulo — deviation_ok só passa se o
    # forward também for zero, nunca por acidente.
    backtest_metrics = BacktestMetrics(
        win_rate=0.0, sharpe=0.0, max_drawdown_pct=0.0, profit_factor=0.0, total_trades=0
    )

    gates = evaluate_promotion_gates(forward_metrics, backtest_metrics)
    has_data = forward_metrics.total_trades > 0

    criteria = [
        GateCriterion(
            key="win_rate",
            label="Taxa de acerto",
            value=forward_metrics.win_rate if has_data else None,
            target=55.0,
            unit="%",
            higher_is_better=True,
            passed=gates.win_rate_ok,
        ),
        GateCriterion(
            key="sharpe",
            label="Sharpe",
            value=forward_metrics.sharpe if has_data else None,
            target=1.0,
            unit="",
            higher_is_better=True,
            passed=gates.sharpe_ok,
        ),
        GateCriterion(
            key="max_drawdown",
            label="Drawdown máximo",
            value=forward_metrics.max_drawdown_pct if has_data else None,
            target=10.0,
            unit="%",
            higher_is_better=False,
            passed=gates.max_drawdown_ok,
        ),
        GateCriterion(
            key="profit_factor",
            label="Fator de lucro",
            value=forward_metrics.profit_factor if has_data else None,
            target=1.3,
            unit="",
            higher_is_better=True,
            passed=gates.profit_factor_ok,
        ),
        GateCriterion(
            key="trades",
            label="Trades encerrados",
            value=float(forward_metrics.total_trades),
            target=200.0,
            unit="",
            higher_is_better=True,
            passed=gates.trades_ok,
        ),
        GateCriterion(
            key="deviation",
            label="Desvio backtest vs. forward test",
            value=None,
            target=15.0,
            unit="%",
            higher_is_better=False,
            passed=gates.deviation_ok,
        ),
    ]

    return PromotionStatusResponse(
        gates=gates, criteria=criteria, total_trades=forward_metrics.total_trades
    )
