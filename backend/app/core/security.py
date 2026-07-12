"""認證原語:密碼雜湊、JWT、refresh token 工具(PHASE_1 §5.1)。

- 密碼:argon2id(argon2-cffi 預設參數);verify 後 `check_needs_rehash` 為真時
  回傳新雜湊供呼叫端回寫(參數升級)。
- Access token:JWT HS256;claims sub / role / iat / exp / jti(uuid7);驗證 leeway 10s。
- Refresh token:不透明字串 `secrets.token_urlsafe(48)`;DB 只存 sha256 hex,NEVER 存明文。

NEVER 於此模組或呼叫端 log 密碼、token 明文或 JWT_SECRET(§C.5.6)。
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error

from app.core.config import settings
from app.core.errors import InvalidToken
from app.core.ids import new_id

_ph = PasswordHasher()
_JWT_ALG = "HS256"
_JWT_LEEWAY_S = 10


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password_hash: str, password: str) -> tuple[bool, str | None]:
    """回傳 (是否正確, 需回寫的新雜湊或 None)。密碼錯誤時回 (False, None)。"""
    try:
        _ph.verify(password_hash, password)
    except Argon2Error:
        return False, None
    if _ph.check_needs_rehash(password_hash):
        return True, _ph.hash(password)
    return True, None


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    return _ph.hash("timing-equalizer-not-a-real-password")


def verify_password_dummy(password: str) -> None:
    """帳號不存在時仍執行一次雜湊驗證,壓平使用者列舉的時間差(§5.3-2)。"""
    verify_password(_dummy_hash(), password)


def create_access_token(user_id: str, role: str) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": user_id,
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_ttl_min),
        "jti": new_id().hex,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=_JWT_ALG)


def decode_access_token(token: str) -> dict[str, Any]:
    """解碼並驗證 access token;任何無效(過期 / 簽章錯 / 格式錯)一律 raise InvalidToken。"""
    try:
        return jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[_JWT_ALG],
            leeway=_JWT_LEEWAY_S,
        )
    except jwt.PyJWTError as exc:
        raise InvalidToken() from exc


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
