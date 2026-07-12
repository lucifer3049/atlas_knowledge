"""conversations / messages I/O schema(interface 層;PHASE_1 §10.2)。"""
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


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


class MessagePage(BaseModel):
    items: list[MessageOut]
    next_cursor: str | None
