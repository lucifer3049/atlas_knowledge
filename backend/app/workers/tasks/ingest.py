"""文件匯入 pipeline 任務(queue=`ingest`;PHASE_2 §8)。

T2.1 僅銜接 API 與任務邊界:
- `parse_document` 為 **stub**(狀態機、claim、parse/chunk/embed 三段實作屬 T2.5);
  現階段只記錄收到的 document_id,NEVER 推進狀態,以免與 T2.5 的 claim 語意打架。
- `purge_document` 為完整實作:僅依賴 T2.1 的 ObjectStorage port,DELETE 端點(D12)需要它。
"""
import structlog

from app.core.config import settings
from app.infrastructure.storage.local_fs import LocalFileStorage
from app.workers.celery_app import celery_app
from app.workers.run_async import run_async

_logger = structlog.get_logger()


@celery_app.task(name="parse_document", ignore_result=True)  # type: ignore[untyped-decorator]  # celery 未附型別
def parse_document(document_id: str) -> None:
    # TODO(T2.5):claim → storage 取原檔 → parser → normalized.json → enqueue chunk_document
    _logger.info("parse_document_stub", document_id=document_id)


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
