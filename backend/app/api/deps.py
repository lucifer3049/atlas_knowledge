"""DI 組裝(interface 層唯一知道 adapter 的地方;PHASE_1 §11)。

T1.1 僅需 settings / db session / auth;LLM、orchestrator 等於後續 ticket 加入。
"""
from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, settings
from app.core.errors import InvalidToken
from app.core.security import decode_access_token
from app.domain.entities.auth_context import AuthContext


def get_settings() -> Settings:
    return settings


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
