"""T1.1 auth 測試(API + 測試 DB;PHASE_1 §14 T1.1 測試清單)。"""
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.ids import new_id
from app.core.security import hash_refresh_token
from app.infrastructure.db.models import RefreshToken, User

pytestmark = pytest.mark.anyio

_PASSWORD = "password123"


def _only_cookie(client: AsyncClient, name: str, value: str) -> None:
    """把 jar 清成只剩指定 cookie(用於重放特定 refresh token,避免 jar 干擾)。"""
    client.cookies.clear()
    client.cookies.set(name, value)


async def _register(
    client: AsyncClient, email: str = "user@example.com", password: str = _PASSWORD
) -> None:
    resp = await client.post(
        "/api/auth/register", json={"email": email, "password": password}
    )
    assert resp.status_code == 201, resp.text


# --- 註冊 -------------------------------------------------------------------

async def test_register_success(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/auth/register", json={"email": "user@example.com", "password": _PASSWORD}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["email"] == "user@example.com"
    assert body["role"] == "user"
    assert "id" in body and "created_at" in body
    assert "password" not in body and "password_hash" not in body


async def test_register_lowercases_email(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/auth/register", json={"email": "User@Example.COM", "password": _PASSWORD}
    )
    assert resp.status_code == 201
    assert resp.json()["email"] == "user@example.com"


async def test_register_duplicate_email_conflict(client: AsyncClient) -> None:
    await _register(client, email="dup@example.com")
    resp = await client.post(
        "/api/auth/register", json={"email": "DUP@example.com", "password": _PASSWORD}
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "email_already_registered"


async def test_register_weak_password_422(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/auth/register", json={"email": "weak@example.com", "password": "short"}
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


# --- 登入 -------------------------------------------------------------------

async def test_login_success_sets_cookie_and_access_works(client: AsyncClient) -> None:
    await _register(client)
    resp = await client.post(
        "/api/auth/login", json={"email": "user@example.com", "password": _PASSWORD}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["token_type"] == "bearer"
    assert data["expires_in"] == settings.access_token_ttl_min * 60
    assert data["user"]["email"] == "user@example.com"
    assert "rt" in resp.cookies

    access = data["access_token"]
    me = await client.get("/api/me", headers={"Authorization": f"Bearer {access}"})
    assert me.status_code == 200
    assert me.json()["email"] == "user@example.com"


async def test_login_wrong_password_and_unknown_user_identical(client: AsyncClient) -> None:
    await _register(client)
    wrong = await client.post(
        "/api/auth/login", json={"email": "user@example.com", "password": "wrongpass1"}
    )
    unknown = await client.post(
        "/api/auth/login", json={"email": "nobody@example.com", "password": "whatever12"}
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["error"]["code"] == "invalid_credentials"
    # 帳號不存在與密碼錯誤回應完全相同(除 trace_id 外)
    assert wrong.json()["error"]["code"] == unknown.json()["error"]["code"]
    assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"]


async def test_login_case_insensitive_email(client: AsyncClient) -> None:
    await _register(client, email="Case@Example.com")
    resp = await client.post(
        "/api/auth/login", json={"email": "case@example.COM", "password": _PASSWORD}
    )
    assert resp.status_code == 200


async def test_login_inactive_user_403(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _register(client, email="inactive@example.com")
    async with session_factory() as session:
        await session.execute(
            update(User).where(User.email == "inactive@example.com").values(is_active=False)
        )
        await session.commit()
    resp = await client.post(
        "/api/auth/login", json={"email": "inactive@example.com", "password": _PASSWORD}
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "user_inactive"


# --- refresh 輪替 / 竊用偵測 -------------------------------------------------

async def test_refresh_rotates_tokens(client: AsyncClient) -> None:
    await _register(client)
    login = await client.post(
        "/api/auth/login", json={"email": "user@example.com", "password": _PASSWORD}
    )
    old_rt = login.cookies["rt"]

    resp = await client.post("/api/auth/refresh")
    assert resp.status_code == 200
    new_rt = resp.cookies["rt"]
    assert new_rt != old_rt

    me = await client.get(
        "/api/me", headers={"Authorization": f"Bearer {resp.json()['access_token']}"}
    )
    assert me.status_code == 200


async def test_replayed_old_refresh_after_grace_revokes_family(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _register(client)
    login = await client.post(
        "/api/auth/login", json={"email": "user@example.com", "password": _PASSWORD}
    )
    old_rt = login.cookies["rt"]
    rotated = await client.post("/api/auth/refresh")
    new_rt = rotated.cookies["rt"]

    # 舊 token 撤銷時間回撥至寬限窗外 → 重放視為竊用
    async with session_factory() as session:
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.token_hash == hash_refresh_token(old_rt))
            .values(revoked_at=datetime.now(UTC) - timedelta(minutes=2))
        )
        await session.commit()

    _only_cookie(client, "rt", old_rt)
    replay = await client.post("/api/auth/refresh")
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "invalid_refresh_token"

    # family 已撤銷:先前有效的新 token 一併失效
    _only_cookie(client, "rt", new_rt)
    after = await client.post("/api/auth/refresh")
    assert after.status_code == 401


async def test_immediate_replay_within_grace_keeps_family(client: AsyncClient) -> None:
    await _register(client)
    login = await client.post(
        "/api/auth/login", json={"email": "user@example.com", "password": _PASSWORD}
    )
    old_rt = login.cookies["rt"]
    rotated = await client.post("/api/auth/refresh")
    new_rt = rotated.cookies["rt"]

    # 寬限窗內重放舊 token → 401 但 NEVER 撤 family
    _only_cookie(client, "rt", old_rt)
    replay = await client.post("/api/auth/refresh")
    assert replay.status_code == 401

    _only_cookie(client, "rt", new_rt)
    ok = await client.post("/api/auth/refresh")
    assert ok.status_code == 200


async def test_logout_revokes_refresh(client: AsyncClient) -> None:
    await _register(client)
    login = await client.post(
        "/api/auth/login", json={"email": "user@example.com", "password": _PASSWORD}
    )
    old_rt = login.cookies["rt"]

    logout = await client.post("/api/auth/logout")
    assert logout.status_code == 204

    _only_cookie(client, "rt", old_rt)
    resp = await client.post("/api/auth/refresh")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_refresh_token"


# --- /me 與 token ----------------------------------------------------------

async def test_me_without_token_401(client: AsyncClient) -> None:
    resp = await client.get("/api/me")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_token"


async def test_me_expired_token_401(client: AsyncClient) -> None:
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": str(new_id()),
            "role": "user",
            "iat": now - timedelta(hours=2),
            "exp": now - timedelta(hours=1),
            "jti": new_id().hex,
        },
        settings.jwt_secret,
        algorithm="HS256",
    )
    resp = await client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_token"
