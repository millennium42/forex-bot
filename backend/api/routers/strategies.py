"""Endpoint de performance por estratégia (história 39)."""

from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.api.dependencies import get_db
from backend.models.outcome import Outcome
from backend.models.signal import Signal

router = APIRouter(prefix="/strategies", tags=["Strategies"])


class StrategyPerformance(BaseModel):
    """Trades encerrados, taxa de acerto e P&L líquido de uma estratégia."""

    strategy: str
    trades: int
    win_rate: float
    net_pnl: float


@router.get("/performance", response_model=list[StrategyPerformance])
def get_strategy_performance(db: Session = Depends(get_db)) -> list[StrategyPerformance]:  # noqa: B008
    """Performance por estratégia a partir dos outcomes já registrados.

    `Signal.strategy` é a fonte de verdade (história 39): não há tabela de
    estratégia própria, a performance é medida juntando os outcomes já
    encerrados com o sinal que os originou.
    """
    linhas = (
        db.query(Signal.strategy, Outcome.was_correct, Outcome.pnl)
        .join(Outcome, Outcome.signal_id == Signal.id)
        .all()
    )

    por_estrategia: dict[str, list[tuple[bool, float]]] = defaultdict(list)
    for strategy, was_correct, pnl in linhas:
        por_estrategia[strategy].append((was_correct, pnl))

    resultado: list[StrategyPerformance] = []
    for strategy, entradas in sorted(por_estrategia.items()):
        total = len(entradas)
        vitorias = sum(1 for correto, _ in entradas if correto)
        resultado.append(
            StrategyPerformance(
                strategy=strategy,
                trades=total,
                win_rate=(vitorias / total * 100.0) if total else 0.0,
                net_pnl=sum(pnl for _, pnl in entradas),
            )
        )
    return resultado
