"""Engine e sessões SQLAlchemy.

O engine é criado sob demanda, nunca no import: importar um módulo não pode
depender de o Postgres estar de pé.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.config import get_settings


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    settings = get_settings()
    return create_engine(
        settings.database_url,
        # Conexão de trading fica ociosa entre sinais; sem isso o primeiro
        # comando após uma pausa longa estoura com conexão morta.
        pool_pre_ping=True,
        future=True,
    )


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Sessão transacional: commit no sucesso, rollback em qualquer exceção."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_engine_cache() -> None:
    """Descarta engine e factory memoizados. Uso em testes e troca de config."""
    get_engine.cache_clear()
    get_session_factory.cache_clear()
