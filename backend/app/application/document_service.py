"""DocumentService:文件上傳 / 查詢 / 重試 / 刪除的 use case(T2.1;PHASE_2 §11)。

分層:router 只做 multipart 讀取與序列化,SQL 只在 repository,ownership 過濾在 repository。
上傳流程(§11.2):型別檢查 → sha256 → dedup(D8)→ storage.put → insert(pending)
→ enqueue parse_document → 202;重複回 200 + deduplicated(既有為 failed 則重置重跑)。
"""
import hashlib
from datetime import datetime
from pathlib import PurePosixPath
from uuid import UUID

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    DocumentNotFound,
    DocumentNotRetryable,
    SourceNotFound,
    UnsupportedMediaType,
)
from app.core.ids import new_id
from app.core.pagination import decode_cursor, paginate
from app.domain.entities.auth_context import AuthContext
from app.domain.ports.storage import ObjectStorage
from app.domain.ports.task_queue import TaskQueue
from app.infrastructure.db.models import Document
from app.infrastructure.db.repositories.documents import DocumentRepository
from app.infrastructure.db.repositories.knowledge_sources import KnowledgeSourceRepository

_logger = structlog.get_logger()

# canonical mime 由副檔名白名單推導;client 送的 content-type 僅 sanity check
# (v1.2 parser 補遺)。`documents.mime` 一律存 canonical 值。
_EXT_TO_MIME: dict[str, str] = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".html": "text/html",
    ".htm": "text/html",
}
# 各 canonical mime 可接受的 client content-type;application/octet-stream 一律放行。
_ACCEPTED_CONTENT_TYPES: dict[str, set[str]] = {
    "application/pdf": {"application/pdf"},
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/zip",
    },
    "text/plain": {"text/plain"},
    "text/markdown": {"text/markdown", "text/x-markdown", "text/plain"},
    "text/html": {"text/html", "text/plain"},
}
_ALWAYS_ACCEPTED_CONTENT_TYPES = {"", "application/octet-stream"}
_PDF_MAGIC = b"%PDF"

_ENQUEUE_FAILED_ERROR = "解析任務入列失敗,請重試"


def _doc_key(doc: Document) -> tuple[datetime, UUID]:
    return doc.created_at, doc.id


def canonical_mime(filename: str, content_type: str | None) -> str:
    """依副檔名推導 canonical mime;不在白名單或 content-type 明顯不符 → 415。"""
    suffix = PurePosixPath(filename).suffix.lower()
    mime = _EXT_TO_MIME.get(suffix)
    if mime is None:
        raise UnsupportedMediaType()
    declared = (content_type or "").split(";")[0].strip().lower()
    if declared in _ALWAYS_ACCEPTED_CONTENT_TYPES:
        return mime
    if declared not in _ACCEPTED_CONTENT_TYPES[mime]:
        raise UnsupportedMediaType()
    return mime


def _check_magic(mime: str, data: bytes) -> None:
    # PDF 另做 magic 檢查(§11.2):副檔名可偽造,避免明顯壞檔進 pipeline 才失敗。
    if mime == "application/pdf" and not data.startswith(_PDF_MAGIC):
        raise UnsupportedMediaType()


def storage_prefix(document_id: UUID) -> str:
    return f"documents/{document_id}/"


