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
    return os.environ.get(
        "DATABASE_URL", "postgresql+psycopg://forex:forex@127.0.0.1:5432/forex_bot"
    )


@pytest.fixture
def pg_engine(pg_url: str) -> Iterator[Engine]:
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
