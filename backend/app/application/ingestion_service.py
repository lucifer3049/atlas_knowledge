"""IngestionService:parse → chunk → embed 三段 pipeline 的 use case(T2.5;PHASE_2 §8)。

**不含任何 Celery 依賴**(§C.2):任務殼(`workers/tasks/ingest.py`)只負責重試策略與
soft timeout,狀態機、冪等、失敗語意全在此,故可用 fake adapter + 測試 DB 直接測。

狀態機(§8.1):`pending → parsing → chunking → embedding → ready`,任一 stage 失敗
→ `failed`。推進一律用**條件更新(claim)**;每個 stage 的 claim 都接受 `failed`
作為來源狀態,stage-level retry(v1.2 §8)因此只需決定「從哪個 stage 重新入列」。

冪等手段(§8.2,每 stage 一種):
- parse:claim(0 rows 即 return)+ normalized.json 覆寫
- chunk:先刪後插
- embed:`embedding is null` 為進度游標,批批 commit,中斷重跑自然續傳

錯誤語意:`ParseError` 與 embedding 的 auth / context_length / provider_error 為
**不可重試**(壞檔 / 組態錯,重跑一百次仍舊失敗)→ 直接標 failed;I/O 與
rate_limited / transient 包成 `TransientStageError` 上拋,由任務殼指數退避重試。
"""
from collections.abc import Sequence
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.domain.entities.chunk import ChunkDraft
from app.domain.entities.document import NormalizedDocument
from app.domain.ports.chunking import ChunkingConfig
from app.domain.ports.embedding import EmbeddingError, EmbeddingProvider
from app.domain.ports.parser import ParseError
from app.domain.ports.storage import ObjectStorage
from app.domain.ports.task_queue import TaskQueue
from app.infrastructure.db.models import Document
from app.infrastructure.db.repositories.chunks import ChunkRepository
from app.infrastructure.db.repositories.documents import DocumentRepository
from app.infrastructure.db.repositories.ingestion_jobs import IngestionJobRepository
from app.infrastructure.db.repositories.knowledge_sources import KnowledgeSourceRepository
from app.infrastructure.parsing.chunking.registry import strategy_for_source_type
from app.infrastructure.parsing.registry import get_parser

_logger = structlog.get_logger()

# embedding 錯誤中屬「暫時性、值得重試」的兩類(§8.2 embed 列)。
_RETRYABLE_EMBEDDING_CODES = frozenset({"rate_limited", "transient"})

NORMALIZED_KEY_SUFFIX = "normalized.json"


class TransientStageError(Exception):
    """可重試的 stage 錯誤(I/O、上游暫時不可用);任務殼據此指數退避。"""


def normalized_key(document_id: UUID) -> str:
    return f"documents/{document_id}/{NORMALIZED_KEY_SUFFIX}"


