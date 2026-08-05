"""Persistência compartilhada da camada de coleta.

News e Twitter gravam na mesma tabela `documents` e diferem apenas em como
derivam o `dedupe_hash`. O insert com dedupe vive aqui para que os dois
coletores tenham exatamente o mesmo comportamento de idempotência.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Protocol

import structlog
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.models import Document

logger = structlog.get_logger(__name__)


class CollectedItem(Protocol):
    """Item já normalizado por um coletor, ainda não persistido."""

    @property
    def dedupe_hash(self) -> str: ...

    def to_document(self) -> Document: ...


def store_items(session: Session, items: Iterable[CollectedItem]) -> int:
    """Persiste os itens ainda desconhecidos e devolve quantos entraram.

    Não faz commit: quem chama controla a transação (`session_scope` na task).
    Cada insert vai em savepoint para que uma colisão — outro worker gravou o
    mesmo hash entre o SELECT e o INSERT — descarte só aquele item, não o lote.
    """
    pendentes: dict[str, CollectedItem] = {}
    for item in items:
        pendentes.setdefault(item.dedupe_hash, item)  # duplicata dentro do próprio lote

    if not pendentes:
        return 0

    ja_existentes = set(
        session.scalars(
            select(Document.dedupe_hash).where(Document.dedupe_hash.in_(pendentes))
        ).all()
    )

    inseridos = 0
    for hash_, item in pendentes.items():
        if hash_ in ja_existentes:
            continue
        try:
            with session.begin_nested():
                session.add(item.to_document())
        except IntegrityError:
            logger.debug("collection.duplicata_concorrente", dedupe_hash=hash_)
            continue
        inseridos += 1

    return inseridos


def recent_documents(session: Session, symbol: str, since: datetime) -> list[Document]:
    """Documentos publicados a partir de `since` que mencionam o símbolo.

    `documents` não tem coluna de símbolo — nem toda fonte declara o par
    explicitamente — então o match é por substring em título ou conteúdo. Cobre
    a cashtag do coletor de Twitter (`$EURUSD` contém `EURUSD`) e a menção
    solta numa notícia.
    """
    padrao = f"%{symbol}%"
    return list(
        session.execute(
            select(Document)
            .where(
                Document.created_at >= since,
                or_(Document.content.ilike(padrao), Document.title.ilike(padrao)),
            )
            .order_by(Document.created_at.desc())
        ).scalars()
    )
