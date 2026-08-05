"""Decisão produzida pelo signal fusion.

Cada linha registra o que o bot sabia, com que peso ponderou e o que decidiu.
É o lado "previsão" do par previsão/resultado que alimenta o aprendizado.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import CheckConstraint, Enum, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base, CreatedAtMixin, JSONType
from backend.models.enums import Direction


class Signal(Base, CreatedAtMixin):
    __tablename__ = "signals"
    __table_args__ = (
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_0_1"),
        CheckConstraint("fused_score >= -1 AND fused_score <= 1", name="fused_score_menos1_1"),
        CheckConstraint(
            "sentiment_score IS NULL OR (sentiment_score >= -1 AND sentiment_score <= 1)",
            name="sentiment_score_menos1_1",
        ),
        CheckConstraint(
            "technical_score IS NULL OR (technical_score >= -1 AND technical_score <= 1)",
            name="technical_score_menos1_1",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    direction: Mapped[Direction] = mapped_column(
        Enum(Direction, name="direction", native_enum=True), nullable=False
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    fused_score: Mapped[float] = mapped_column(Float, nullable=False)

    # Componentes, guardados separados para o weight optimizer poder reprocessar
    # decisões antigas com pesos novos sem recoletar nada.
    sentiment_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    sentiment_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    technical_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Qual versão de pesos produziu esta decisão. Sem isso o aprendizado não é
    # auditável: não dá para saber qual configuração gerou qual resultado.
    weight_version: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # Entradas cruas (indicadores, headlines consideradas, pesos aplicados).
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)

    def __repr__(self) -> str:
        return f"<Signal {self.id} {self.direction} conf={self.confidence:.2f}>"
