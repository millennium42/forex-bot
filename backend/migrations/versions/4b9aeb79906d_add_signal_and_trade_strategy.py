"""add_signal_and_trade_strategy

Revision ID: 4b9aeb79906d
Revises: 9b1c6e2a7f3d
Create Date: 2026-08-06 21:10:00.000000

Colunas `signals.strategy` e `trades.strategy` (história 39): o bot passa a
rodar um registro de estratégias independentes por ciclo em vez de uma única
leitura técnica, e cada decisão/ordem precisa registrar qual estratégia a
produziu — para o cooldown por (símbolo, direção, estratégia) e para medir
performance por estratégia a partir dos outcomes. `server_default='technical'`
cobre as linhas já existentes (a única leitura que existia até aqui) e é
removido depois — o default real mora no `mapped_column(default=...)` do lado
Python.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4b9aeb79906d"
down_revision: str | None = "9b1c6e2a7f3d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "signals",
        sa.Column("strategy", sa.String(length=64), nullable=False, server_default="technical"),
    )
    op.alter_column("signals", "strategy", server_default=None)
    op.create_index(op.f("ix_signals_strategy"), "signals", ["strategy"])

    op.add_column(
        "trades",
        sa.Column("strategy", sa.String(length=64), nullable=False, server_default="technical"),
    )
    op.alter_column("trades", "strategy", server_default=None)
    op.create_index(op.f("ix_trades_strategy"), "trades", ["strategy"])


def downgrade() -> None:
    op.drop_index(op.f("ix_trades_strategy"), table_name="trades")
    op.drop_column("trades", "strategy")

    op.drop_index(op.f("ix_signals_strategy"), table_name="signals")
    op.drop_column("signals", "strategy")
