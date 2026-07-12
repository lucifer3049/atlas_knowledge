"""auth router:register / login / refresh / logout(PHASE_1 §10.3)。

router 只做:schema 綁定、呼叫 AuthService、cookie 設定、回應塑形。NEVER 有業務邏輯。
refresh cookie 名 `rt`、HttpOnly、SameSite=Lax、Path=/api/auth(§5.1)。同源 + SameSite=Lax
已足以防 CSRF,NEVER 另做 CSRF token。
"""
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_settings
from app.api.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserOut
from app.application.auth_service import AuthService, IssuedTokens
from app.core.config import Settings
from app.core.errors import InvalidRefreshToken

router = APIRouter(prefix="/auth", tags=["auth"])

_REFRESH_COOKIE = "rt"
_REFRESH_PATH = "/api/auth"

SessionDep = Annotated[AsyncSession, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def _set_refresh_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        key=_REFRESH_COOKIE,
        value=token,
        max_age=settings.refresh_token_ttl_days * 86400,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path=_REFRESH_PATH,
    )


def _token_response(issued: IssuedTokens) -> TokenResponse:
    return TokenResponse(
        access_token=issued.access_token,
        expires_in=issued.expires_in,
        user=UserOut.model_validate(issued.user),
    )


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, session: SessionDep) -> UserOut:
    user = await AuthService(session).register(body.email, body.password)
    return UserOut.model_validate(user)


@router.post("/login")
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
) -> TokenResponse:
    issued = await AuthService(session).login(
        body.email, body.password, request.headers.get("user-agent")
    )
    _set_refresh_cookie(response, issued.refresh_token, settings)
    return _token_response(issued)


@router.post("/refresh")
async def refresh(
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
) -> TokenResponse:
    token = request.cookies.get(_REFRESH_COOKIE)
    if not token:
        raise InvalidRefreshToken()
    issued = await AuthService(session).refresh(token, request.headers.get("user-agent"))
    _set_refresh_cookie(response, issued.refresh_token, settings)
    return _token_response(issued)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response, session: SessionDep) -> None:
    await AuthService(session).logout(request.cookies.get(_REFRESH_COOKIE))
    response.delete_cookie(_REFRESH_COOKIE, path=_REFRESH_PATH)
