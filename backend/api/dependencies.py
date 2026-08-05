"""Dependências injetáveis do FastAPI."""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy.orm import Session

from backend.db import get_session_factory


def get_db() -> Generator[Session, None, None]:
    """Fornece uma sessão transacional do banco de dados por requisição."""
    db = get_session_factory()()
    try:
        yield db
    finally:
        db.close()
