"""Endpoint de performance por estratégia (histórias 39 e 43)."""

from __future__ import annotations

from collections import defaultdict
from statistics import mean

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.api.dependencies import get_db
from backend.config import get_settings
from backend.models.outcome import Outcome
from backend.models.signal import Signal

router = APIRouter(prefix="/strategies", tags=["Strategies"])


class StrategyPerformance(BaseModel):
    """Trades encerrados, taxa de acerto, P&L e RR de uma estratégia.

    Todo campo além de `strategy`/`trades` é `None` quando a estratégia não
    tem trade encerrado o suficiente para calculá-lo — nunca 0, que seria
    indistinguível de um dado real.
    """

    strategy: str
    trades: int
    win_rate: float | None
    net_pnl: float | None
    avg_win: float | None
    avg_loss: float | None
    breakeven_win_rate: float | None


def _breakeven_win_rate(avg_win: float | None, avg_loss: float | None) -> float | None:
    """Win rate mínimo para não perder dinheiro, dado o RR observado.

    Sem nenhuma vitória registrada não há RR para embasar o cálculo (None).
    Sem nenhuma derrota, qualquer win rate observado já é lucrativo (0.0).
    """
    if avg_win is None:
        return None
    if avg_loss is None:
        return 0.0
    return avg_loss / (avg_win + avg_loss) * 100.0


def _performance_de(strategy: str, entradas: list[tuple[bool, float]]) -> StrategyPerformance:
    if not entradas:
        return StrategyPerformance(
            strategy=strategy,
            trades=0,
            win_rate=None,
            net_pnl=None,
            avg_win=None,
            avg_loss=None,
            breakeven_win_rate=None,
        )

    total = len(entradas)
    vitorias = sum(1 for correto, _ in entradas if correto)
    ganhos = [pnl for _, pnl in entradas if pnl > 0]
    perdas = [-pnl for _, pnl in entradas if pnl < 0]
    avg_win = mean(ganhos) if ganhos else None
    avg_loss = mean(perdas) if perdas else None

    return StrategyPerformance(
        strategy=strategy,
        trades=total,
        win_rate=vitorias / total * 100.0,
        net_pnl=sum(pnl for _, pnl in entradas),
        avg_win=avg_win,
        avg_loss=avg_loss,
        breakeven_win_rate=_breakeven_win_rate(avg_win, avg_loss),
    )


@router.get("/performance", response_model=list[StrategyPerformance])
def get_strategy_performance(db: Session = Depends(get_db)) -> list[StrategyPerformance]:  # noqa: B008
    """Performance por estratégia a partir dos outcomes já registrados.

    `Signal.strategy` é a fonte de verdade (história 39): não há tabela de
    estratégia própria, a performance é medida juntando os outcomes já
    encerrados com o sinal que os originou. Toda estratégia habilitada em
    `STRATEGIES_ENABLED` aparece na resposta mesmo sem trade encerrado ainda
    (história 43) — o dashboard não pode confundir "sem dado" com estratégia
    inexistente.
    """
    linhas = (
        db.query(Signal.strategy, Outcome.was_correct, Outcome.pnl)
        .join(Outcome, Outcome.signal_id == Signal.id)
        .all()
    )

    por_estrategia: dict[str, list[tuple[bool, float]]] = defaultdict(list)
    for strategy, was_correct, pnl in linhas:
        por_estrategia[strategy].append((was_correct, pnl))

    nomes = set(por_estrategia) | set(get_settings().strategies_enabled_list)
    return [_performance_de(nome, por_estrategia.get(nome, [])) for nome in sorted(nomes)]
