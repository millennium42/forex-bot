"""Endpoints de controle do sistema (ex: Kill Switch)."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.api.dependencies import get_db
from backend.collection.mt5_client import (
    MT5Client,
    MT5ConnectionError,
    MT5ModeMismatchError,
)
from backend.execution.kill_switch import (
    is_kill_switch_active,
    reset_kill_switch,
    trigger_kill_switch,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/system", tags=["System"])


class KillSwitchStatus(BaseModel):
    active: bool


class ActionPayload(BaseModel):
    actor: str
    reason: str = "Manual action"


class AccountStatus(BaseModel):
    """Estado da conta no broker.

    `balance` e `equity` são `None` quando o terminal não responde — nunca 0.0.
    Zero é um valor legítimo de conta e o dashboard não teria como distinguir
    "sem dinheiro" de "sem conexão".
    """

    balance: float | None
    equity: float | None
    currency: str | None
    connected: bool
    demo: bool | None = None
    detail: str | None = None


@router.get("/account", response_model=AccountStatus)
def get_account_status() -> AccountStatus:
    """Saldo e equity reais do MT5. Falha fechado: sem terminal, sem número."""
    try:
        # Via MT5Client, não `import MetaTrader5` direto: só ele aplica a
        # checagem de coerência entre o modo configurado e o tipo da conta.
        with MT5Client() as client:
            info = client.get_account_info()
            return AccountStatus(
                balance=info.balance,
                equity=info.equity,
                currency=info.currency,
                connected=True,
                demo=info.is_demo,
            )
    except (MT5ConnectionError, MT5ModeMismatchError) as exc:
        logger.warning("api.account.indisponivel", motivo=str(exc))
        return AccountStatus(
            balance=None, equity=None, currency=None, connected=False, detail=str(exc)
        )


@router.get("/kill-switch", response_model=KillSwitchStatus)
def get_kill_switch_status(db: Session = Depends(get_db)) -> KillSwitchStatus:  # noqa: B008
    """Retorna o estado atual do kill switch."""
    return KillSwitchStatus(active=is_kill_switch_active(db))


@router.post("/kill-switch/trigger", response_model=KillSwitchStatus)
def trigger_kill_switch_endpoint(
    payload: ActionPayload,
    db: Session = Depends(get_db),  # noqa: B008
) -> KillSwitchStatus:
    """Ativa o kill switch manualmente."""
    trigger_kill_switch(db, reason=payload.reason, actor=payload.actor)
    return KillSwitchStatus(active=True)


@router.post("/kill-switch/reset", response_model=KillSwitchStatus)
def reset_kill_switch_endpoint(
    payload: ActionPayload,
    db: Session = Depends(get_db),  # noqa: B008
) -> KillSwitchStatus:
    """Desativa o kill switch (exige um ator)."""
    reset_kill_switch(db, actor=payload.actor)
    return KillSwitchStatus(active=False)
