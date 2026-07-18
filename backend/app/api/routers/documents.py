"""documents router:上傳 / 列表 / 詳情 / 重試 / 刪除(PHASE_2 §11.2、T2.1)。

router 只做 multipart 讀取(含大小上限把關)、呼叫 service、回應塑形;
型別白名單、dedup、storage、入列皆在 DocumentService。
"""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_auth, get_db, get_settings, get_storage, get_task_queue
from app.api.schemas.documents import DocumentOut, DocumentPage, DocumentUploadResponse
from app.application.document_service import DocumentService
from app.core.config import Settings
from app.core.errors import FileTooLarge
from app.domain.entities.auth_context import AuthContext
from app.domain.ports.storage import ObjectStorage
from app.domain.ports.task_queue import TaskQueue

router = APIRouter(prefix="/documents", tags=["documents"])

AuthDep = Annotated[AuthContext, Depends(get_auth)]
SessionDep = Annotated[AsyncSession, Depends(get_db)]
StorageDep = Annotated[ObjectStorage, Depends(get_storage)]
QueueDep = Annotated[TaskQueue, Depends(get_task_queue)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
LimitDep = Annotated[int, Query(ge=1, le=100)]

_READ_CHUNK_BYTES = 1024 * 1024


def _service(session: AsyncSession, storage: ObjectStorage, queue: TaskQueue) -> DocumentService:
    return DocumentService(session, storage=storage, task_queue=queue)


async def _read_within_limit(file: UploadFile, max_bytes: int) -> bytes:
    """分塊讀取 spool 檔;超過上限立即中止,NEVER 把超大檔整個載入記憶體。"""
    buffer = bytearray()
    while chunk := await file.read(_READ_CHUNK_BYTES):
        buffer.extend(chunk)
        if len(buffer) > max_bytes:
            raise FileTooLarge()
    return bytes(buffer)


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def upload_document(
    auth: AuthDep,
    session: SessionDep,
    storage: StorageDep,
    queue: QueueDep,
    settings: SettingsDep,
    response: Response,
    file: Annotated[UploadFile, File()],
    source_id: Annotated[UUID | None, Form()] = None,
) -> DocumentUploadResponse:
    data = await _read_within_limit(file, settings.max_upload_mb * 1024 * 1024)
    doc, deduplicated = await _service(session, storage, queue).upload(
        auth,
        filename=file.filename or "unnamed",
        content_type=file.content_type,
        data=data,
        source_id=source_id,
    )
    if deduplicated:  # D8:重複上傳不是錯誤,回既有 document
        response.status_code = status.HTTP_200_OK
    out = DocumentUploadResponse.model_validate(doc)
    out.deduplicated = deduplicated
    return out


@router.get("")
async def list_documents(
    auth: AuthDep,
    session: SessionDep,
    storage: StorageDep,
    queue: QueueDep,
    limit: LimitDep = 20,
    cursor: str | None = None,
    doc_status: Annotated[str | None, Query(alias="status")] = None,
) -> DocumentPage:
    items, next_cursor = await _service(session, storage, queue).list_documents(
        auth, limit=limit, cursor=cursor, status=doc_status
    )
    return DocumentPage(
        items=[DocumentOut.model_validate(d) for d in items], next_cursor=next_cursor
    )


@router.get("/{document_id}")
async def get_document(
    document_id: UUID, auth: AuthDep, session: SessionDep, storage: StorageDep, queue: QueueDep
) -> DocumentOut:
    doc = await _service(session, storage, queue).get(auth, document_id)
    return DocumentOut.model_validate(doc)


@router.post("/{document_id}/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry_document(
    document_id: UUID, auth: AuthDep, session: SessionDep, storage: StorageDep, queue: QueueDep
) -> DocumentOut:
    doc = await _service(session, storage, queue).retry(auth, document_id)
    return DocumentOut.model_validate(doc)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: UUID, auth: AuthDep, session: SessionDep, storage: StorageDep, queue: QueueDep
) -> None:
    await _service(session, storage, queue).delete(auth, document_id)
