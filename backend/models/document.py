"""Documento textual coletado (notícia, tweet) que alimenta o sentiment analyzer.

A não-duplicação é garantida pelo índice único em `dedupe_hash`, não por
checagem prévia na aplicação: dois workers Celery processando o mesmo feed em
paralelo passariam por qualquer `SELECT ... IF NOT EXISTS` ao mesmo tempo.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base, CreatedAtMixin, TimestampTZ, pg_enum
from backend.models.enums import DocumentSource


class Document(Base, CreatedAtMixin):
    __tablename__ = "documents"

    # BigInteger: o volume de notícias/tweets cresce muito mais rápido que o de
    # trades. O variant existe porque o SQLite só autoincrementa INTEGER PK.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"), primary_key=True, autoincrement=True
    )

    source: Mapped[DocumentSource] = mapped_column(
        pg_enum(DocumentSource, "document_source"),
        nullable=False,
        index=True,
    )

    # sha256 hex (64 chars) do identificador canônico do documento: para notícia,
    # da URL normalizada; para tweet, do id. É a chave de idempotência do coletor.
    dedupe_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # De onde veio: URL do feed RSS ou nome do stream. Permite desligar uma fonte
    # ruidosa sem apagar o que ela já produziu.
    origin: Mapped[str] = mapped_column(String(255), nullable=False)

    # Data declarada pela fonte. Nullable porque nem todo feed publica uma, e
    # inventar `created_at` no lugar falsearia a janela temporal do sentimento.
    published_at: Mapped[datetime | None] = mapped_column(TimestampTZ, nullable=True, index=True)

    def __repr__(self) -> str:
        return f"<Document {self.id} {self.source} {self.dedupe_hash[:8]}>"
