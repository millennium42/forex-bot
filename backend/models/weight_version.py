"""Modelo para versionamento dos pesos do Signal Fusion."""

from __future__ import annotations

import datetime

from sqlalchemy import Boolean, DateTime, Float, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class WeightVersion(Base):
    """Versão dos pesos aplicados pelo signal fusion."""

    __tablename__ = "weight_versions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    version: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    technical: Mapped[float] = mapped_column(Float, nullable=False)
    sentiment: Mapped[float] = mapped_column(Float, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.datetime.now(datetime.UTC),
        nullable=False,
    )
