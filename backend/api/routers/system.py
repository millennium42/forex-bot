"""Endpoints de controle do sistema (ex: Kill Switch)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.api.dependencies import get_db
from backend.execution.kill_switch import (
    is_kill_switch_active,
    reset_kill_switch,
    trigger_kill_switch,
)

router = APIRouter(prefix="/system", tags=["System"])


class KillSwitchStatus(BaseModel):
    active: bool


class ActionPayload(BaseModel):
    actor: str
    reason: str = "Manual action"


@router.get("/kill-switch", response_model=KillSwitchStatus)
def get_kill_switch_status(db: Session = Depends(get_db)) -> KillSwitchStatus:  # noqa: B008
    """Retorna o estado atual do kill switch."""
    return KillSwitchStatus(active=is_kill_switch_active(db))


@router.post("/kill-switch/trigger", response_model=KillSwitchStatus)
def trigger_kill_switch_endpoint(
    payload: ActionPayload, db: Session = Depends(get_db)  # noqa: B008
) -> KillSwitchStatus:
    """Ativa o kill switch manualmente."""
    trigger_kill_switch(db, reason=payload.reason, actor=payload.actor)
    return KillSwitchStatus(active=True)


@router.post("/kill-switch/reset", response_model=KillSwitchStatus)
def reset_kill_switch_endpoint(
    payload: ActionPayload, db: Session = Depends(get_db)  # noqa: B008
) -> KillSwitchStatus:
    """Desativa o kill switch (exige um ator)."""
    reset_kill_switch(db, actor=payload.actor)
    return KillSwitchStatus(active=False)
