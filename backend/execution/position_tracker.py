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
from backend.learning.outcome_recorder import record_outcome
from backend.models.audit_log import AuditLog
from backend.models.enums import AuditEventType, TradeStatus
from backend.models.outcome import Outcome
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
                continue

            # A posição sumiu do broker. Antes de chamar isso de divergência,
            # a explicação mais provável é a mais banal: ela fechou — no stop,
            # no alvo, ou por intervenção manual no terminal.
            if self._encerrar_pelo_historico(trade):
                continue

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

        self._gravar_outcomes_pendentes()
        self.db.commit()

    # ------------------------------------------------------------------ #
    def _gravar_outcomes_pendentes(self) -> None:
        """Gera outcome para todo trade encerrado que ainda não tem um.

        Um trade pode ser encerrado por dois caminhos: a reconciliação (stop ou
        alvo dispararam) e o `close_trade` explícito (bot ou operador). Só o
        primeiro gravava outcome, então fechamento manual sumia do aprendizado.

        Varrer por ausência de outcome cobre os dois caminhos e é idempotente:
        rodar de novo não duplica, porque `outcomes.trade_id` é UNIQUE.
        """
        pendentes = self.db.scalars(
            select(Trade)
            .outerjoin(Outcome, Outcome.trade_id == Trade.id)
            .where(Trade.status == TradeStatus.CLOSED, Outcome.id.is_(None))
        ).all()

        for trade in pendentes:
            position_id = trade.mt5_position_id or trade.mt5_order_id
            if position_id is None:
                continue

            try:
                deal = self.mt5_client.get_exit_deal(position_id)
            except MT5ConnectionError:
                return  # broker indisponível: tenta de novo no próximo ciclo

            if deal is None:
                continue

            if trade.opened_at is None:
                trade.opened_at = trade.created_at
            if trade.closed_at is None:
                trade.closed_at = deal.time

            outcome = record_outcome(self.db, trade, exit_price=deal.price, pnl=deal.profit)
            logger.info(
                "position_tracker.outcome_pendente_gravado",
                trade_id=trade.id,
                pnl=deal.profit,
                acertou=outcome.was_correct,
            )

    def _encerrar_pelo_historico(self, trade: Trade) -> bool:
        """Fecha o trade a partir do deal de saída e grava o outcome.

        Este é o elo que fecha o ciclo previsão → resultado. Sem ele o trade
        ficaria `open` para sempre no banco, nenhum `Outcome` seria criado, e
        tanto o weight optimizer quanto o promotion gate — que contam outcomes —
        nunca teriam do que se alimentar.

        Devolve `False` quando não foi possível encerrar; aí a divergência é
        registrada normalmente e a tentativa se repete no próximo ciclo.
        """
        position_id = trade.mt5_position_id or trade.mt5_order_id
        if position_id is None:
            return False

        try:
            deal = self.mt5_client.get_exit_deal(position_id)
        except MT5ConnectionError as exc:
            logger.warning("position_tracker.historico_indisponivel", motivo=str(exc))
            return False

        if deal is None:
            # Sem deal de saída a posição não fechou de fato — pode ser atraso de
            # propagação do broker. Inventar um preço aqui envenenaria o
            # aprendizado com um P&L que nunca existiu.
            return False

        trade.status = TradeStatus.CLOSED
        trade.closed_at = deal.time
        if trade.opened_at is None:
            # Trades gravados antes de `opened_at` passar a ser preenchido. O
            # `created_at` é o instante em que a ordem foi aceita — aproximação
            # boa o suficiente para a duração, e melhor que descartar o outcome.
            trade.opened_at = trade.created_at

        outcome = record_outcome(self.db, trade, exit_price=deal.price, pnl=deal.profit)

        self.db.add(
            AuditLog(
                event_type=AuditEventType.ORDER_CLOSED,
                client_request_id=trade.client_request_id,
                payload={
                    "trade_id": trade.id,
                    "exit_price": deal.price,
                    "pnl": deal.profit,
                    "origem": "reconciliacao",
                },
            )
        )
        logger.info(
            "position_tracker.trade_encerrado",
            trade_id=trade.id,
            exit_price=deal.price,
            pnl=deal.profit,
            acertou=outcome.was_correct,
        )
        return True
