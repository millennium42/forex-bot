"""add_instrument_volume_step_max

Revision ID: 0f81a5aba6e8
Revises: 7a2f9c1d4e6b
Create Date: 2026-08-05 15:20:43.166938

Passo de volume (`volume_step`) e volume máximo por ordem (`volume_max`) do
símbolo no broker, lidos de `symbol_info(symbol)` (história 30). O volume da
ordem deixa de ser sempre o lote mínimo — passa a ser derivado do risco
configurado, arredondado para baixo no passo e limitado ao teto do broker.
`server_default` garante que instrumentos já cadastrados recebam valores
conservadores em vez de quebrar o NOT NULL.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0f81a5aba6e8"
down_revision: str | None = "7a2f9c1d4e6b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "instruments",
        sa.Column("volume_step", sa.Float(), nullable=False, server_default="0.01"),
    )
    op.add_column(
        "instruments",
        sa.Column("volume_max", sa.Float(), nullable=False, server_default="100.0"),
    )
    op.alter_column("instruments", "volume_step", server_default=None)
    op.alter_column("instruments", "volume_max", server_default=None)
    op.create_check_constraint(
        op.f("ck_instruments_volume_step_positivo"), "instruments", "volume_step > 0"
    )
    op.create_check_constraint(
        op.f("ck_instruments_volume_max_positivo"), "instruments", "volume_max > 0"
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_instruments_volume_max_positivo"), "instruments", type_="check")
    op.drop_constraint(op.f("ck_instruments_volume_step_positivo"), "instruments", type_="check")
    op.drop_column("instruments", "volume_max")
    op.drop_column("instruments", "volume_step")
