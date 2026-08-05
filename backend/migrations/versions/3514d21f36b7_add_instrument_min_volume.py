"""add_instrument_min_volume

Revision ID: 3514d21f36b7
Revises: 25b1dfc0e341
Create Date: 2026-08-05 09:17:01.304763

Lote mínimo negociável por símbolo, lido de `symbol_info(symbol).volume_min`
no MT5 (história 23). `server_default` garante que instrumentos já
cadastrados recebam o mínimo padrão de 0.01 em vez de quebrar o NOT NULL.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3514d21f36b7"
down_revision: str | None = "25b1dfc0e341"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "instruments",
        sa.Column("min_volume", sa.Float(), nullable=False, server_default="0.01"),
    )
    op.alter_column("instruments", "min_volume", server_default=None)
    op.create_check_constraint(
        op.f("ck_instruments_min_volume_positivo"), "instruments", "min_volume > 0"
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_instruments_min_volume_positivo"), "instruments", type_="check")
    op.drop_column("instruments", "min_volume")
