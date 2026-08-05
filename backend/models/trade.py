"""Ordem enviada ao broker.

Uma linha aqui só existe se o risk manager aprovou. Ordem barrada não vira trade:
vira registro em `audit_log` com `order_rejected`.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Float,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base, CreatedAtMixin, TimestampTZ, pg_enum
from backend.models.enums import Side, TradeStatus


class Trade(Base, CreatedAtMixin):
    __tablename__ = "trades"
    __table_args__ = (
        CheckConstraint("volume > 0", name="volume_positivo"),
        CheckConstraint("stop_loss > 0", name="stop_loss_positivo"),
        CheckConstraint("take_profit IS NULL OR take_profit > 0", name="take_profit_positivo"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Chave de idempotência. UNIQUE é o que garante que um retry de rede não
    # abre duas posições: a segunda tentativa colide aqui, não no broker.
    client_request_id: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )

    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    # Nullable: um trade pode nascer de intervenção manual (fechamento, hedge),
    # sem sinal originador. Quando existe, é o elo previsão -> resultado.
    signal_id: Mapped[int | None] = mapped_column(
        ForeignKey("signals.id", ondelete="RESTRICT"), nullable=True, index=True
    )

    side: Mapped[Side] = mapped_column(pg_enum(Side, "side"), nullable=False)
    status: Mapped[TradeStatus] = mapped_column(
        pg_enum(TradeStatus, "trade_status"),
        nullable=False,
        default=TradeStatus.PENDING,
        index=True,
    )

    volume: Mapped[float] = mapped_column(Float, nullable=False)
    entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)

    # NOT NULL é a expressão em banco da regra "stop loss obrigatório" (§4).
    # Mesmo que alguém contorne o risk manager, o banco recusa a linha.
    stop_loss: Mapped[float] = mapped_column(Float, nullable=False)
    take_profit: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Em que modo a ordem foi enviada. Registrado por linha, nunca inferido da
    # config atual: a config muda, o histórico não pode mudar junto.
    trading_mode: Mapped[str] = mapped_column(String(8), nullable=False)

    # Identificadores do lado do MT5, preenchidos quando o broker confirma.
    mt5_order_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    mt5_position_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)

    opened_at: Mapped[datetime | None] = mapped_column(TimestampTZ, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(TimestampTZ, nullable=True)

    def __repr__(self) -> str:
        return f"<Trade {self.id} {self.side} vol={self.volume} {self.status}>"
