"""add_drawdown_audit_events

Revision ID: 7a2f9c1d4e6b
Revises: 3514d21f36b7
Create Date: 2026-08-05 10:00:00.000000

Três valores novos em `audit_event_type` para a trava de drawdown acumulado
(história 29): `equity_peak_updated` registra cada novo pico de equity
observado (o pico persiste no banco, não em memória do processo);
`drawdown_limit_triggered`/`drawdown_limit_reset` seguem o mesmo par
trigger/reset já usado pelo kill switch. `ALTER TYPE ... ADD VALUE` é aditivo
por natureza — não remove nem reordena os valores existentes.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "7a2f9c1d4e6b"
down_revision: str | None = "3514d21f36b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_VALUES = ("equity_peak_updated", "drawdown_limit_triggered", "drawdown_limit_reset")


def upgrade() -> None:
    for value in NEW_VALUES:
        op.execute(f"ALTER TYPE audit_event_type ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    # Postgres não suporta remover valor de enum nativo (ALTER TYPE ... DROP
    # VALUE não existe). Reverter exigiria recriar o tipo inteiro e todas as
    # colunas que o usam; como nenhum valor antigo é removido ou alterado,
    # não há dado a perder ao deixar o downgrade como no-op.
    pass
