"""App Celery do bot.

Uma fila só para toda a camada de coleta (`collection`): notícias e tweets
alimentam o mesmo analisador de sentimento e competem pelo mesmo worker.

Criar o app não abre conexão com o broker — o Celery só conecta na primeira
publicação ou no start do worker. Importar este módulo é seguro sem Redis.
"""

from __future__ import annotations

from celery import Celery

from backend.config import Settings, get_settings

COLLECTION_QUEUE = "collection"


def create_celery_app(settings: Settings | None = None) -> Celery:
    settings = settings or get_settings()
    app = Celery(
        "forex_bot",
        broker=settings.celery_broker_url,
        backend=settings.celery_result_backend,
        include=["backend.collection.tasks"],
    )
    app.conf.update(
        task_default_queue=COLLECTION_QUEUE,
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        # Ack depois de executar: worker morto no meio de um feed devolve a
        # tarefa para a fila. O dedupe por hash torna o reprocesso inofensivo.
        task_acks_late=True,
        worker_prefetch_multiplier=1,
    )
    return app


celery_app = create_celery_app()
