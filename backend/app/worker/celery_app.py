from celery import Celery

from app.core.config import Settings


def create_celery(settings: Settings) -> Celery:
    app = Celery("knowforge", broker=settings.celery_broker_url, include=["app.worker.tasks"])
    app.conf.update(
        task_default_queue="knowforge.documents",
        broker_transport_options={
            "global_keyprefix": settings.redis_prefix + "celery:",
            "visibility_timeout": 3600,
        },
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        broker_connection_retry_on_startup=True,
        task_ignore_result=True,
        beat_schedule={"recover-pending-documents": {"task": "knowforge.recover", "schedule": 30.0}},
    )
    return app


celery_app = create_celery(Settings())
