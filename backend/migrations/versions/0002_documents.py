"""Tabela documents: notícias e tweets coletados

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-05

O índice único em `dedupe_hash` é o que impede duplicata em reprocesso de feed.
Fica no banco, não na aplicação: dois workers Celery lendo o mesmo feed ao mesmo
tempo passariam juntos por qualquer checagem prévia em Python.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

document_source = postgresql.ENUM("news", "twitter", name="document_source", create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    document_source.create(bind, checkfirst=True)

    op.create_table(
        "documents",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source", document_source, nullable=False),
        sa.Column("dedupe_hash", sa.String(length=64), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("origin", sa.String(length=255), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_documents"),
        sa.UniqueConstraint("dedupe_hash", name="uq_documents_dedupe_hash"),
    )
    op.create_index("ix_documents_created_at", "documents", ["created_at"])
    op.create_index("ix_documents_published_at", "documents", ["published_at"])
    op.create_index("ix_documents_source", "documents", ["source"])


def downgrade() -> None:
    op.drop_table("documents")
    document_source.drop(op.get_bind(), checkfirst=True)
