"""Position tracker.

Reconcilia posições MT5 versus banco periodicamente.
Qualquer divergência (posição aberta no MT5 mas não no banco, ou vice-versa)
gera um evento no audit_log.
"""

from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.collection.mt5_client import MT5Client, MT5ConnectionError
from backend.models.audit_log import AuditLog
from backend.models.enums import AuditEventType, TradeStatus
from backend.models.trade import Trade

logger = structlog.get_logger(__name__)


class PositionTracker:
    def __init__(self, mt5_client: MT5Client, db_session: Session) -> None:
        self.mt5_client = mt5_client
        self.db = db_session

    def reconcile(self) -> None:
        """Compara trades abertos no banco com posições ativas no MT5.

        Registra no audit_log sempre que houver divergência.
        Divergência ocorre quando:
        - DB considera um Trade como OPEN, mas o MT5 não tem a posição correspondente.
        - MT5 possui uma posição que o DB não mapeia para nenhum Trade OPEN.
        """
        # Obter posições do MT5 (falha inteira se offline)
        try:
            mt5_positions = self.mt5_client.get_positions()
        except MT5ConnectionError as exc:
            logger.warning("position_tracker.mt5_offline", reason=str(exc))
            return  # Não faz reconciliação se não consegue ver o broker

        # Obter trades abertos no banco (faz join no Instrument para ter o símbolo)
        db_trades = self.db.scalars(select(Trade).where(Trade.status == TradeStatus.OPEN)).all()

        mt5_matched_tickets = set()

        # Mapeamento do MT5
        mt5_tickets_map = {pos.ticket: pos for pos in mt5_positions}
        mt5_identifiers_map = {pos.identifier: pos for pos in mt5_positions}

        for trade in db_trades:
            # Verifica se mt5_order_id ou mt5_position_id está no MT5
            pos_from_order = mt5_tickets_map.get(trade.mt5_order_id) if trade.mt5_order_id else None
            pos_from_deal = (
                mt5_tickets_map.get(trade.mt5_position_id) if trade.mt5_position_id else None
            )
            pos_from_ident = (
                mt5_identifiers_map.get(trade.mt5_order_id) if trade.mt5_order_id else None
            )

            matched_pos = pos_from_order or pos_from_deal or pos_from_ident
            if matched_pos:
                mt5_matched_tickets.add(matched_pos.ticket)
            else:
                logger.warning("position_tracker.missing_in_mt5", trade_id=trade.id)
                event = AuditLog(
                    event_type=AuditEventType.RECONCILIATION_MISMATCH,
                    client_request_id=trade.client_request_id,
                    payload={
                        "reason": "missing_in_mt5",
                        "trade_id": trade.id,
                        "mt5_order_id": trade.mt5_order_id,
                        "mt5_position_id": trade.mt5_position_id,
                    },
                )
                self.db.add(event)

        for pos in mt5_positions:
            if pos.ticket not in mt5_matched_tickets:
                logger.warning(
                    "position_tracker.missing_in_db",
                    ticket=pos.ticket,
                    symbol=pos.symbol,
                    volume=pos.volume,
                )
                event = AuditLog(
                    event_type=AuditEventType.RECONCILIATION_MISMATCH,
                    payload={
                        "reason": "missing_in_db",
                        "mt5_ticket": pos.ticket,
                        "mt5_identifier": pos.identifier,
                        "symbol": pos.symbol,
                        "volume": pos.volume,
                        "type": pos.type,
                    },
                )
                self.db.add(event)

        self.db.commit()
