"""Trava de drawdown acumulado, medido a partir do pico de equity já observado.

O pico persiste no `audit_log` como evento `EQUITY_PEAK_UPDATED`: cada vez que
o equity bate um novo máximo, um evento novo é gravado. Como o pico só cresce,
o evento mais recente já É o pico — sem tabela dedicada e sem estado em
memória, que se perderia a cada reinício do processo (lucro seguido de queda
tem que contar a partir do pico, não do saldo com que o processo começou a
rodar dessa vez).

O bloqueio segue o mesmo padrão do kill switch (história 18): dois tipos de
evento (triggered/reset), o mais recente decide o estado, e reset só acontece
por ação manual — a diferença é o gatilho automático que dispara aqui.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.audit_log import AuditLog
from backend.models.enums import AuditEventType


def get_peak_equity(db: Session) -> float | None:
    """Maior equity já observado, ou `None` se nenhum ciclo gravou um ainda."""
    last_event = db.scalar(
        select(AuditLog)
        .where(AuditLog.event_type == AuditEventType.EQUITY_PEAK_UPDATED)
        .order_by(AuditLog.id.desc())
        .limit(1)
    )
    if last_event is None:
        return None
    return float(last_event.payload["equity"])


def record_equity(db: Session, equity: float) -> float:
    """Atualiza o pico se `equity` superar o valor conhecido; devolve o pico vigente."""
    peak = get_peak_equity(db)
    if peak is not None and equity <= peak:
        return peak

    event = AuditLog(event_type=AuditEventType.EQUITY_PEAK_UPDATED, payload={"equity": equity})
    db.add(event)
    db.commit()
    return equity


def is_drawdown_block_active(db: Session) -> bool:
    """Verifica se o bloqueio por drawdown está ativo, pelo último evento gravado."""
    last_event = db.scalar(
        select(AuditLog.event_type)
        .where(
            AuditLog.event_type.in_(
                [AuditEventType.DRAWDOWN_LIMIT_TRIGGERED, AuditEventType.DRAWDOWN_LIMIT_RESET]
            )
        )
        .order_by(AuditLog.id.desc())
        .limit(1)
    )
    return last_event == AuditEventType.DRAWDOWN_LIMIT_TRIGGERED


def trigger_drawdown_block(db: Session, reason: str, actor: str = "system") -> None:
    """Ativa o bloqueio por drawdown, impedindo o envio de novas ordens."""
    if is_drawdown_block_active(db):
        return

    event = AuditLog(
        event_type=AuditEventType.DRAWDOWN_LIMIT_TRIGGERED,
        actor=actor,
        payload={"reason": reason},
    )
    db.add(event)
    db.commit()


def reset_drawdown_block(db: Session, actor: str) -> None:
    """Desativa o bloqueio por drawdown (requer ação manual com identificação do ator)."""
    if not is_drawdown_block_active(db):
        return

    event = AuditLog(
        event_type=AuditEventType.DRAWDOWN_LIMIT_RESET,
        actor=actor,
        payload={"reason": "Manual reset"},
    )
    db.add(event)
    db.commit()
