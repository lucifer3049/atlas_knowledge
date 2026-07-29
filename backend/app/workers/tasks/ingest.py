"""文件匯入 pipeline 任務(queue=`ingest`;PHASE_2 §8)。

**任務殼只管重試策略與 soft timeout**;狀態機、冪等、失敗語意全在 `IngestionService`
(§C.2:worker 不寫業務邏輯)。三段各自入列而非串 chain,任一段失敗後可自該段
stage-level retry(v1.2 §8)。

重試(§8.2):`TransientStageError` / I/O 錯誤指數退避,parse・chunk ≤3、embed ≤5;
`ParseError` 與 embedding 的 auth / context_length / provider_error 由 service 直接標
failed,NEVER 進重試迴圈。重試耗盡、soft timeout、非預期例外一律收斂為該 stage 的 failed
——文件 NEVER 卡在中間狀態。

soft_time_limit(v1.2 §8):parse 120s / chunk 120s / embed 600s。
(Windows 開發用 solo pool 不支援 soft limit,正式環境 prefork 生效。)
"""
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

import structlog
from celery.exceptions import Ignore, Retry, SoftTimeLimitExceeded
from sqlalchemy.exc import SQLAlchemyError

from app.application.ingestion_service import IngestionService, TransientStageError
from app.core.config import settings
from app.core.db import create_engine
from app.core.wiring import build_embedding
from app.infrastructure.db.session import create_session_factory
from app.infrastructure.storage.local_fs import LocalFileStorage
from app.workers.celery_app import celery_app
from app.workers.run_async import run_async

_logger = structlog.get_logger()

# I/O 類例外一律視為可重試(§8.2);程式錯誤等其他例外重試無用,直接標 failed。
_RETRYABLE = (TransientStageError, OSError, SQLAlchemyError)
_MAX_BACKOFF_S = 60

_PARSE_MAX_RETRIES = 3
_CHUNK_MAX_RETRIES = 3
_EMBED_MAX_RETRIES = 5


async def _with_service(fn: Callable[[IngestionService], Awaitable[None]]) -> None:
    """worker 內建立 engine/adapter(綁定當前 run_async loop);用完即棄(同 T1.7 慣例)。"""
    # 延遲 import:celery_queue 於模組層 import 本模組的任務,頂層互 import 會成環。
    from app.infrastructure.tasks.celery_queue import CeleryTaskQueue

    engine = create_engine()
    embedding = build_embedding(settings)
    service = IngestionService(
        session_factory=create_session_factory(engine),
        storage=LocalFileStorage(settings.storage_root),
        embedding=embedding,
        task_queue=CeleryTaskQueue(),
        settings=settings,
    )
    try:
        await fn(service)
    finally:
        aclose = getattr(embedding, "aclose", None)
        if aclose is not None:
            await aclose()
        await engine.dispose()


def _fail(document_id: UUID, stage: str, error: str) -> None:
    async def run(service: IngestionService) -> None:
        await service.mark_stage_failed(document_id, stage, error)

    try:
        run_async(_with_service(run))
    except Exception:
        # 連標記失敗都失敗(DB 不可用):只 log;文件留在執行中狀態,由人工 retry 復原。
        _logger.error("ingest.mark_failed_error", document_id=str(document_id), stage=stage,
                      exc_info=True)


def _run_stage(
    task: Any,
    *,
    stage: str,
    document_id: str,
    max_retries: int,
    run: Callable[[IngestionService, UUID], Awaitable[None]],
) -> None:
    doc_id = UUID(document_id)
    try:
        run_async(_with_service(lambda service: run(service, doc_id)))
    except SoftTimeLimitExceeded:
        # soft timeout 走該 stage 的失敗語意(v1.2 §8)
        _fail(doc_id, stage, f"{stage} 階段執行逾時")
    except _RETRYABLE as exc:
        if task.request.retries >= max_retries:
            _fail(doc_id, stage, f"{stage} 階段重試耗盡:{exc}")
            return
        countdown = min(2 ** task.request.retries, _MAX_BACKOFF_S)
        raise task.retry(exc=exc, countdown=countdown) from exc
    except (Retry, Ignore):
        raise
    except Exception as exc:
        # 非預期例外(程式錯誤等):重試無用,直接標 failed,NEVER 讓文件卡住
        _logger.error("ingest.unexpected_error", document_id=document_id, stage=stage,
                      exc_info=True)
        _fail(doc_id, stage, f"{stage} 階段發生非預期錯誤:{type(exc).__name__}")


@celery_app.task(bind=True, name="parse_document", ignore_result=True, soft_time_limit=120)  # type: ignore[untyped-decorator]  # celery 未附型別
def parse_document(self: Any, document_id: str) -> None:
    _run_stage(
        self,
        stage="parse",
        document_id=document_id,
        max_retries=_PARSE_MAX_RETRIES,
        run=lambda service, doc_id: service.parse(doc_id),
    )


@celery_app.task(bind=True, name="chunk_document", ignore_result=True, soft_time_limit=120)  # type: ignore[untyped-decorator]  # celery 未附型別
def chunk_document(self: Any, document_id: str) -> None:
    _run_stage(
        self,
        stage="chunk",
        document_id=document_id,
        max_retries=_CHUNK_MAX_RETRIES,
        run=lambda service, doc_id: service.chunk(doc_id),
    )


@celery_app.task(bind=True, name="embed_chunks", ignore_result=True, soft_time_limit=600)  # type: ignore[untyped-decorator]  # celery 未附型別
def embed_chunks(self: Any, document_id: str) -> None:
    _run_stage(
        self,
        stage="embed",
        document_id=document_id,
        max_retries=_EMBED_MAX_RETRIES,
        run=lambda service, doc_id: service.embed(doc_id),
    )


@celery_app.task(  # type: ignore[untyped-decorator]  # celery 未附型別
    name="purge_document",
    ignore_result=True,
    autoretry_for=(OSError,),  # I/O 錯誤指數退避重試(§8.2)
    max_retries=3,
    retry_backoff=True,
)
def purge_document(storage_prefix: str) -> None:
    """刪除文件的整個 storage prefix(§8.2;delete 天然冪等)。"""
    storage = LocalFileStorage(settings.storage_root)
    run_async(storage.delete_prefix(storage_prefix))
    _logger.info("purge_document_done", storage_prefix=storage_prefix)
