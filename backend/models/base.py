"""Base declarativa e tipos compartilhados dos modelos."""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import JSON, DateTime, MetaData, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Nomes determinísticos de constraint. Sem isso, o downgrade de uma migration
# não consegue referenciar por nome o que o upgrade criou.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# JSONB no Postgres (indexável), JSON genérico no SQLite dos testes de unidade.
JSONType = JSONB().with_variant(JSON(), "sqlite")

# Sempre timezone-aware. Horário de trade sem timezone é bug esperando acontecer.
TimestampTZ = DateTime(timezone=True)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    type_annotation_map: ClassVar[dict[Any, Any]] = {
        dict[str, Any]: JSONType,
        datetime: TimestampTZ,
    }


class CreatedAtMixin:
    """`created_at` preenchido pelo banco, não pelo processo Python.

    O relógio do banco é a única referência temporal consistente entre a API,
    os workers Celery e o reconciliador de posições.
    """

    created_at: Mapped[datetime] = mapped_column(
        TimestampTZ,
        server_default=func.now(),
        nullable=False,
        index=True,
    )
