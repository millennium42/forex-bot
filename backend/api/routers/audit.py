"""Endpoint de consulta da trilha de auditoria (append-only)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.api.dependencies import get_db
from backend.models.audit_log import AuditLog
from backend.models.enums import AuditEventType

router = APIRouter(prefix="/audit", tags=["Audit"])

MAX_AUDIT_LIMIT = 500


class AuditEntry(BaseModel):
    """Uma linha do `audit_log`. Somente leitura — a tabela é append-only no banco."""

    id: int
    event_type: str
    client_request_id: str | None
    actor: str
    payload: dict[str, Any]
    created_at: datetime


@router.get("/", response_model=list[AuditEntry])
def list_audit_log(
    event_type: AuditEventType | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),  # noqa: B008
) -> list[AuditEntry]:
    """Stream (mais recente primeiro) do audit log, filtrável por tipo de evento."""
    query = db.query(AuditLog).order_by(AuditLog.created_at.desc())
    if event_type is not None:
        query = query.filter(AuditLog.event_type == event_type)

    rows = query.limit(min(limit, MAX_AUDIT_LIMIT)).all()
    return [
        AuditEntry(
            id=row.id,
            event_type=row.event_type.value,
            client_request_id=row.client_request_id,
            actor=row.actor,
            payload=row.payload,
            created_at=row.created_at,
        )
        for row in rows
    ]
