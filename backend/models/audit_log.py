"""Trilha de auditoria append-only.

A imutabilidade é garantida por trigger no banco (ver a migration), não por
convenção de código: um bug, um script ad-hoc ou um `psql` manual não conseguem
alterar linha existente. Insert é a única operação permitida.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import BigInteger, Enum, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base, CreatedAtMixin, JSONType
from backend.models.enums import AuditEventType


class AuditLog(Base, CreatedAtMixin):
    __tablename__ = "audit_log"

    # BigInteger porque a trilha nunca é podada. O variant existe porque o
    # SQLite só autoincrementa em INTEGER PRIMARY KEY.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"), primary_key=True, autoincrement=True
    )

    event_type: Mapped[AuditEventType] = mapped_column(
        Enum(AuditEventType, name="audit_event_type", native_enum=True),
        nullable=False,
        index=True,
    )

    # Correlaciona o evento com a ordem que o originou. Nullable porque eventos
    # como kill switch e promoção de modo não pertencem a nenhuma ordem.
    client_request_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    # Quem causou: "system" para o pipeline, ou identificação do operador humano.
    actor: Mapped[str] = mapped_column(String(64), nullable=False, default="system")

    # Corpo do evento. Contrato: sem credenciais e sem PII — o que entra aqui
    # nunca mais sai.
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)

    def __repr__(self) -> str:
        return f"<AuditLog {self.id} {self.event_type}>"
