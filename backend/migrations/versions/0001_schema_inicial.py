"""Schema inicial: instruments, signals, trades, outcomes, audit_log

Revision ID: 0001
Revises:
Create Date: 2026-08-04

`audit_log` recebe triggers que bloqueiam UPDATE, DELETE e TRUNCATE. A garantia
de append-only fica no banco de propósito: código pode ter bug, script ad-hoc
pode existir, `psql` manual acontece — o trigger vale para todos eles.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Tipos enum criados uma única vez e reusados: `direction` aparece em duas
# tabelas, e deixar o SQLAlchemy criá-lo implicitamente falharia na segunda.
direction = postgresql.ENUM("buy", "sell", "hold", name="direction", create_type=False)
side = postgresql.ENUM("buy", "sell", name="side", create_type=False)
trade_status = postgresql.ENUM(
    "pending", "open", "closed", "rejected", name="trade_status", create_type=False
)
audit_event_type = postgresql.ENUM(
    "order_requested",
    "order_rejected",
    "order_placed",
    "order_modified",
    "order_closed",
    "kill_switch_triggered",
    "kill_switch_reset",
    "mode_promoted",
    "reconciliation_mismatch",
    "weights_updated",
    "weights_rolled_back",
    name="audit_event_type",
    create_type=False,
)

APPEND_ONLY_FUNCTION = """
CREATE OR REPLACE FUNCTION audit_log_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_log e append-only: operacao % bloqueada', TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$;
"""


def upgrade() -> None:
    bind = op.get_bind()
    for enum_type in (direction, side, trade_status, audit_event_type):
        enum_type.create(bind, checkfirst=True)

    op.create_table(
        "instruments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("digits", sa.Integer(), nullable=False),
        sa.Column("point", sa.Numeric(precision=18, scale=10), nullable=False),
        sa.Column("contract_size", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("digits >= 0", name="ck_instruments_digits_nao_negativo"),
        sa.CheckConstraint("contract_size > 0", name="ck_instruments_contract_size_positivo"),
        sa.PrimaryKeyConstraint("id", name="pk_instruments"),
        sa.UniqueConstraint("symbol", name="uq_instruments_symbol"),
    )
    op.create_index("ix_instruments_created_at", "instruments", ["created_at"])

    op.create_table(
        "signals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("instrument_id", sa.Integer(), nullable=False),
        sa.Column("direction", direction, nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("fused_score", sa.Float(), nullable=False),
        sa.Column("sentiment_score", sa.Float(), nullable=True),
        sa.Column("sentiment_confidence", sa.Float(), nullable=True),
        sa.Column("technical_score", sa.Float(), nullable=True),
        sa.Column("weight_version", sa.String(length=64), nullable=False),
        sa.Column("inputs", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_signals_confidence_0_1"),
        sa.CheckConstraint(
            "fused_score >= -1 AND fused_score <= 1", name="ck_signals_fused_score_menos1_1"
        ),
        sa.CheckConstraint(
            "sentiment_score IS NULL OR (sentiment_score >= -1 AND sentiment_score <= 1)",
            name="ck_signals_sentiment_score_menos1_1",
        ),
        sa.CheckConstraint(
            "technical_score IS NULL OR (technical_score >= -1 AND technical_score <= 1)",
            name="ck_signals_technical_score_menos1_1",
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["instruments.id"],
            name="fk_signals_instrument_id_instruments",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_signals"),
    )
    op.create_index("ix_signals_created_at", "signals", ["created_at"])
    op.create_index("ix_signals_instrument_id", "signals", ["instrument_id"])
    op.create_index("ix_signals_weight_version", "signals", ["weight_version"])

    op.create_table(
        "trades",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("client_request_id", sa.String(length=64), nullable=False),
        sa.Column("instrument_id", sa.Integer(), nullable=False),
        sa.Column("signal_id", sa.Integer(), nullable=True),
        sa.Column("side", side, nullable=False),
        sa.Column("status", trade_status, nullable=False),
        sa.Column("volume", sa.Float(), nullable=False),
        sa.Column("entry_price", sa.Float(), nullable=True),
        # NOT NULL é a regra "stop loss obrigatório" (§4 do PRD) expressa em banco.
        sa.Column("stop_loss", sa.Float(), nullable=False),
        sa.Column("take_profit", sa.Float(), nullable=True),
        sa.Column("trading_mode", sa.String(length=8), nullable=False),
        sa.Column("mt5_order_id", sa.BigInteger(), nullable=True),
        sa.Column("mt5_position_id", sa.BigInteger(), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("volume > 0", name="ck_trades_volume_positivo"),
        sa.CheckConstraint("stop_loss > 0", name="ck_trades_stop_loss_positivo"),
        sa.CheckConstraint(
            "take_profit IS NULL OR take_profit > 0", name="ck_trades_take_profit_positivo"
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["instruments.id"],
            name="fk_trades_instrument_id_instruments",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["signal_id"], ["signals.id"], name="fk_trades_signal_id_signals", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_trades"),
        # Idempotência de ordem: um retry colide aqui, não no broker.
        sa.UniqueConstraint("client_request_id", name="uq_trades_client_request_id"),
    )
    op.create_index("ix_trades_client_request_id", "trades", ["client_request_id"])
    op.create_index("ix_trades_created_at", "trades", ["created_at"])
    op.create_index("ix_trades_instrument_id", "trades", ["instrument_id"])
    op.create_index("ix_trades_signal_id", "trades", ["signal_id"])
    op.create_index("ix_trades_status", "trades", ["status"])
    op.create_index("ix_trades_mt5_order_id", "trades", ["mt5_order_id"])
    op.create_index("ix_trades_mt5_position_id", "trades", ["mt5_position_id"])

    op.create_table(
        "outcomes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("trade_id", sa.Integer(), nullable=False),
        sa.Column("signal_id", sa.Integer(), nullable=True),
        sa.Column("exit_price", sa.Float(), nullable=False),
        sa.Column("pnl", sa.Float(), nullable=False),
        sa.Column("pnl_pct", sa.Float(), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column("predicted_direction", direction, nullable=True),
        sa.Column("actual_direction", direction, nullable=False),
        sa.Column("was_correct", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("duration_seconds >= 0", name="ck_outcomes_duration_nao_negativa"),
        sa.ForeignKeyConstraint(
            ["trade_id"], ["trades.id"], name="fk_outcomes_trade_id_trades", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["signal_id"], ["signals.id"], name="fk_outcomes_signal_id_signals", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_outcomes"),
        # Um trade encerrado gera exatamente um outcome; reprocessar não duplica.
        sa.UniqueConstraint("trade_id", name="uq_outcomes_trade_id"),
    )
    op.create_index("ix_outcomes_created_at", "outcomes", ["created_at"])
    op.create_index("ix_outcomes_signal_id", "outcomes", ["signal_id"])
    op.create_index("ix_outcomes_was_correct", "outcomes", ["was_correct"])

    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("event_type", audit_event_type, nullable=False),
        sa.Column("client_request_id", sa.String(length=64), nullable=True),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_log"),
    )
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"])
    op.create_index("ix_audit_log_event_type", "audit_log", ["event_type"])
    op.create_index("ix_audit_log_client_request_id", "audit_log", ["client_request_id"])

    op.execute(APPEND_ONLY_FUNCTION)
    op.execute(
        "CREATE TRIGGER audit_log_no_mutation "
        "BEFORE UPDATE OR DELETE ON audit_log "
        "FOR EACH ROW EXECUTE FUNCTION audit_log_append_only();"
    )
    op.execute(
        "CREATE TRIGGER audit_log_no_truncate "
        "BEFORE TRUNCATE ON audit_log "
        "FOR EACH STATEMENT EXECUTE FUNCTION audit_log_append_only();"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_truncate ON audit_log;")
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_mutation ON audit_log;")
    op.execute("DROP FUNCTION IF EXISTS audit_log_append_only();")

    op.drop_table("audit_log")
    op.drop_table("outcomes")
    op.drop_table("trades")
    op.drop_table("signals")
    op.drop_table("instruments")

    bind = op.get_bind()
    for enum_type in (audit_event_type, trade_status, side, direction):
        enum_type.drop(bind, checkfirst=True)