class IngestionService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        storage: ObjectStorage,
        embedding: EmbeddingProvider,
        task_queue: TaskQueue,
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
        self._storage = storage
        self._embedding = embedding
        self._task_queue = task_queue
        self._settings = settings

    # --- stage 1:parse -----------------------------------------------------

    async def parse(self, document_id: UUID) -> None:
        async with self._session_factory() as session:
            doc = await self._claim(session, document_id, allowed_from=("pending", "failed"),
                                    to="parsing", stage="parse")
            if doc is None:
                return
            storage_key, mime, filename = doc.storage_key, doc.mime, doc.filename

            try:
                data = await self._read(storage_key)
            except FileNotFoundError:
                await self._fail(session, document_id, "parse", "找不到原始檔案")
                return

            try:
                normalized = get_parser(mime).parse(data, filename)
            except ParseError as exc:
                # 壞檔 / 加密 / 超量體:NEVER 重試(§8.2)
                await self._fail(session, document_id, "parse", str(exc))
                return

            await self._write(normalized_key(document_id), normalized.model_dump_json().encode())
            await self._succeed(session, document_id, "parse", next_status="chunking")

        _logger.info("ingest.parse_done", document_id=str(document_id),
                     blocks=len(normalized.blocks))
        self._task_queue.enqueue_chunk_document(document_id)

    # --- stage 2:chunk -----------------------------------------------------

    async def chunk(self, document_id: UUID) -> None:
        async with self._session_factory() as session:
            doc = await self._claim(session, document_id, allowed_from=("chunking", "failed"),
                                    to="chunking", stage="chunk")
            if doc is None:
                return

            try:
                raw = await self._read(normalized_key(document_id))
            except FileNotFoundError:
                # parse 產物不見了(手動清理 / 舊資料):重跑本階段無用,退回 failed,
                # 由使用者 retry — 屆時 first_failed_stage 會指回 chunk,故訊息明示需重傳。
                await self._fail(session, document_id, "chunk",
                                 "找不到解析結果,請刪除後重新上傳")
                return

            try:
                normalized = NormalizedDocument.model_validate_json(raw)
            except ValueError as exc:
                await self._fail(session, document_id, "chunk", f"解析結果格式錯誤:{exc}")
                return

            source = await KnowledgeSourceRepository(session).get(doc.source_id)
            source_type = source.type if source is not None else "upload"
            drafts = self._build_chunks(normalized, source_type)

            await ChunkRepository(session).replace_for_document(document_id, drafts)
            await self._succeed(session, document_id, "chunk", next_status="embedding")

        _logger.info("ingest.chunk_done", document_id=str(document_id), chunks=len(drafts))
        self._task_queue.enqueue_embed_chunks(document_id)

    def _build_chunks(self, doc: NormalizedDocument, source_type: str) -> list[ChunkDraft]:
        cfg = ChunkingConfig(
            target_tokens=self._settings.chunk_target_tokens,
            overlap_tokens=self._settings.chunk_overlap_tokens,
        )
        return strategy_for_source_type(source_type).chunk(doc, cfg)

    # --- stage 3:embed -----------------------------------------------------

    async def embed(self, document_id: UUID) -> None:
        async with self._session_factory() as session:
            doc = await self._claim(session, document_id, allowed_from=("embedding", "failed"),
                                    to="embedding", stage="embed")
            if doc is None:
                return

            chunks_repo = ChunkRepository(session)
            batch_size = self._settings.embedding_batch_size
            embedded = 0
            while batch := await chunks_repo.list_unembedded(document_id, limit=batch_size):
                try:
                    vectors = await self._embedding.embed([c.text for c in batch])
                except EmbeddingError as exc:
                    if exc.code in _RETRYABLE_EMBEDDING_CODES:
                        # 已完成的批次已 commit;重試自游標續傳,NEVER 重嵌
                        raise TransientStageError(exc.message) from exc
                    await self._fail(session, document_id, "embed", exc.message)
                    return
                await chunks_repo.set_embeddings(
                    dict(zip((c.id for c in batch), vectors, strict=True)),
                    version=self._embedding.version,
                )
                await session.commit()  # 批批 commit = 進度落地(§8.2)
                embedded += len(batch)

            await self._succeed(session, document_id, "embed", next_status="ready")

        _logger.info("ingest.embed_done", document_id=str(document_id), embedded=embedded)

    # --- 共用:狀態機與 ingestion_jobs ---------------------------------------

    async def _claim(
        self,
        session: AsyncSession,
        document_id: UUID,
        *,
        allowed_from: Sequence[str],
        to: str,
        stage: str,
    ) -> Document | None:
        """claim 成功回 document;0 rows(已被處理 / 文件已刪)回 None,任務直接 return。"""
        documents = DocumentRepository(session)
        if not await documents.claim(document_id, allowed_from=allowed_from, to=to):
            _logger.info("ingest.claim_skipped", document_id=str(document_id), stage=stage)
            await session.rollback()
            return None
        await IngestionJobRepository(session).mark_running(document_id, stage)
        await session.commit()
        return await documents.get(document_id)

    async def _succeed(
        self, session: AsyncSession, document_id: UUID, stage: str, *, next_status: str
    ) -> None:
        await IngestionJobRepository(session).mark_finished(
            document_id, stage, status="succeeded"
        )
        await DocumentRepository(session).set_status(document_id, next_status)
        await session.commit()

    async def _fail(
        self, session: AsyncSession, document_id: UUID, stage: str, error: str
    ) -> None:
        await IngestionJobRepository(session).mark_finished(
            document_id, stage, status="failed", error=error
        )
        await DocumentRepository(session).set_failed(document_id, error)
        await session.commit()
        _logger.warning("ingest.stage_failed", document_id=str(document_id), stage=stage)

    async def mark_stage_failed(self, document_id: UUID, stage: str, error: str) -> None:
        """供任務殼在重試耗盡 / soft timeout 時收尾(§8.2 v1.2 補遺)。"""
        async with self._session_factory() as session:
            await self._fail(session, document_id, stage, error)

    # --- storage I/O(失敗一律轉可重試,FileNotFoundError 由呼叫端另行處理)---

    async def _read(self, key: str) -> bytes:
        try:
            return await self._storage.get(key)
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise TransientStageError(f"讀取儲存失敗:{exc}") from exc

    async def _write(self, key: str, data: bytes) -> None:
        try:
            await self._storage.put(key, data)
        except OSError as exc:
            raise TransientStageError(f"寫入儲存失敗:{exc}") from exc