class DocumentService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        storage: ObjectStorage,
        task_queue: TaskQueue,
    ) -> None:
        self._session = session
        self._storage = storage
        self._task_queue = task_queue
        self._documents = DocumentRepository(session)
        self._sources = KnowledgeSourceRepository(session)

    # --- 上傳 ---------------------------------------------------------------

    async def upload(
        self,
        ctx: AuthContext,
        *,
        filename: str,
        content_type: str | None,
        data: bytes,
        source_id: UUID | None,
    ) -> tuple[Document, bool]:
        """回傳 (document, deduplicated)。"""
        safe_name = PurePosixPath(filename.replace("\\", "/")).name[:255]
        mime = canonical_mime(safe_name, content_type)
        _check_magic(mime, data)
        checksum = hashlib.sha256(data).hexdigest()

        existing = await self._documents.get_by_checksum(ctx.user_id, checksum)
        if existing is not None:
            # dedup 跨 source 時回既有 document(掛原 source),回應如實帶原 source_id。
            await self._reset_if_failed(existing)
            return existing, True

        resolved_source_id = await self._resolve_source_id(ctx, source_id)
        document_id = new_id()
        key = f"{storage_prefix(document_id)}original{PurePosixPath(safe_name).suffix.lower()}"
        await self._storage.put(key, data)

        try:
            doc = await self._documents.create(
                document_id=document_id,
                source_id=resolved_source_id,
                uploaded_by=ctx.user_id,
                filename=safe_name,
                mime=mime,
                size_bytes=len(data),
                storage_key=key,
                checksum=checksum,
            )
            await self._session.commit()
        except IntegrityError:
            # 併發上傳同檔撞 ux_documents_owner_checksum(§11 補遺):回既有 document。
            await self._session.rollback()
            await self._storage.delete_prefix(storage_prefix(document_id))
            duplicate = await self._documents.get_by_checksum(ctx.user_id, checksum)
            if duplicate is None:  # pragma: no cover - 唯一鍵衝突必然查得到
                raise
            await self._reset_if_failed(duplicate)
            return duplicate, True

        await self._enqueue_parse(doc)
        return doc, False

    async def _resolve_source_id(self, ctx: AuthContext, source_id: UUID | None) -> UUID:
        if source_id is not None:
            source = await self._sources.get_owned(ctx.user_id, source_id)
            if source is None:
                raise SourceNotFound()
            return source.id
        existing = await self._sources.get_default(ctx.user_id)
        if existing is not None:
            return existing.id
        try:
            created = await self._sources.create_default(ctx.user_id)
            await self._session.commit()
        except IntegrityError:
            # partial unique index 擋下併發懶建 → 重查既有列(§11 補遺)。
            await self._session.rollback()
            fallback = await self._sources.get_default(ctx.user_id)
            if fallback is None:  # pragma: no cover - 唯一鍵衝突必然查得到
                raise
            return fallback.id
        return created.id

    async def _reset_if_failed(self, doc: Document) -> None:
        """既有為 failed 則重置重跑(D8);其餘狀態不動,NEVER 重跑 pipeline。"""
        if doc.status != "failed":
            return
        doc.status = "pending"
        doc.error = None
        await self._session.commit()
        await self._session.refresh(doc)
        await self._enqueue_parse(doc)

    async def _enqueue_parse(self, doc: Document) -> None:
        try:
            self._task_queue.enqueue_parse_document(doc.id)
        except Exception:
            # 入列失敗 = 文件不會被處理;標為 failed 讓使用者可經 retry API 復原,
            # NEVER 讓文件永久卡在 pending。
            _logger.warning("parse_enqueue_failed", document_id=str(doc.id), exc_info=True)
            doc.status = "failed"
            doc.error = _ENQUEUE_FAILED_ERROR
            await self._session.commit()
            await self._session.refresh(doc)

    # --- 查詢 / 重試 / 刪除 -------------------------------------------------

    async def get(self, ctx: AuthContext, document_id: UUID) -> Document:
        doc = await self._documents.get_owned(ctx.user_id, document_id)
        if doc is None:
            raise DocumentNotFound()
        return doc

    async def list_documents(
        self, ctx: AuthContext, *, limit: int, cursor: str | None, status: str | None
    ) -> tuple[list[Document], str | None]:
        keyset = decode_cursor(cursor) if cursor else None
        rows = await self._documents.list_page(
            ctx.user_id, limit=limit, cursor=keyset, status=status
        )
        return paginate(rows, limit, _doc_key)

    async def retry(self, ctx: AuthContext, document_id: UUID) -> Document:
        doc = await self.get(ctx, document_id)
        if doc.status != "failed":
            raise DocumentNotRetryable()
        doc.status = "pending"
        doc.error = None
        await self._session.commit()
        await self._session.refresh(doc)
        await self._enqueue_parse(doc)
        return doc

    async def delete(self, ctx: AuthContext, document_id: UUID) -> None:
        doc = await self.get(ctx, document_id)
        prefix = storage_prefix(doc.id)
        await self._documents.delete(doc)
        await self._session.commit()
        # DB 一致性即時、慢 I/O 背景化(D12);清理失敗只留孤兒檔案,不影響回應。
        try:
            self._task_queue.enqueue_purge_document(prefix)
        except Exception:
            _logger.warning("purge_enqueue_failed", storage_prefix=prefix, exc_info=True)
