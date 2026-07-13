"""暫置 `TaskQueue` 實作(T1.4)。

Celery app 與 `generate_title` 任務屬 T1.7;在其就位前,標題生成入列先由本 adapter
承接(僅記錄意圖,不實際入列)。T1.7 完成後 deps 改注入 `CeleryTaskQueue`,本檔即退場。
NEVER 在此放任何業務邏輯或 SQL。
"""
from uuid import UUID

import structlog

_logger = structlog.get_logger()


class NoopTaskQueue:
    def enqueue_generate_title(self, conversation_id: UUID) -> None:
        # 標題生成尚待 T1.7 的 Celery 任務;此處僅留軌跡(NEVER log 訊息內容)。
        _logger.info("title_enqueue_skipped", conversation_id=str(conversation_id))
