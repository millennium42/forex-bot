"""Tarefas Celery da camada de execução."""

from __future__ import annotations

from backend.celery_app import celery_app
from backend.collection.mt5_client import MT5Client
from backend.db import session_scope
from backend.execution.position_tracker import PositionTracker


@celery_app.task(name="execution.track_positions")  # type: ignore[untyped-decorator]
def track_positions_task() -> None:
    """Executa a reconciliação de posições do PositionTracker."""
    with session_scope() as session:
        # Usa um client MT5 descartável; se for longo pode manter singleton.
        with MT5Client() as client:
            tracker = PositionTracker(client, session)
            tracker.reconcile()
