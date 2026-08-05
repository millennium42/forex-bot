"""Tarefas Celery da camada de coleta.

Ficam separadas dos coletores para que importar `news_collector` em teste não
arraste o app Celery junto.
"""

from __future__ import annotations

from backend.celery_app import celery_app
from backend.collection.news_collector import collect_news
from backend.db import session_scope


# O Celery não distribui type hints; o decorator entra como untyped no mypy strict.
@celery_app.task(name="collection.collect_news")  # type: ignore[untyped-decorator]
def collect_news_task() -> dict[str, int]:
    """Coleta os feeds RSS configurados. Reexecutar é seguro: o dedupe é no banco."""
    with session_scope() as session:
        return collect_news(session)
