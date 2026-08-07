"""Gerenciador de ordens.

Responsável por enviar ordens ao broker (MT5) garantindo idempotência e a
passagem obrigatória pelo risk manager.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.collection.mt5_client import MT5Client, MT5ConnectionError
from backend.execution.kill_switch import is_kill_switch_active, trigger_kill_switch
from backend.execution.risk_manager import (
    KillSwitchError,
    OrderRequest,
    RiskManager,
    RiskValidationError,
)
from backend.models.audit_log import AuditLog
from backend.models.enums import AuditEventType, Side, TradeStatus
from backend.models.instrument import Instrument
from backend.models.trade import Trade

logger = structlog.get_logger(__name__)

# Menor que meio ponto num par de 5 dígitos: abaixo disso o fill saiu no preço
# pedido e realinhar os stops seria uma ida ao broker para reescrever o mesmo
# número.
_TOLERANCIA_PRECO = 1e-6


class OrderManager:
    def __init__(
        self,
        mt5_client: MT5Client,
        db_session: Session,
        risk_manager: RiskManager,
    ) -> None:
        self.mt5_client = mt5_client
        self.db = db_session
        self.risk_manager = risk_manager

    def place_order(
        self,
        request: OrderRequest,
        client_request_id: str,
        instrument_id: int,
        equity: float,
        daily_loss: float,
        signal_id: int | None = None,
        account_drawdown: float = 0.0,
        strategy: str = "technical",
    ) -> Trade | None:
        """Gera ou recupera uma ordem de forma idempotente, executando no MT5.

        Se a ordem for rejeitada pelo risk_manager, retorna None e grava no audit_log.
        """
        # Idempotência
        existing = self.db.scalar(select(Trade).where(Trade.client_request_id == client_request_id))
        if existing:
            logger.info("order_manager.idempotent_hit", client_request_id=client_request_id)
            return existing

        # 1. Gate de risco (INVARIANTE)
        ks_active = is_kill_switch_active(self.db)
        try:
            self.risk_manager.validate_order(
                request=request,
                equity=equity,
                daily_loss=daily_loss,
                account_drawdown=account_drawdown,
                kill_switch_active=ks_active,
            )
        except KillSwitchError as exc:
            if not ks_active:
                logger.warning("order_manager.kill_switch_triggered", reason=str(exc))
                trigger_kill_switch(self.db, reason=str(exc), actor="system")

            logger.warning("order_manager.risk_rejected", reason=str(exc))
            mode = self.risk_manager.settings.effective_trading_mode.value
            trade = Trade(
                client_request_id=client_request_id,
                instrument_id=instrument_id,
                signal_id=signal_id,
                side=request.side,
                status=TradeStatus.REJECTED,
                volume=request.volume,
                stop_loss=request.stop_loss or 0.0,
                take_profit=request.take_profit,
                trading_mode=mode,
                strategy=strategy,
            )
            self.db.add(trade)
            event = AuditLog(
                event_type=AuditEventType.ORDER_REJECTED,
                client_request_id=client_request_id,
                payload={"reason": str(exc), "symbol": request.symbol, "volume": request.volume},
            )
            self.db.add(event)
            self.db.commit()
            return None
        except RiskValidationError as exc:
            logger.warning("order_manager.risk_rejected", reason=str(exc))
            mode = self.risk_manager.settings.effective_trading_mode.value
            trade = Trade(
                client_request_id=client_request_id,
                instrument_id=instrument_id,
                signal_id=signal_id,
                side=request.side,
                status=TradeStatus.REJECTED,
                volume=request.volume,
                stop_loss=request.stop_loss or 0.0,
                take_profit=request.take_profit,
                trading_mode=mode,
                strategy=strategy,
            )
            self.db.add(trade)
            event = AuditLog(
                event_type=AuditEventType.ORDER_REJECTED,
                client_request_id=client_request_id,
                payload={"reason": str(exc), "symbol": request.symbol, "volume": request.volume},
            )
            self.db.add(event)
            self.db.commit()
            return None

        # 2. Lock otimista no banco
        mode = self.risk_manager.settings.effective_trading_mode.value
        sl = request.stop_loss
        if sl is None:
            raise RuntimeError("Stop loss ausente após validação de risco")  # Invariante

        trade = Trade(
            client_request_id=client_request_id,
            instrument_id=instrument_id,
            signal_id=signal_id,
            side=request.side,
            status=TradeStatus.PENDING,
            volume=request.volume,
            stop_loss=sl,
            take_profit=request.take_profit,
            trading_mode=mode,
            strategy=strategy,
        )
        self.db.add(trade)

        try:
            self.db.flush()
        except IntegrityError:
            self.db.rollback()
            existing = self.db.scalar(
                select(Trade).where(Trade.client_request_id == client_request_id)
            )
            if existing:
                return existing
            raise

        req_event = AuditLog(
            event_type=AuditEventType.ORDER_REQUESTED,
            client_request_id=client_request_id,
            payload={
                "symbol": request.symbol,
                "side": request.side.value,
                "volume": request.volume,
            },
        )
        self.db.add(req_event)

        # 3. Envia ao MT5
        terminal = self.mt5_client.terminal
        action = getattr(terminal, "TRADE_ACTION_DEAL", 1)
        if request.side == Side.BUY:
            order_type = getattr(terminal, "ORDER_TYPE_BUY", 0)
        else:
            order_type = getattr(terminal, "ORDER_TYPE_SELL", 1)

        mt5_req: dict[str, Any] = {
            "action": action,
            "symbol": request.symbol,
            "volume": float(request.volume),
            "type": order_type,
            "price": float(request.price),
            "deviation": 20,
            "comment": client_request_id[:16],
            "sl": float(sl),
        }
        if request.take_profit is not None:
            mt5_req["tp"] = float(request.take_profit)

        try:
            result = terminal.order_send(mt5_req)
        except Exception as exc:
            logger.error("order_manager.mt5_exception", exc_info=True)
            trade.status = TradeStatus.REJECTED
            self.db.commit()
            raise MT5ConnectionError("Erro interno ao enviar ordem pro MT5") from exc

        if result is None:
            code, msg = terminal.last_error()
            trade.status = TradeStatus.REJECTED
            self.db.commit()
            logger.warning("order_manager.mt5_none_result", code=code, msg=msg)
            return trade

        retcode_done = getattr(terminal, "TRADE_RETCODE_DONE", 10009)
        if getattr(result, "retcode", None) != retcode_done:
            logger.warning(
                "order_manager.mt5_rejected",
                retcode=getattr(result, "retcode", -1),
                comment=getattr(result, "comment", ""),
            )
            trade.status = TradeStatus.REJECTED
            rej_event = AuditLog(
                event_type=AuditEventType.ORDER_REJECTED,
                client_request_id=client_request_id,
                payload={
                    "retcode": getattr(result, "retcode", -1),
                    "comment": getattr(result, "comment", ""),
                },
            )
            self.db.add(rej_event)
            self.db.commit()
            return trade

        # Sucesso
        trade.status = TradeStatus.OPEN
        trade.mt5_order_id = getattr(result, "order", None)
        # O ticket da POSIÇÃO, não o do deal. Numa ordem a mercado o MT5 usa o
        # ticket da ordem como ticket da posição resultante; `result.deal` é
        # outro identificador e não casa com nada em `positions_get()`.
        # Guardar o deal aqui fazia a reconciliação nunca encontrar a posição.
        trade.mt5_position_id = getattr(result, "order", None)
        trade.entry_price = getattr(result, "price", None)
        # Sem `opened_at` não há duração, e sem duração o outcome não é gravável.
        trade.opened_at = datetime.now(UTC)

        placed_event = AuditLog(
            event_type=AuditEventType.ORDER_PLACED,
            client_request_id=client_request_id,
            payload={
                "order": trade.mt5_order_id,
                "price": trade.entry_price,
                "volume": getattr(result, "volume", None),
            },
        )
        self.db.add(placed_event)
        self.db.commit()

        self._realinhar_stops_ao_fill(trade, request)
        return trade

    def _realinhar_stops_ao_fill(self, trade: Trade, request: OrderRequest) -> None:
        """Reposiciona SL/TP a partir do preço de preenchimento real.

        SL e TP são calculados a partir do preço SOLICITADO, mas o broker
        preenche a mercado e o preço efetivo difere. Como os stops são níveis
        ABSOLUTOS, qualquer diferença desloca as distâncias reais e, com elas,
        o risco monetário e o RR.

        Observado em produção: uma venda de 2MACDSTO com RR pretendido 1.0 saiu
        com 1.27 porque o fill veio 0,2 pip melhor — `d_sl` encolheu e `d_tp`
        cresceu na mesma medida. Naquele caso foi a favor; um fill pior desloca
        contra, e aí o risco real fica ACIMA do pretendido, que é o que não pode
        acontecer.

        Preserva as DISTÂNCIAS pretendidas, recolocando-as em volta do fill.
        """
        fill = trade.entry_price
        if fill is None or request.stop_loss is None:
            return

        distancia_sl = abs(request.price - request.stop_loss)
        if distancia_sl <= 0:
            return

        novo_sl = fill - distancia_sl if trade.side == Side.BUY else fill + distancia_sl

        novo_tp: float | None = None
        if request.take_profit is not None:
            distancia_tp = abs(request.take_profit - request.price)
            novo_tp = fill + distancia_tp if trade.side == Side.BUY else fill - distancia_tp

        # Nada a fazer quando o fill saiu exatamente no preço pedido: evita uma
        # ida ao broker por ordem só para reescrever os mesmos números.
        if abs(novo_sl - request.stop_loss) < _TOLERANCIA_PRECO and (
            novo_tp is None
            or request.take_profit is None
            or abs(novo_tp - request.take_profit) < _TOLERANCIA_PRECO
        ):
            return

        if novo_sl <= 0:
            logger.warning("order_manager.realinhamento_stop_invalido", trade_id=trade.id)
            return

        if self.modify_trade(trade, novo_sl, novo_tp):
            logger.info(
                "order_manager.stops_realinhados_ao_fill",
                trade_id=trade.id,
                preco_pedido=request.price,
                preco_fill=fill,
            )
        else:
            # Falhar aqui não invalida a posição: ela existe com os stops
            # originais, que continuam protegendo — só com distância diferente
            # da pretendida. Registrar é o suficiente.
            logger.warning("order_manager.realinhamento_falhou", trade_id=trade.id)

    def _get_symbol(self, instrument_id: int) -> str:
        instrument = self.db.get(Instrument, instrument_id)
        if not instrument:
            raise RuntimeError(f"Instrumento {instrument_id} inexistente")
        return instrument.symbol

    def modify_trade(self, trade: Trade, sl: float, tp: float | None = None) -> bool:
        """Modifica SL/TP de um trade existente."""
        if trade.status != TradeStatus.OPEN:
            return False

        terminal = self.mt5_client.terminal
        action = getattr(terminal, "TRADE_ACTION_SLTP", 6)

        pos_id = trade.mt5_position_id or trade.mt5_order_id
        if not pos_id:
            logger.error("order_manager.modify_missing_id", trade_id=trade.id)
            return False

        symbol = self._get_symbol(trade.instrument_id)

        mt5_req: dict[str, Any] = {
            "action": action,
            "position": pos_id,
            "symbol": symbol,
            "sl": float(sl),
        }
        if tp is not None:
            mt5_req["tp"] = float(tp)

        try:
            result = terminal.order_send(mt5_req)
        except Exception:
            logger.error("order_manager.mt5_modify_exception", exc_info=True)
            return False

        if result is None:
            return False

        retcode_done = getattr(terminal, "TRADE_RETCODE_DONE", 10009)
        retcode_no_changes = getattr(terminal, "TRADE_RETCODE_NO_CHANGES", 10025)

        rc = getattr(result, "retcode", -1)
        if rc in (retcode_done, retcode_no_changes):
            trade.stop_loss = sl
            trade.take_profit = tp

            event = AuditLog(
                event_type=AuditEventType.ORDER_MODIFIED,
                client_request_id=trade.client_request_id,
                payload={"new_sl": sl, "new_tp": tp, "retcode": rc},
            )
            self.db.add(event)
            self.db.commit()
            return True

        return False

    def close_trade(self, trade: Trade, price: float) -> bool:
        """Encerra uma ordem aberta enviando ordem oposta."""
        if trade.status != TradeStatus.OPEN:
            return False

        terminal = self.mt5_client.terminal
        action = getattr(terminal, "TRADE_ACTION_DEAL", 1)

        if trade.side == Side.BUY:
            order_type = getattr(terminal, "ORDER_TYPE_SELL", 1)
        else:
            order_type = getattr(terminal, "ORDER_TYPE_BUY", 0)

        pos_id = trade.mt5_position_id or trade.mt5_order_id
        if not pos_id:
            logger.error("order_manager.close_missing_id", trade_id=trade.id)
            return False

        symbol = self._get_symbol(trade.instrument_id)

        mt5_req: dict[str, Any] = {
            "action": action,
            "symbol": symbol,
            "volume": float(trade.volume),
            "type": order_type,
            "position": pos_id,
            "price": float(price),
            "deviation": 20,
            "comment": "close",
        }

        try:
            result = terminal.order_send(mt5_req)
        except Exception:
            logger.error("order_manager.mt5_close_exception", exc_info=True)
            return False

        if result is None:
            return False

        retcode_done = getattr(terminal, "TRADE_RETCODE_DONE", 10009)
        rc = getattr(result, "retcode", -1)
        if rc == retcode_done:
            trade.status = TradeStatus.CLOSED
            trade.closed_at = datetime.now(UTC)

            event = AuditLog(
                event_type=AuditEventType.ORDER_CLOSED,
                client_request_id=trade.client_request_id,
                payload={"close_price": price, "retcode": rc},
            )
            self.db.add(event)
            self.db.commit()
            return True

        return False
