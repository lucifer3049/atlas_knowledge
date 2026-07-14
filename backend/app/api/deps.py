"""DI 組裝(interface 層唯一知道 adapter 的地方;PHASE_1 §11)。"""
from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.chat_orchestrator import ChatOrchestrator
from app.core.config import Settings, settings
from app.core.errors import InvalidToken
from app.core.security import decode_access_token
from app.domain.entities.auth_context import AuthContext
from app.domain.ports.llm import LLMProvider
from app.domain.ports.task_queue import TaskQueue
from app.infrastructure.tasks.celery_queue import CeleryTaskQueue


def get_settings() -> Settings:
    return settings


def get_llm(request: Request) -> LLMProvider:
    llm: LLMProvider = request.app.state.llm
    return llm


def get_task_queue() -> TaskQueue:
    return CeleryTaskQueue()


def get_orchestrator(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    llm: Annotated[LLMProvider, Depends(get_llm)],
    task_queue: Annotated[TaskQueue, Depends(get_task_queue)],
) -> ChatOrchestrator:
    return ChatOrchestrator(
        session_factory=request.app.state.session_factory,
        llm=llm,
        settings=settings,
        task_queue=task_queue,
    )


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_factory() as session:
        yield session


_bearer = HTTPBearer(auto_error=False)


async def get_auth(
    request: Request,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> AuthContext:
    if creds is None:
        raise InvalidToken()
    payload = decode_access_token(creds.credentials)
    return AuthContext(
        user_id=UUID(payload["sub"]),
        role=payload["role"],
        trace_id=request.state.request_id,
    )
