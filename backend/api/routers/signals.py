"""Endpoint para consulta de sinais."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

router = APIRouter(prefix="/signals", tags=["Signals"])


@router.get("/")
def list_signals() -> list[dict[str, Any]]:
    """Lista sinais recentes. Endpoint stub para schema."""
    return []
