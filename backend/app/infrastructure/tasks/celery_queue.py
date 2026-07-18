"""CeleryTaskQueue:`TaskQueue` port 的 Celery adapter(§C.3、T1.7)。

producer 端(chat API)呼叫;enqueue 為 best-effort:broker 不可用等失敗只 log warning,
NEVER 阻斷 chat 回應(標題生成非關鍵路徑)。
"""
from uuid import UUID

import structlog

from app.workers.tasks.ingest import parse_document, purge_document
from app.workers.tasks.titles import generate_title

_logger = structlog.get_logger()


class CeleryTaskQueue:
    def enqueue_generate_title(self, conversation_id: UUID) -> None:
        try:
            generate_title.delay(str(conversation_id))
        except Exception:
            _logger.warning("title_enqueue_failed", conversation_id=str(conversation_id))

    def enqueue_parse_document(self, document_id: UUID) -> None:
        # 非 best-effort:入列失敗 MUST 讓上層知道(文件會卡住),由 service 標 failed。
        parse_document.delay(str(document_id))

    def enqueue_purge_document(self, storage_prefix: str) -> None:
        purge_document.delay(storage_prefix)
