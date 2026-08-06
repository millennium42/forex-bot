"""add_order_blocked_audit_event

Revision ID: 9b1c6e2a7f3d
Revises: 0f81a5aba6e8
Create Date: 2026-08-06 10:00:00.000000

Novo valor `order_blocked` em `audit_event_type` (história 36): o runner
rejeitava confiança insuficiente, cooldown de leitura repetida, margem livre
insuficiente, ATR/stop inválidos e risco que não cobre o lote mínimo só via
log estruturado (`structlog`), nunca persistido — o dashboard não tinha como
mostrar por que a última ordem não saiu. `ALTER TYPE ... ADD VALUE` é
aditivo, mesmo padrão de `7a2f9c1d4e6b_add_drawdown_audit_events.py`.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "9b1c6e2a7f3d"
down_revision: str | None = "0f81a5aba6e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE audit_event_type ADD VALUE IF NOT EXISTS 'order_blocked'")


def downgrade() -> None:
    # Postgres não suporta remover valor de enum nativo. Nenhum valor antigo é
    # alterado ou removido, então não há dado a perder deixando isto no-op —
    # mesma justificativa de 7a2f9c1d4e6b_add_drawdown_audit_events.py.
    pass
