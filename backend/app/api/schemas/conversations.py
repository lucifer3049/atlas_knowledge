"""conversations / messages I/O schema(interface 層;PHASE_1 §10.2、PHASE_2 §10.1)。"""
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.api.schemas.documents import CitationOut


class ConversationCreate(BaseModel):
    title: str | None = Field(None, max_length=200)
    model_alias: str | None = None  # None → config/models.yaml 的 default alias(§R R2)


class ConversationUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str | None
    channel: str
    model_alias: str
    created_at: datetime
    updated_at: datetime


class ConversationPage(BaseModel):
    items: list[ConversationOut]
    next_cursor: str | None


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    role: str
    content: str
    content_meta: dict[str, Any]
    tokens_in: int | None
    tokens_out: int | None
    latency_ms: int | None
    created_at: datetime
    citations: list[CitationOut] = []  # 歷史訊息的引用快照(D7);純聊天為空


class MessagePage(BaseModel):
    items: list[MessageOut]
    next_cursor: str | None


class KnowledgeScope(BaseModel):
    """檢索範圍(§10.1;schema 依 R5 直接採 phase-3 §3.3 最終形)。

    三個欄位皆 optional,`None` 與空陣列同義 = 不限(前端送 `[]` 不得炸)。
    **P2 僅 `source_ids` 有實際過濾行為**——當時只有 document 類知識;`types` /
    `law_codes` 的過濾於 P3 接上,NEVER 在 P2 提前實作。
    """

    types: list[Literal["document", "law", "erp_dataset"]] | None = None
    law_codes: list[str] | None = None
    source_ids: list[UUID] | None = None


class ChatSendRequest(BaseModel):
    content: str = Field(min_length=1, max_length=8000)
    client_message_id: UUID | None = None
    knowledge_scope: KnowledgeScope | None = None  # None = 純聊天(P1 行為不變)
