"""T1.2 conversations / messages CRUD 測試(API + 測試 DB;PHASE_1 §14 T1.2 測試清單)。"""
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.db.models import Message

pytestmark = pytest.mark.anyio

_PASSWORD = "password123"


async def _auth_headers(client: AsyncClient, email: str) -> dict[str, str]:
    await client.post("/api/auth/register", json={"email": email, "password": _PASSWORD})
    resp = await client.post(
        "/api/auth/login", json={"email": email, "password": _PASSWORD}
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _create_conversation(
    client: AsyncClient, headers: dict[str, str], title: str | None = None
) -> str:
    body: dict[str, object] = {}
    if title is not None:
        body["title"] = title
    resp = await client.post("/api/conversations", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


async def _insert_messages(
    session_factory: async_sessionmaker[AsyncSession], conversation_id: str, count: int
) -> None:
    async with session_factory() as session:
        for i in range(count):
            session.add(
                Message(
                    conversation_id=UUID(conversation_id), role="user", content=f"m{i}"
                )
            )
        await session.commit()


# --- 建立 -------------------------------------------------------------------

async def test_create_defaults_model_alias(client: AsyncClient) -> None:
    headers = await _auth_headers(client, "a@example.com")
    resp = await client.post("/api/conversations", json={}, headers=headers)
    assert resp.status_code == 201
    body = resp.json()
    assert body["model_alias"] == "local-default"  # yaml default_alias(§R R2)
    assert body["channel"] == "web"
    assert body["title"] is None


async def test_create_with_title_and_alias(client: AsyncClient) -> None:
    headers = await _auth_headers(client, "a@example.com")
    resp = await client.post(
        "/api/conversations",
        json={"title": "我的對話", "model_alias": "local-default"},
        headers=headers,
    )
    assert resp.status_code == 201
    assert resp.json()["title"] == "我的對話"


async def test_create_invalid_alias_422(client: AsyncClient) -> None:
    headers = await _auth_headers(client, "a@example.com")
    resp = await client.post(
        "/api/conversations", json={"model_alias": "does-not-exist"}, headers=headers
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


# --- 讀取 / ownership ------------------------------------------------------

async def test_get_owner_ok_others_404(client: AsyncClient) -> None:
    a = await _auth_headers(client, "a@example.com")
    b = await _auth_headers(client, "b@example.com")
    conv_id = await _create_conversation(client, a)

    mine = await client.get(f"/api/conversations/{conv_id}", headers=a)
    assert mine.status_code == 200

    others = await client.get(f"/api/conversations/{conv_id}", headers=b)
    assert others.status_code == 404
    assert others.json()["error"]["code"] == "conversation_not_found"

    missing = await client.get(f"/api/conversations/{uuid4()}", headers=a)
    assert missing.status_code == 404


async def test_unauthenticated_401(client: AsyncClient) -> None:
    resp = await client.get("/api/conversations")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_token"


# --- 更新 -------------------------------------------------------------------

async def test_patch_title(client: AsyncClient) -> None:
    a = await _auth_headers(client, "a@example.com")
    b = await _auth_headers(client, "b@example.com")
    conv_id = await _create_conversation(client, a)

    resp = await client.patch(
        f"/api/conversations/{conv_id}", json={"title": "改名後"}, headers=a
    )
    assert resp.status_code == 200
    assert resp.json()["title"] == "改名後"

    # 他人不可改 → 404
    others = await client.patch(
        f"/api/conversations/{conv_id}", json={"title": "壞人"}, headers=b
    )
    assert others.status_code == 404


async def test_patch_empty_title_422(client: AsyncClient) -> None:
    a = await _auth_headers(client, "a@example.com")
    conv_id = await _create_conversation(client, a)
    resp = await client.patch(
        f"/api/conversations/{conv_id}", json={"title": ""}, headers=a
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


# --- 刪除(級聯)----------------------------------------------------------

async def test_delete_cascades_messages(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    a = await _auth_headers(client, "a@example.com")
    conv_id = await _create_conversation(client, a)
    await _insert_messages(session_factory, conv_id, 3)

    resp = await client.delete(f"/api/conversations/{conv_id}", headers=a)
    assert resp.status_code == 204

    gone = await client.get(f"/api/conversations/{conv_id}", headers=a)
    assert gone.status_code == 404

    async with session_factory() as session:
        remaining = await session.scalar(
            select(func.count())
            .select_from(Message)
            .where(Message.conversation_id == UUID(conv_id))
        )
    assert remaining == 0


# --- 分頁 -------------------------------------------------------------------

async def _collect_all(
    client: AsyncClient, headers: dict[str, str], path: str, page_size: int
) -> list[str]:
    """以 page_size 逐頁抓完,回傳所有 item id(依序)。"""
    ids: list[str] = []
    cursor: str | None = None
    while True:
        url = f"{path}?limit={page_size}"
        if cursor:
            url += f"&cursor={cursor}"
        resp = await client.get(url, headers=headers)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        ids.extend(str(item["id"]) for item in data["items"])
        cursor = data["next_cursor"]
        if cursor is None:
            break
    return ids


async def test_list_conversations_pagination(client: AsyncClient) -> None:
    a = await _auth_headers(client, "a@example.com")
    created = [await _create_conversation(client, a, title=f"c{i}") for i in range(3)]

    # 單頁抓全(limit 大)作為權威序
    full = await _collect_all(client, a, "/api/conversations", page_size=10)
    assert sorted(full) == sorted(created)  # 無遺漏、無多餘

    # 以 page_size=2 逐頁抓,序列必須與單頁一致(keyset 無重複 / 遺漏)
    paged = await _collect_all(client, a, "/api/conversations", page_size=2)
    assert paged == full
    assert len(paged) == 3
    assert len(set(paged)) == 3


async def test_list_messages_pagination(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    a = await _auth_headers(client, "a@example.com")
    conv_id = await _create_conversation(client, a)
    await _insert_messages(session_factory, conv_id, 5)

    path = f"/api/conversations/{conv_id}/messages"
    full = await _collect_all(client, a, path, page_size=10)
    assert len(full) == 5

    paged = await _collect_all(client, a, path, page_size=2)
    assert paged == full
    assert len(set(paged)) == 5


async def test_list_messages_other_user_404(client: AsyncClient) -> None:
    a = await _auth_headers(client, "a@example.com")
    b = await _auth_headers(client, "b@example.com")
    conv_id = await _create_conversation(client, a)
    resp = await client.get(f"/api/conversations/{conv_id}/messages", headers=b)
    assert resp.status_code == 404


async def test_invalid_cursor_422(client: AsyncClient) -> None:
    a = await _auth_headers(client, "a@example.com")
    resp = await client.get("/api/conversations?cursor=@@not-base64@@", headers=a)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_cursor"
