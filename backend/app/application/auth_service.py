"""AuthService:註冊 / 登入 / refresh 輪替 / 登出的業務邏輯(PHASE_1 §5)。

分層:router 只做序列化與 cookie,SQL 只在 repository,竊用偵測狀態機在此。
交易邊界:每個對外方法自行 commit(短交易)。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import (
    EmailAlreadyRegistered,
    InvalidCredentials,
    InvalidRefreshToken,
    UserInactive,
)
from app.core.ids import new_id
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
    verify_password_dummy,
)
from app.infrastructure.db.models import User
from app.infrastructure.db.repositories.refresh_tokens import RefreshTokenRepository
from app.infrastructure.db.repositories.users import UserRepository

# refresh 併發競態寬限窗(§PHASE_1 v1.2):撤銷後 60 秒內且已 replaced 的重放視為多分頁
# 競態(回 401 但 NEVER 撤 family);超過才視為重放竊用。
_ROTATION_GRACE = timedelta(seconds=60)


@dataclass(frozen=True)
class IssuedTokens:
    user: User
    access_token: str
    refresh_token: str  # 明文,僅回傳給 router 寫入 cookie,NEVER 落庫
    expires_in: int  # access token 秒數


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._users = UserRepository(session)
        self._refresh = RefreshTokenRepository(session)

    async def register(self, email: str, password: str) -> User:
        email = _normalize_email(email)
        if await self._users.get_by_email(email) is not None:
            raise EmailAlreadyRegistered()
        try:
            user = await self._users.create(email=email, password_hash=hash_password(password))
            await self._session.commit()
        except IntegrityError as exc:  # 併發競態:唯一索引兜底
            await self._session.rollback()
            raise EmailAlreadyRegistered() from exc
        return user

    async def login(self, email: str, password: str, user_agent: str | None) -> IssuedTokens:
        email = _normalize_email(email)
        user = await self._users.get_by_email(email)
        if user is None:
            verify_password_dummy(password)  # 壓平時間差
            raise InvalidCredentials()
        ok, new_hash = verify_password(user.password_hash, password)
        if not ok:
            raise InvalidCredentials()
        if not user.is_active:
            raise UserInactive()
        if new_hash is not None:
            user.password_hash = new_hash
        issued, _ = await self._issue(user, family_id=new_id(), user_agent=user_agent)
        await self._session.commit()
        return issued

    async def refresh(self, refresh_token: str, user_agent: str | None) -> IssuedTokens:
        now = _now()
        row = await self._refresh.get_by_hash(hash_refresh_token(refresh_token))
        if row is None or row.expires_at < now:
            raise InvalidRefreshToken()
        if row.revoked_at is not None:
            # 已撤銷 token 被重放。寬限窗內的 replaced token 視為併發競態,不撤 family。
            if row.replaced_by is not None and (now - row.revoked_at) <= _ROTATION_GRACE:
                raise InvalidRefreshToken()
            await self._refresh.revoke_family(row.family_id, now=now)
            await self._session.commit()
            raise InvalidRefreshToken()
        user = await self._users.get_by_id(row.user_id)
        if user is None or not user.is_active:
            raise InvalidRefreshToken()
        issued, new_row_id = await self._issue(
            user, family_id=row.family_id, user_agent=user_agent, now=now
        )
        # 與發新 token 同一交易:撤銷舊 token 並指向新 token。
        row.revoked_at = now
        row.replaced_by = new_row_id
        await self._session.commit()
        return issued

    async def logout(self, refresh_token: str | None) -> None:
        if refresh_token:
            row = await self._refresh.get_by_hash(hash_refresh_token(refresh_token))
            if row is not None:
                await self._refresh.revoke_family(row.family_id, now=_now())
        await self._session.commit()

    async def _issue(
        self,
        user: User,
        *,
        family_id: UUID,
        user_agent: str | None,
        now: datetime | None = None,
    ) -> tuple[IssuedTokens, UUID]:
        """發一組 access + refresh;回傳 (tokens, 新 refresh 列 id)。NEVER 自行 commit。"""
        now = now or _now()
        refresh_plain = generate_refresh_token()
        row = await self._refresh.create(
            user_id=user.id,
            family_id=family_id,
            token_hash=hash_refresh_token(refresh_plain),
            expires_at=now + timedelta(days=settings.refresh_token_ttl_days),
            user_agent=user_agent,
        )
        access = create_access_token(str(user.id), user.role)
        issued = IssuedTokens(
            user=user,
            access_token=access,
            refresh_token=refresh_plain,
            expires_in=settings.access_token_ttl_min * 60,
        )
        return issued, row.id


def _now() -> datetime:
    return datetime.now(UTC)


def _normalize_email(email: str) -> str:
    return email.strip().lower()
