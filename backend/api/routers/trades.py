"""Endpoint para consulta de trades."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

router = APIRouter(prefix="/trades", tags=["Trades"])


@router.get("/")
def list_trades() -> list[dict[str, Any]]:
    """Lista trades recentes. Endpoint stub para schema."""
    return []
