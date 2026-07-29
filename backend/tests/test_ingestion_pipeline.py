"""T2.5 ingestion pipeline 測試(IngestionService + 測試 DB;PHASE_2 §12.2)。

任務本體直接呼叫函式測狀態機與冪等(NEVER 依賴 Celery eager 模式測交易行為,§C.5.7)。
storage 為 in-memory fake、embedding 為決定性 fake(tests/fakes.py),CI NEVER 打外部 API。
"""
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.ingestion_service import (
    IngestionService,
    TransientStageError,
    normalized_key,
)
from app.core.config import settings
from app.domain.ports.embedding import EmbeddingError
from app.infrastructure.db.models import (
    Document,
    DocumentChunk,
    IngestionJob,
    KnowledgeSource,
    User,
)
from tests.fakes import FakeEmbeddingProvider

pytestmark = pytest.mark.anyio

_TXT = "第一段內容。\n\n第二段內容。".encode()


class FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.fail_on_get: set[str] = set()

    async def put(self, key: str, data: bytes) -> None:
        self.objects[key] = data

    async def get(self, key: str) -> bytes:
        if key in self.fail_on_get:
            raise OSError("disk error")
        if key not in self.objects:
            raise FileNotFoundError(key)
        return self.objects[key]

    async def delete_prefix(self, prefix: str) -> None:
        for key in [k for k in self.objects if k.startswith(prefix)]:
            del self.objects[key]


class RecordingQueue:
    def __init__(self) -> None:
        self.parse: list[UUID] = []
        self.chunk: list[UUID] = []
        self.embed: list[UUID] = []
        self.purge: list[str] = []

    def enqueue_generate_title(self, conversation_id: UUID) -> None:
        pass

    def enqueue_parse_document(self, document_id: UUID) -> None:
        self.parse.append(document_id)

    def enqueue_chunk_document(self, document_id: UUID) -> None:
        self.chunk.append(document_id)

    def enqueue_embed_chunks(self, document_id: UUID) -> None:
        self.embed.append(document_id)

    def enqueue_purge_document(self, storage_prefix: str) -> None:
        self.purge.append(storage_prefix)


class FailingEmbedding(FakeEmbeddingProvider):
    def __init__(self, code: str, *, fail_after: int = 0) -> None:
        super().__init__()
        self._code = code
        self._fail_after = fail_after

    async def embed(self, texts: list[str]) -> list[list[float]]:  # type: ignore[override]
        if self.calls >= self._fail_after:
            self.calls += 1
            raise EmbeddingError(self._code, f"fake {self._code}")  # type: ignore[arg-type]
        return await super().embed(texts)


class _Harness:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        embedding: FakeEmbeddingProvider | None = None,
        settings_overrides: dict[str, object] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.storage = FakeStorage()
        self.queue = RecordingQueue()
        self.embedding = embedding or FakeEmbeddingProvider()
        self.settings = settings.model_copy(update=settings_overrides or {})
        self.service = IngestionService(
            session_factory=session_factory,
            storage=self.storage,
            embedding=self.embedding,
            task_queue=self.queue,
            settings=self.settings,
        )

    def swap_embedding(self, embedding: FakeEmbeddingProvider) -> None:
        """換掉 embedding adapter(重建 service 而非改 private 欄位)。"""
        self.embedding = embedding
        self.service = IngestionService(
            session_factory=self.session_factory,
            storage=self.storage,
            embedding=embedding,
            task_queue=self.queue,
            settings=self.settings,
        )

    async def status(self, document_id: UUID) -> str:
        async with self.session_factory() as session:
            doc = await session.get(Document, document_id)
            assert doc is not None
            return doc.status

    async def error(self, document_id: UUID) -> str | None:
        async with self.session_factory() as session:
            doc = await session.get(Document, document_id)
            assert doc is not None
            return doc.error

    async def chunks(self, document_id: UUID) -> list[DocumentChunk]:
        async with self.session_factory() as session:
            rows = await session.execute(
                select(DocumentChunk)
                .where(DocumentChunk.document_id == document_id)
                .order_by(DocumentChunk.seq)
            )
            return list(rows.scalars().all())

    async def jobs(self, document_id: UUID) -> dict[str, IngestionJob]:
        async with self.session_factory() as session:
            rows = await session.execute(
                select(IngestionJob).where(IngestionJob.document_id == document_id)
            )
            return {job.stage: job for job in rows.scalars().all()}

    async def run_all(self, document_id: UUID) -> None:
        await self.service.parse(document_id)
        await self.service.chunk(document_id)
        await self.service.embed(document_id)


async def _seed(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    mime: str = "text/plain",
    filename: str = "a.txt",
    status: str = "pending",
    email: str = "p@example.com",
    content: bytes = _TXT,
) -> UUID:
    async with session_factory() as session:
        user = User(email=email, password_hash="x")
        session.add(user)
        await session.flush()
        source = KnowledgeSource(owner_id=user.id, name="我的上傳", type="upload")
        session.add(source)
        await session.flush()
        doc = Document(
            source_id=source.id,
            uploaded_by=user.id,
            filename=filename,
            mime=mime,
            size_bytes=len(content),
            storage_key="documents/seed/original.txt",
            checksum=uuid4().hex * 2,
            status=status,
        )
        session.add(doc)
        await session.flush()
        await session.commit()
        return doc.id


