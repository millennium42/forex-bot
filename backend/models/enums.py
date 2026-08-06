"""Enums persistidos. Os valores viram tipos nomeados no Postgres."""

from __future__ import annotations

from enum import StrEnum


class DocumentSource(StrEnum):
    """Origem de um documento textual coletado.

    A tabela é compartilhada entre os coletores: o que muda entre eles é a
    origem e como o `dedupe_hash` é derivado, não o schema.
    """

    NEWS = "news"
    TWITTER = "twitter"


class Direction(StrEnum):
    """Direção de uma decisão de sinal."""

    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


class Side(StrEnum):
    """Lado de uma ordem de fato enviada. `hold` não vira ordem."""

    BUY = "buy"
    SELL = "sell"


class TradeStatus(StrEnum):
    PENDING = "pending"  # aceita pelo risk manager, ainda não confirmada pelo MT5
    OPEN = "open"
    CLOSED = "closed"
    REJECTED = "rejected"  # barrada pelo risk manager ou recusada pelo broker


class AuditEventType(StrEnum):
    ORDER_REQUESTED = "order_requested"
    ORDER_REJECTED = "order_rejected"
    ORDER_PLACED = "order_placed"
    ORDER_MODIFIED = "order_modified"
    ORDER_CLOSED = "order_closed"
    KILL_SWITCH_TRIGGERED = "kill_switch_triggered"
    KILL_SWITCH_RESET = "kill_switch_reset"
    MODE_PROMOTED = "mode_promoted"
    RECONCILIATION_MISMATCH = "reconciliation_mismatch"
    WEIGHTS_UPDATED = "weights_updated"
    WEIGHTS_ROLLED_BACK = "weights_rolled_back"
    EQUITY_PEAK_UPDATED = "equity_peak_updated"
    DRAWDOWN_LIMIT_TRIGGERED = "drawdown_limit_triggered"
    DRAWDOWN_LIMIT_RESET = "drawdown_limit_reset"
    ORDER_BLOCKED = "order_blocked"
