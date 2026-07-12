"""conversations router:CRUD + 訊息列表(PHASE_1 §10.3、T1.2)。

router 只做 schema 綁定、呼叫 service、回應塑形。ownership / 分頁 / alias 驗證皆在下層。
訊息「送出」(POST /messages,SSE)屬 T1.4,本 ticket 不做。
"""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_auth, get_db
from app.api.schemas.conversations import (
    ConversationCreate,
    ConversationOut,
    ConversationPage,
    ConversationUpdate,
    MessageOut,
    MessagePage,
)
from app.application.conversation_service import ConversationService
from app.domain.entities.auth_context import AuthContext

router = APIRouter(prefix="/conversations", tags=["conversations"])

AuthDep = Annotated[AuthContext, Depends(get_auth)]
SessionDep = Annotated[AsyncSession, Depends(get_db)]
LimitDep = Annotated[int, Query(ge=1, le=100)]


@router.get("")
async def list_conversations(
    auth: AuthDep,
    session: SessionDep,
    limit: LimitDep = 20,
    cursor: str | None = None,
) -> ConversationPage:
    items, next_cursor = await ConversationService(session).list_conversations(
        auth, limit=limit, cursor=cursor
    )
    return ConversationPage(
        items=[ConversationOut.model_validate(c) for c in items],
        next_cursor=next_cursor,
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_conversation(
    body: ConversationCreate, auth: AuthDep, session: SessionDep
) -> ConversationOut:
    conv = await ConversationService(session).create(
        auth, title=body.title, model_alias=body.model_alias
    )
    return ConversationOut.model_validate(conv)


@router.get("/{conversation_id}")
async def get_conversation(
    conversation_id: UUID, auth: AuthDep, session: SessionDep
) -> ConversationOut:
    conv = await ConversationService(session).get(auth, conversation_id)
    return ConversationOut.model_validate(conv)


@router.patch("/{conversation_id}")
async def update_conversation(
    conversation_id: UUID, body: ConversationUpdate, auth: AuthDep, session: SessionDep
) -> ConversationOut:
    conv = await ConversationService(session).rename(auth, conversation_id, body.title)
    return ConversationOut.model_validate(conv)


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: UUID, auth: AuthDep, session: SessionDep
) -> None:
    await ConversationService(session).delete(auth, conversation_id)


@router.get("/{conversation_id}/messages")
async def list_messages(
    conversation_id: UUID,
    auth: AuthDep,
    session: SessionDep,
    limit: LimitDep = 20,
    cursor: str | None = None,
) -> MessagePage:
    items, next_cursor = await ConversationService(session).list_messages(
        auth, conversation_id, limit=limit, cursor=cursor
    )
    return MessagePage(
        items=[MessageOut.model_validate(m) for m in items],
        next_cursor=next_cursor,
    )
