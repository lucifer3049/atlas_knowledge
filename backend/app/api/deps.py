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
from app.core.model_registry import default_alias, resolve
from app.core.security import decode_access_token
from app.domain.entities.auth_context import AuthContext
from app.domain.ports.llm import LLMProvider
from app.infrastructure.llm.openai_compat import OpenAICompatProvider
from app.infrastructure.tasks.noop_queue import NoopTaskQueue


def get_settings() -> Settings:
    return settings


def build_llm(settings: Settings) -> LLMProvider:
    """由 default alias 組 LLM adapter(§R R2)。於 lifespan 建一次掛 app.state,
    連線層(base_url/api_key/timeout)取自 settings;model 名於 orchestrator 依
    conversation.model_alias 解析。多 provider(anthropic/gemini)為 P6 ModelRouter。"""
    resolved = resolve(default_alias())
    if resolved.provider != "openai_compat":
        raise RuntimeError(f"P1 僅支援 openai_compat provider,取得 {resolved.provider!r}")
    return OpenAICompatProvider(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        timeout_s=settings.llm_timeout_s,
    )


def get_llm(request: Request) -> LLMProvider:
    llm: LLMProvider = request.app.state.llm
    return llm


def get_orchestrator(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    llm: Annotated[LLMProvider, Depends(get_llm)],
) -> ChatOrchestrator:
    return ChatOrchestrator(
        session_factory=request.app.state.session_factory,
        llm=llm,
        settings=settings,
        task_queue=NoopTaskQueue(),
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
