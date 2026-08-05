"""Endpoint para verificação do status dos gates de promoção."""

from __future__ import annotations

from fastapi import APIRouter

from backend.learning.backtester import BacktestMetrics
from backend.learning.promotion_gate import GateStatus, evaluate_promotion_gates

router = APIRouter(prefix="/promotion", tags=["Promotion"])


@router.get("/status", response_model=GateStatus)
def get_promotion_status() -> GateStatus:
    """
    Retorna o status atual dos gates de promoção de demo para real.

    A promoção não ocorre automaticamente; a API apenas reporta
    se as condições para promoção manual foram satisfeitas.
    """
    # TODO: Integrar com a sessão do DB para obter outcomes reais (forward test)
    # e buscar os dados de métricas de backtest salvos.
    # Por ora, retorna fallback vazio para validar a estrutura do endpoint.
    forward_metrics = BacktestMetrics(
        win_rate=0.0, sharpe=0.0, max_drawdown_pct=0.0, profit_factor=0.0, total_trades=0
    )
    backtest_metrics = BacktestMetrics(
        win_rate=0.0, sharpe=0.0, max_drawdown_pct=0.0, profit_factor=0.0, total_trades=0
    )

    return evaluate_promotion_gates(forward_metrics, backtest_metrics)
