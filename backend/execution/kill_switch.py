"""Funções para manipular o estado persistente do kill switch via banco de dados."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.audit_log import AuditLog
from backend.models.enums import AuditEventType


def is_kill_switch_active(db: Session) -> bool:
    """Verifica se o kill switch (manual ou por perda diária) está ativo.

    Baseia-se no último evento registrado no log de auditoria.
    """
    last_event = db.scalar(
        select(AuditLog.event_type)
        .where(
            AuditLog.event_type.in_(
                [AuditEventType.KILL_SWITCH_TRIGGERED, AuditEventType.KILL_SWITCH_RESET]
            )
        )
        .order_by(AuditLog.id.desc())
        .limit(1)
    )
    return last_event == AuditEventType.KILL_SWITCH_TRIGGERED


def trigger_kill_switch(db: Session, reason: str, actor: str = "system") -> None:
    """Ativa o kill switch, impedindo o envio de novas ordens."""
    if is_kill_switch_active(db):
        return

    event = AuditLog(
        event_type=AuditEventType.KILL_SWITCH_TRIGGERED,
        actor=actor,
        payload={"reason": reason},
    )
    db.add(event)
    db.commit()


def reset_kill_switch(db: Session, actor: str) -> None:
    """Desativa o kill switch (requer ação manual com identificação do ator)."""
    if not is_kill_switch_active(db):
        return

    event = AuditLog(
        event_type=AuditEventType.KILL_SWITCH_RESET,
        actor=actor,
        payload={"reason": "Manual reset"},
    )
    db.add(event)
    db.commit()
