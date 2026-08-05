"""Endpoint para consulta de trades."""

from __future__ import annotations

import sys
from typing import Any

from fastapi import APIRouter

router = APIRouter(prefix="/trades", tags=["Trades"])


@router.get("/")
def list_trades() -> list[dict[str, Any]]:
    """Lista trades em andamento (posições) no MT5."""
    if sys.platform != "win32":
        return []

    try:
        import MetaTrader5 as mt5  # noqa: N813

        if not mt5.initialize():
            return []

        positions = mt5.positions_get()
        if not positions:
            return []

        result = []
        for p in positions:
            side = "COMPRA" if p.type == mt5.POSITION_TYPE_BUY else "VENDA"
            result.append(
                {
                    "id": f"T-{p.ticket}",
                    "pair": p.symbol,
                    "side": side,
                    "entry": p.price_open,
                    "current": p.price_current,
                    "pnl": p.profit,
                    "time": "Agora",
                }
            )
        return result
    except Exception:
        return []
