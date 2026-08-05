"""Fixtures compartilhadas.

Dois níveis de teste de banco:

* `session`  — SQLite em memória, schema via `create_all`. Cobre constraints
  portáveis (NOT NULL, UNIQUE, CHECK, FK). Roda em qualquer máquina.
* `pg_session` — Postgres real, schema via Alembic. Cobre o que só existe no
  Postgres: triggers, tipos enum nativos, JSONB. Marcado `integration`; pulado
  quando não há Postgres acessível.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from backend.models import Base


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection: object, _record: object) -> None:
    """SQLite ignora FK por default; sem isso os testes de FK passariam de mentira."""
    if type(dbapi_connection).__module__.startswith("sqlite3"):
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with factory() as s:
        yield s
    engine.dispose()


@pytest.fixture
def pg_url() -> str:
    """URL do banco **de teste**, nunca o de desenvolvimento.

    Os testes de migration fazem `downgrade base` no teardown. Apontados para o
    banco de dev, eles apagam o schema e o bot morre no ciclo seguinte com
    `relation "audit_log" does not exist`. Por isso o sufixo `_test`, criado sob
    demanda em `pg_engine`.
    """
    override = os.environ.get("TEST_DATABASE_URL")
    if override:
        return override
    base = os.environ.get(
        "DATABASE_URL", "postgresql+psycopg://forex:forex@127.0.0.1:5432/forex_bot"
    )
    return base if base.endswith("_test") else f"{base}_test"


def _garantir_banco_de_teste(url: str) -> None:
    """Cria o banco de teste se ainda não existir.

    CREATE DATABASE não roda dentro de transação — daí o AUTOCOMMIT.
    """
    nome = url.rsplit("/", 1)[-1]
    manutencao = create_engine(
        url.rsplit("/", 1)[0] + "/postgres",
        isolation_level="AUTOCOMMIT",
        connect_args={"connect_timeout": 3},
    )
    try:
        with manutencao.connect() as conn:
            existe = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": nome}
            ).scalar()
            if not existe:
                conn.execute(text(f'CREATE DATABASE "{nome}"'))
    finally:
        manutencao.dispose()


@pytest.fixture
def pg_engine(pg_url: str) -> Iterator[Engine]:
    try:
        _garantir_banco_de_teste(pg_url)
    except Exception as exc:  # pragma: no cover - depende do ambiente
        pytest.skip(f"Postgres indisponível: {exc}")

    # connect_timeout curto: sem Postgres de pé, o teste precisa pular em
    # segundos, não ficar preso no timeout default do TCP.
    engine = create_engine(pg_url, future=True, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - depende do ambiente
        engine.dispose()
        pytest.skip(f"Postgres indisponível em {pg_url}: {exc}")
    yield engine
    engine.dispose()
