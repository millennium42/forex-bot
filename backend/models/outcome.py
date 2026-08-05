"""Resultado real de um trade encerrado — o lado "verdade" do par previsão/resultado.

É a partir daqui que o weight optimizer (história 13) e o promotion gate
(história 15) trabalham. Um trade encerrado tem exatamente um outcome.
"""

from __future__ import annotations

from sqlalchemy import Boolean, CheckConstraint, Enum, Float, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base, CreatedAtMixin
from backend.models.enums import Direction


class Outcome(Base, CreatedAtMixin):
    __tablename__ = "outcomes"
    __table_args__ = (CheckConstraint("duration_seconds >= 0", name="duration_nao_negativa"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # UNIQUE: um trade encerrado gera um único outcome. Reprocessar não duplica.
    trade_id: Mapped[int] = mapped_column(
        ForeignKey("trades.id", ondelete="RESTRICT"), unique=True, nullable=False
    )
    signal_id: Mapped[int | None] = mapped_column(
        ForeignKey("signals.id", ondelete="RESTRICT"), nullable=True, index=True
    )

    exit_price: Mapped[float] = mapped_column(Float, nullable=False)
    pnl: Mapped[float] = mapped_column(Float, nullable=False)
    pnl_pct: Mapped[float] = mapped_column(Float, nullable=False)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)

    # Previsto vs. realizado. `was_correct` é derivado na escrita e persistido
    # para que as consultas de performance não precisem recalcular a cada leitura.
    predicted_direction: Mapped[Direction | None] = mapped_column(
        Enum(Direction, name="direction", native_enum=True), nullable=True
    )
    actual_direction: Mapped[Direction] = mapped_column(
        Enum(Direction, name="direction", native_enum=True), nullable=False
    )
    was_correct: Mapped[bool] = mapped_column(Boolean, nullable=False, index=True)

    def __repr__(self) -> str:
        return f"<Outcome trade={self.trade_id} pnl={self.pnl:.2f} ok={self.was_correct}>"
