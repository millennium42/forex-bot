"""História 2 — migration aplica/reverte e o append-only é do banco.

Exige Postgres: trigger, tipo enum nativo e JSONB não existem no SQLite.
Pulado automaticamente quando o Postgres não está acessível.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect, text

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
TABELAS = {"instruments", "signals", "trades", "outcomes", "audit_log", "documents"}


def _alembic_config(url: str) -> Config:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "backend" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


@pytest.fixture
def migrated(pg_engine: Engine, pg_url: str) -> Iterator[Engine]:
    cfg = _alembic_config(pg_url)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield pg_engine
    command.downgrade(cfg, "base")


def test_upgrade_cria_todas_as_tabelas(migrated: Engine) -> None:
    assert set(inspect(migrated).get_table_names()) >= TABELAS


def test_downgrade_remove_tudo(pg_engine: Engine, pg_url: str) -> None:
    cfg = _alembic_config(pg_url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    assert TABELAS.isdisjoint(set(inspect(pg_engine).get_table_names()))


def _insere_evento(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO audit_log (event_type, actor, payload) "
                "VALUES ('order_placed', 'system', '{}'::jsonb)"
            )
        )


def test_audit_log_aceita_insert(migrated: Engine) -> None:
    _insere_evento(migrated)
    with migrated.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM audit_log")).scalar_one() == 1


def test_update_em_audit_log_falha(migrated: Engine) -> None:
    _insere_evento(migrated)
    with pytest.raises(Exception, match="append-only"), migrated.begin() as conn:
        conn.execute(text("UPDATE audit_log SET actor = 'hacker'"))


def test_delete_em_audit_log_falha(migrated: Engine) -> None:
    _insere_evento(migrated)
    with pytest.raises(Exception, match="append-only"), migrated.begin() as conn:
        conn.execute(text("DELETE FROM audit_log"))


def test_truncate_em_audit_log_falha(migrated: Engine) -> None:
    """DELETE bloqueado não basta: TRUNCATE não dispara trigger de linha."""
    _insere_evento(migrated)
    with pytest.raises(Exception, match="append-only"), migrated.begin() as conn:
        conn.execute(text("TRUNCATE audit_log"))


def test_linha_sobrevive_as_tentativas_de_mutacao(migrated: Engine) -> None:
    _insere_evento(migrated)
    for sql in ("UPDATE audit_log SET actor = 'x'", "DELETE FROM audit_log"):
        with pytest.raises(Exception, match="append-only"), migrated.begin() as conn:
            conn.execute(text(sql))

    with migrated.connect() as conn:
        assert conn.execute(text("SELECT actor FROM audit_log")).scalar_one() == "system"


def test_stop_loss_nulo_e_recusado_no_postgres(migrated: Engine) -> None:
    with migrated.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO instruments "
                "(symbol, digits, point, contract_size, min_volume, volume_step, "
                "volume_max, active) "
                "VALUES ('EURUSD', 5, 0.00001, 100000, 0.01, 0.01, 100.0, true)"
            )
        )
    with pytest.raises(Exception, match="stop_loss"), migrated.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO trades "
                "(client_request_id, instrument_id, side, status, volume, stop_loss, "
                "trading_mode, strategy) "
                "SELECT 'req-1', id, 'buy', 'pending', 0.1, NULL, 'demo', 'technical' "
                "FROM instruments"
            )
        )
