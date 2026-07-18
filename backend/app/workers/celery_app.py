"""Celery app(唯一入口;§C.3 workers/celery_app.py)。

broker / backend 皆走 Redis(§F.3)。task_publish_retry=False:broker 不可用時
producer(chat)快速失敗而非阻塞——標題入列為 best-effort。
"""
from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "falskapi",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.workers.tasks.titles", "app.workers.tasks.ingest"],
)
celery_app.conf.update(
    # 文件匯入為重活,獨立 queue(§2.3:worker -Q ingest,default)。
    task_routes={
        "parse_document": {"queue": "ingest"},
        "purge_document": {"queue": "ingest"},
    },
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_publish_retry=False,
)