async def _prepared(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    content: bytes = _TXT,
    settings_overrides: dict[str, object] | None = None,
    **kwargs: object,
) -> tuple[_Harness, UUID]:
    """建立文件並把原檔放進 fake storage。"""
    doc_id = await _seed(session_factory, content=content, **kwargs)  # type: ignore[arg-type]
    harness = _Harness(session_factory, settings_overrides=settings_overrides)
    async with session_factory() as session:
        doc = await session.get(Document, doc_id)
        assert doc is not None
        harness.storage.objects[doc.storage_key] = content
    return harness, doc_id


# --- 正常全流程(§12.2)------------------------------------------------------

async def test_full_pipeline_pending_to_ready(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    harness, doc_id = await _prepared(session_factory)

    await harness.service.parse(doc_id)
    assert await harness.status(doc_id) == "chunking"
    assert harness.queue.chunk == [doc_id]

    await harness.service.chunk(doc_id)
    assert await harness.status(doc_id) == "embedding"
    assert harness.queue.embed == [doc_id]

    await harness.service.embed(doc_id)
    assert await harness.status(doc_id) == "ready"
    assert await harness.error(doc_id) is None


async def test_full_pipeline_writes_chunks_with_embeddings(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    harness, doc_id = await _prepared(session_factory)
    await harness.run_all(doc_id)

    chunks = await harness.chunks(doc_id)
    assert len(chunks) >= 1
    assert [c.seq for c in chunks] == list(range(len(chunks)))
    assert all(c.embedding is not None for c in chunks)
    assert all(c.embedding_version == harness.embedding.version for c in chunks)
    assert all(c.meta["kind"] == "document" for c in chunks)  # R6


async def test_parse_writes_normalized_json_to_storage(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    harness, doc_id = await _prepared(session_factory)
    await harness.service.parse(doc_id)
    assert normalized_key(doc_id) in harness.storage.objects


async def test_all_three_jobs_succeeded(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    harness, doc_id = await _prepared(session_factory)
    await harness.run_all(doc_id)

    jobs = await harness.jobs(doc_id)
    assert set(jobs) == {"parse", "chunk", "embed"}
    assert all(job.status == "succeeded" for job in jobs.values())
    assert all(job.finished_at is not None for job in jobs.values())


# --- claim / 冪等(§12.2)----------------------------------------------------

async def test_claim_blocks_concurrent_reentry(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # 第一次 parse 已把狀態推進到 chunking;第二次投遞同一任務 → claim 0 rows → no-op
    harness, doc_id = await _prepared(session_factory)
    await harness.service.parse(doc_id)
    harness.queue.chunk.clear()

    await harness.service.parse(doc_id)

    assert await harness.status(doc_id) == "chunking"
    assert harness.queue.chunk == []  # NEVER 重複入列下一段


async def test_parse_on_ready_document_is_noop(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    harness, doc_id = await _prepared(session_factory)
    await harness.run_all(doc_id)

    await harness.service.parse(doc_id)  # 遲到的重複投遞
    assert await harness.status(doc_id) == "ready"


async def test_missing_document_is_noop(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    harness = _Harness(session_factory)
    await harness.service.parse(uuid4())  # 文件已刪:claim 0 rows,NEVER 拋出


async def test_chunk_rerun_produces_no_duplicate_chunks(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    harness, doc_id = await _prepared(session_factory)
    await harness.service.parse(doc_id)
    await harness.service.chunk(doc_id)
    first = await harness.chunks(doc_id)

    # 手動退回 chunking 再跑一次(模擬 Celery 重複投遞)
    async with session_factory() as session:
        doc = await session.get(Document, doc_id)
        assert doc is not None
        doc.status = "chunking"
        await session.commit()
    await harness.service.chunk(doc_id)

    second = await harness.chunks(doc_id)
    assert len(second) == len(first)
    assert [c.text for c in second] == [c.text for c in first]


async def test_embed_resumes_from_cursor_without_re_embedding(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    harness, doc_id = await _prepared(session_factory)
    await harness.service.parse(doc_id)
    await harness.service.chunk(doc_id)
    await harness.service.embed(doc_id)
    embedded_once = list(harness.embedding.embedded_texts)

    # 退回 embedding 重跑:所有 chunk 都已有 embedding → 游標為空,不再呼叫 provider
    async with session_factory() as session:
        doc = await session.get(Document, doc_id)
        assert doc is not None
        doc.status = "embedding"
        await session.commit()
    await harness.service.embed(doc_id)

    assert harness.embedding.embedded_texts == embedded_once  # NEVER 重嵌已完成 chunks
    assert await harness.status(doc_id) == "ready"


async def test_embed_partial_progress_survives_failure(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # 多段落 + 小 target_tokens → 切出多塊;batch_size=1 使第一批成功、第二批失敗
    content = "\n\n".join(f"第{i}段測試內容,用於切出多個區塊。" for i in range(6)).encode()
    harness, doc_id = await _prepared(
        session_factory,
        content=content,
        settings_overrides={"chunk_target_tokens": 20, "embedding_batch_size": 1},
    )
    await harness.service.parse(doc_id)
    await harness.service.chunk(doc_id)
    assert len(await harness.chunks(doc_id)) >= 2, "測試前提:需切出多個 chunk"

    harness.swap_embedding(FailingEmbedding("transient", fail_after=1))
    with pytest.raises(TransientStageError):
        await harness.service.embed(doc_id)

    chunks = await harness.chunks(doc_id)
    embedded = [c for c in chunks if c.embedding is not None]
    assert len(embedded) == 1  # 第一批已 commit 落地
    assert len(embedded) < len(chunks)  # 其餘留待重試續傳


# --- 失敗語意(§8.2)---------------------------------------------------------

async def test_parse_error_fails_without_retry(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    harness, doc_id = await _prepared(session_factory, mime="application/pdf", filename="a.pdf")
    # storage 內容是純文字,PDF parser 會拋 ParseError(不可重試)
    await harness.service.parse(doc_id)

    assert await harness.status(doc_id) == "failed"
    assert await harness.error(doc_id) is not None
    assert harness.queue.chunk == []  # NEVER 進入下一段
    jobs = await harness.jobs(doc_id)
    assert jobs["parse"].status == "failed"


async def test_missing_original_file_fails(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    doc_id = await _seed(session_factory)
    harness = _Harness(session_factory)  # storage 全空
    await harness.service.parse(doc_id)

    assert await harness.status(doc_id) == "failed"
    assert await harness.error(doc_id) == "找不到原始檔案"


async def test_storage_io_error_raises_transient_for_retry(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    harness, doc_id = await _prepared(session_factory)
    async with session_factory() as session:
        doc = await session.get(Document, doc_id)
        assert doc is not None
        harness.storage.fail_on_get.add(doc.storage_key)

    with pytest.raises(TransientStageError):
        await harness.service.parse(doc_id)
    # 可重試錯誤 NEVER 直接標 failed(由任務殼決定退避或耗盡)
    assert await harness.status(doc_id) == "parsing"


async def test_chunk_without_normalized_json_fails(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    harness, doc_id = await _prepared(session_factory, status="chunking")
    await harness.service.chunk(doc_id)

    assert await harness.status(doc_id) == "failed"
    error = await harness.error(doc_id)
    assert error is not None and "重新上傳" in error


async def test_embed_transient_error_raises_for_retry(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    harness, doc_id = await _prepared(session_factory)
    await harness.service.parse(doc_id)
    await harness.service.chunk(doc_id)
    harness.swap_embedding(FailingEmbedding("rate_limited"))

    with pytest.raises(TransientStageError):
        await harness.service.embed(doc_id)
    assert await harness.status(doc_id) == "embedding"  # 保持執行中,待退避重試


async def test_embed_permanent_error_fails(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    harness, doc_id = await _prepared(session_factory)
    await harness.service.parse(doc_id)
    await harness.service.chunk(doc_id)
    harness.swap_embedding(FailingEmbedding("auth"))

    await harness.service.embed(doc_id)  # auth 錯誤不可重試 → 直接 failed
    assert await harness.status(doc_id) == "failed"
    jobs = await harness.jobs(doc_id)
    assert jobs["embed"].status == "failed"


# --- ingestion_jobs attempts(§8.2 末)---------------------------------------

async def test_attempts_increments_on_rerun(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    harness, doc_id = await _prepared(session_factory, mime="application/pdf", filename="a.pdf")
    await harness.service.parse(doc_id)  # 失敗(ParseError)
    jobs = await harness.jobs(doc_id)
    assert jobs["parse"].attempts == 1

    await harness.service.parse(doc_id)  # claim 接受 failed → 再跑一次
    jobs = await harness.jobs(doc_id)
    assert jobs["parse"].attempts == 2


async def test_stage_level_retry_resumes_at_failed_stage(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # embed 失敗後,claim 接受 failed → 直接重跑 embed,chunks 不被刪除(v1.2 §8)
    harness, doc_id = await _prepared(session_factory)
    await harness.service.parse(doc_id)
    await harness.service.chunk(doc_id)
    harness.swap_embedding(FailingEmbedding("auth"))
    await harness.service.embed(doc_id)
    assert await harness.status(doc_id) == "failed"
    chunk_ids = [c.id for c in await harness.chunks(doc_id)]

    harness.swap_embedding(FakeEmbeddingProvider())
    await harness.service.embed(doc_id)

    assert await harness.status(doc_id) == "ready"
    assert [c.id for c in await harness.chunks(doc_id)] == chunk_ids  # 同一批 chunks
