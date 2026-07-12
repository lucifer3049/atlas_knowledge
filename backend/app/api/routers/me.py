"""me router:GET /api/me — 回傳目前登入者(PHASE_1 §10.3)。"""
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_auth, get_db
from app.api.schemas.auth import UserOut
from app.core.errors import InvalidToken
from app.domain.entities.auth_context import AuthContext
from app.infrastructure.db.repositories.users import UserRepository

router = APIRouter(tags=["me"])


@router.get("/me")
async def me(
    auth: Annotated[AuthContext, Depends(get_auth)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> UserOut:
    user = await UserRepository(session).get_by_id(auth.user_id)
    if user is None:  # token 有效但使用者已不存在
        raise InvalidToken()
    return UserOut.model_validate(user)
