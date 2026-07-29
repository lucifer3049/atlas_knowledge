"""documents I/O schema(interface 層;PHASE_2 §11.1)。"""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source_id: UUID
    filename: str
    mime: str
    size_bytes: int
    status: str
    error: str | None
    created_at: datetime
    updated_at: datetime


class DocumentUploadResponse(DocumentOut):
    deduplicated: bool = False  # D8:重複上傳不是錯誤,回既有 document


class DocumentPage(BaseModel):
    items: list[DocumentOut]
    next_cursor: str | None


class CitationOut(BaseModel):
    # SSE `citations` 事件與 messages API 共用同一形狀(§10.3、§11.1;R8 亦以此為 CitationRef)。
    # chunk_id / document_id 為軟引用(D7),來源文件刪除後歷史引用仍可讀,故可為 null。
    model_config = ConfigDict(from_attributes=True)

    rank: int
    chunk_id: UUID | None
    document_id: UUID | None
    filename: str
    snippet: str
    score: float
