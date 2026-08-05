"""Instrumento negociável (par de moedas)."""

from __future__ import annotations

from sqlalchemy import Boolean, CheckConstraint, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base, CreatedAtMixin


class Instrument(Base, CreatedAtMixin):
    __tablename__ = "instruments"
    __table_args__ = (
        CheckConstraint("digits >= 0", name="digits_nao_negativo"),
        CheckConstraint("contract_size > 0", name="contract_size_positivo"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Símbolo como o MT5 o conhece. É a chave de junção com o broker.
    symbol: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)

    # Metadados do símbolo no broker, não valores monetários acumulados —
    # float64 basta e evita ter que lidar com Decimal em todo o pipeline.
    digits: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    point: Mapped[float] = mapped_column(Float, nullable=False, default=0.00001)
    contract_size: Mapped[float] = mapped_column(Float, nullable=False, default=100_000)

    # Desligar um instrumento não apaga histórico — só o tira da rotação.
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    def __repr__(self) -> str:
        return f"<Instrument {self.symbol}>"
