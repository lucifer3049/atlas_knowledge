"""`TaskQueue` port(PHASE_1 §6 末)。

隔離 Celery 依賴:orchestrator 只依賴此介面,測試用 fake、正式用 Celery adapter。
P1 唯一入列點 = 對話首輪後的標題生成(T1.7 提供實作;T1.4 以此 port 呼叫)。
本檔為純 domain,NEVER import 任何框架 / SDK。
"""
from typing import Protocol
from uuid import UUID


class TaskQueue(Protocol):
    def enqueue_generate_title(self, conversation_id: UUID) -> None: ...

    # P2:文件匯入 pipeline 三段(T2.5)與刪除後的 storage 清理(T2.1);PHASE_2 §8.2。
    # 三段各自入列而非串成 chain:任一段失敗後可自該段 stage-level retry(v1.2 §8)。
    def enqueue_parse_document(self, document_id: UUID) -> None: ...

    def enqueue_chunk_document(self, document_id: UUID) -> None: ...

    def enqueue_embed_chunks(self, document_id: UUID) -> None: ...

    def enqueue_purge_document(self, storage_prefix: str) -> None: ...
