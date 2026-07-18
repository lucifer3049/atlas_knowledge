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
