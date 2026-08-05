"""História 4 — a fila da coleta existe e a task de news está registrada nela.

Nenhum teste aqui fala com o Redis: criar o app Celery não abre conexão.
"""

from __future__ import annotations

from backend.celery_app import COLLECTION_QUEUE, create_celery_app
from backend.collection.tasks import collect_news_task
from backend.config import Settings

TASK_NEWS = "collection.collect_news"


def test_app_usa_broker_da_config() -> None:
    settings = Settings(
        _env_file=None,
        celery_broker_url="redis://localhost:6379/9",
        celery_result_backend="redis://localhost:6379/8",
    )
    app = create_celery_app(settings)
    assert app.conf.broker_url == "redis://localhost:6379/9"
    assert app.conf.result_backend == "redis://localhost:6379/8"


def test_fila_default_e_a_da_coleta() -> None:
    """News e twitter (história 5) compartilham fila: alimentam o mesmo analisador."""
    app = create_celery_app(Settings(_env_file=None))
    assert app.conf.task_default_queue == COLLECTION_QUEUE


def test_serializacao_e_json() -> None:
    """Pickle no broker seria execução de código arbitrário vinda da fila."""
    app = create_celery_app(Settings(_env_file=None))
    assert app.conf.task_serializer == "json"
    assert app.conf.accept_content == ["json"]


def test_ack_tardio_para_reprocessar_feed_apos_queda() -> None:
    """Reprocesso é seguro porque o dedupe é do banco."""
    app = create_celery_app(Settings(_env_file=None))
    assert app.conf.task_acks_late is True


def test_task_de_news_registrada() -> None:
    assert collect_news_task.name == TASK_NEWS
