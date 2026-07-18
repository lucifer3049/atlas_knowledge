"""T2.1 documents API 測試(API + 測試 DB;PHASE_2 §14 T2.1 測試清單)。

涵蓋:dedup 三情境(D8)、型別/大小把關、懶建預設來源、ownership、retry 狀態機、
DELETE 後 404 與背景 purge 入列。任務入列一律以 fake 取代(NEVER 碰 broker,§C.5.7)。
"""
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import get_task_queue
from app.core.config import settings
from app.infrastructure.db.models import Document, KnowledgeSource
from app.main import app

pytestmark = pytest.mark.anyio

_PASSWORD = "password123"
_PDF_BYTES = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\ntrailer\n"


class _FakeTaskQueue:
    parsed: list[str] = []
    purged: list[str] = []

    def enqueue_generate_title(self, conversation_id: object) -> None:
        pass

    def enqueue_parse_document(self, document_id: UUID) -> None:
        _FakeTaskQueue.parsed.append(str(document_id))

    def enqueue_purge_document(self, storage_prefix: str) -> None:
        _FakeTaskQueue.purged.append(storage_prefix)


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(settings, "storage_root", str(tmp_path))
    _FakeTaskQueue.parsed = []
    _FakeTaskQueue.purged = []
    app.dependency_overrides[get_task_queue] = _FakeTaskQueue
    yield
    app.dependency_overrides.clear()


async def _auth_headers(client: AsyncClient, email: str) -> dict[str, str]:
    await client.post("/api/auth/register", json={"email": email, "password": _PASSWORD})
    resp = await client.post("/api/auth/login", json={"email": email, "password": _PASSWORD})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _upload(
    client: AsyncClient,
    headers: dict[str, str],
    *,
    filename: str = "note.txt",
    data: bytes = b"hello world",
    content_type: str = "text/plain",
    source_id: str | None = None,
) -> tuple[int, dict[str, Any]]:
    payload = {"source_id": source_id} if source_id is not None else None
    resp = await client.post(
        "/api/documents",
        files={"file": (filename, data, content_type)},
        data=payload,
        headers=headers,
    )
    return resp.status_code, resp.json() if resp.content else {}


# --- 上傳與 dedup(D8)-------------------------------------------------------

async def test_upload_new_file_202_and_enqueued(client: AsyncClient) -> None:
    headers = await _auth_headers(client, "a@example.com")
    status_code, body = await _upload(client, headers)
    assert status_code == 202, body
    assert body["status"] == "pending"
    assert body["deduplicated"] is False
    assert body["filename"] == "note.txt"
    assert body["mime"] == "text/plain"
    assert body["size_bytes"] == len(b"hello world")
    assert _FakeTaskQueue.parsed == [body["id"]]


async def test_upload_writes_original_to_storage(client: AsyncClient, tmp_path: Path) -> None:
    headers = await _auth_headers(client, "a@example.com")
    _, body = await _upload(client, headers)
    stored = tmp_path / "documents" / str(body["id"]) / "original.txt"
    assert stored.read_bytes() == b"hello world"


async def test_upload_duplicate_returns_200_deduplicated(client: AsyncClient) -> None:
    headers = await _auth_headers(client, "a@example.com")
    _, first = await _upload(client, headers)
    _FakeTaskQueue.parsed = []
    status_code, second = await _upload(client, headers)
    assert status_code == 200
    assert second["deduplicated"] is True
    assert second["id"] == first["id"]
    assert _FakeTaskQueue.parsed == []  # NEVER 重跑 pipeline


async def test_upload_duplicate_of_failed_document_resets_and_reruns(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    headers = await _auth_headers(client, "a@example.com")
    _, first = await _upload(client, headers)
    async with session_factory() as session:
        doc = await session.get(Document, UUID(str(first["id"])))
        assert doc is not None
        doc.status = "failed"
        doc.error = "boom"
        await session.commit()

    _FakeTaskQueue.parsed = []
    status_code, second = await _upload(client, headers)
    assert status_code == 200
    assert second["deduplicated"] is True
    assert second["status"] == "pending"
    assert second["error"] is None
    assert _FakeTaskQueue.parsed == [first["id"]]


async def test_dedup_is_per_owner(client: AsyncClient) -> None:
    headers_a = await _auth_headers(client, "a@example.com")
    headers_b = await _auth_headers(client, "b@example.com")
    _, doc_a = await _upload(client, headers_a)
    status_code, doc_b = await _upload(client, headers_b)
    assert status_code == 202
    assert doc_b["id"] != doc_a["id"]


# --- 型別 / 大小把關 --------------------------------------------------------

async def test_unsupported_extension_415(client: AsyncClient) -> None:
    headers = await _auth_headers(client, "a@example.com")
    status_code, body = await _upload(
        client, headers, filename="a.exe", data=b"MZ", content_type="application/octet-stream"
    )
    assert status_code == 415
    assert body["error"]["code"] == "unsupported_media_type"


async def test_content_type_mismatch_415(client: AsyncClient) -> None:
    headers = await _auth_headers(client, "a@example.com")
    status_code, body = await _upload(
        client, headers, filename="a.txt", data=b"x", content_type="application/pdf"
    )
    assert status_code == 415
    assert body["error"]["code"] == "unsupported_media_type"


async def test_octet_stream_content_type_is_accepted(client: AsyncClient) -> None:
    # client 送 application/octet-stream 僅為 sanity check 放行項(§v1.2 parser 補遺)
    headers = await _auth_headers(client, "a@example.com")
    status_code, body = await _upload(
        client, headers, filename="a.md", data=b"# t", content_type="application/octet-stream"
    )
    assert status_code == 202
    assert body["mime"] == "text/markdown"  # canonical mime 由副檔名推導


async def test_pdf_without_magic_415(client: AsyncClient) -> None:
    headers = await _auth_headers(client, "a@example.com")
    status_code, body = await _upload(
        client, headers, filename="fake.pdf", data=b"just text", content_type="application/pdf"
    )
    assert status_code == 415
    assert body["error"]["code"] == "unsupported_media_type"


async def test_pdf_with_magic_accepted(client: AsyncClient) -> None:
    headers = await _auth_headers(client, "a@example.com")
    status_code, body = await _upload(
        client, headers, filename="a.pdf", data=_PDF_BYTES, content_type="application/pdf"
    )
    assert status_code == 202
    assert body["mime"] == "application/pdf"


async def test_file_too_large_413(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    headers = await _auth_headers(client, "a@example.com")
    monkeypatch.setattr(settings, "max_upload_mb", 0)  # 上限 0 bytes
    status_code, body = await _upload(client, headers)
    assert status_code == 413
    assert body["error"]["code"] == "file_too_large"


async def test_too_large_file_is_not_persisted(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    headers = await _auth_headers(client, "a@example.com")
    monkeypatch.setattr(settings, "max_upload_mb", 0)
    await _upload(client, headers)
    assert not (tmp_path / "documents").exists()


# --- 來源(懶建 + 明指)------------------------------------------------------

async def test_default_source_is_created_once(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    headers = await _auth_headers(client, "a@example.com")
    _, first = await _upload(client, headers, data=b"one")
    _, second = await _upload(client, headers, data=b"two")
    assert first["source_id"] == second["source_id"]
    async with session_factory() as session:
        rows = (await session.execute(select(KnowledgeSource))).scalars().all()
    assert len(rows) == 1
    assert rows[0].type == "upload"


async def test_unknown_source_id_404(client: AsyncClient) -> None:
    headers = await _auth_headers(client, "a@example.com")
    status_code, body = await _upload(client, headers, source_id=str(uuid4()))
    assert status_code == 404
    assert body["error"]["code"] == "source_not_found"


async def test_other_users_source_id_404(client: AsyncClient) -> None:
    headers_a = await _auth_headers(client, "a@example.com")
    headers_b = await _auth_headers(client, "b@example.com")
    _, doc_a = await _upload(client, headers_a)
    status_code, body = await _upload(client, headers_b, source_id=str(doc_a["source_id"]))
    assert status_code == 404
    assert body["error"]["code"] == "source_not_found"


# --- 查詢 -------------------------------------------------------------------

async def test_list_and_get(client: AsyncClient) -> None:
    headers = await _auth_headers(client, "a@example.com")
    _, doc = await _upload(client, headers)
    resp = await client.get("/api/documents", headers=headers)
    assert resp.status_code == 200
    page = resp.json()
    assert [item["id"] for item in page["items"]] == [doc["id"]]
    assert page["next_cursor"] is None

    detail = await client.get(f"/api/documents/{doc['id']}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["id"] == doc["id"]


async def test_list_paginates_with_cursor(client: AsyncClient) -> None:
    headers = await _auth_headers(client, "a@example.com")
    for i in range(3):
        await _upload(client, headers, data=f"body-{i}".encode())
    first = (await client.get("/api/documents?limit=2", headers=headers)).json()
    assert len(first["items"]) == 2
    assert first["next_cursor"] is not None
    second = (
        await client.get(
            f"/api/documents?limit=2&cursor={first['next_cursor']}", headers=headers
        )
    ).json()
    assert len(second["items"]) == 1
    assert second["next_cursor"] is None


async def test_list_filters_by_status(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    headers = await _auth_headers(client, "a@example.com")
    _, kept = await _upload(client, headers, data=b"one")
    _, other = await _upload(client, headers, data=b"two")
    async with session_factory() as session:
        doc = await session.get(Document, UUID(str(other["id"])))
        assert doc is not None
        doc.status = "ready"
        await session.commit()
    page = (await client.get("/api/documents?status=pending", headers=headers)).json()
    assert [item["id"] for item in page["items"]] == [kept["id"]]


async def test_list_invalid_cursor_422(client: AsyncClient) -> None:
    headers = await _auth_headers(client, "a@example.com")
    resp = await client.get("/api/documents?cursor=not-a-cursor", headers=headers)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_cursor"


async def test_get_other_users_document_404(client: AsyncClient) -> None:
    headers_a = await _auth_headers(client, "a@example.com")
    headers_b = await _auth_headers(client, "b@example.com")
    _, doc = await _upload(client, headers_a)
    resp = await client.get(f"/api/documents/{doc['id']}", headers=headers_b)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "document_not_found"


async def test_list_excludes_other_users_documents(client: AsyncClient) -> None:
    headers_a = await _auth_headers(client, "a@example.com")
    headers_b = await _auth_headers(client, "b@example.com")
    await _upload(client, headers_a)
    page = (await client.get("/api/documents", headers=headers_b)).json()
    assert page["items"] == []


async def test_requires_auth(client: AsyncClient) -> None:
    resp = await client.get("/api/documents")
    assert resp.status_code == 401


# --- retry(§8.3)------------------------------------------------------------

async def test_retry_only_allowed_for_failed(client: AsyncClient) -> None:
    headers = await _auth_headers(client, "a@example.com")
    _, doc = await _upload(client, headers)
    resp = await client.post(f"/api/documents/{doc['id']}/retry", headers=headers)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "document_not_retryable"


async def test_retry_resets_failed_document(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    headers = await _auth_headers(client, "a@example.com")
    _, doc = await _upload(client, headers)
    async with session_factory() as session:
        row = await session.get(Document, UUID(str(doc["id"])))
        assert row is not None
        row.status = "failed"
        row.error = "boom"
        await session.commit()

    _FakeTaskQueue.parsed = []
    resp = await client.post(f"/api/documents/{doc['id']}/retry", headers=headers)
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "pending"
    assert body["error"] is None
    assert _FakeTaskQueue.parsed == [doc["id"]]


async def test_retry_other_users_document_404(client: AsyncClient) -> None:
    headers_a = await _auth_headers(client, "a@example.com")
    headers_b = await _auth_headers(client, "b@example.com")
    _, doc = await _upload(client, headers_a)
    resp = await client.post(f"/api/documents/{doc['id']}/retry", headers=headers_b)
    assert resp.status_code == 404


# --- 刪除(D12)-------------------------------------------------------------

async def test_delete_returns_204_then_404(client: AsyncClient) -> None:
    headers = await _auth_headers(client, "a@example.com")
    _, doc = await _upload(client, headers)
    resp = await client.delete(f"/api/documents/{doc['id']}", headers=headers)
    assert resp.status_code == 204
    assert _FakeTaskQueue.purged == [f"documents/{doc['id']}/"]
    follow_up = await client.get(f"/api/documents/{doc['id']}", headers=headers)
    assert follow_up.status_code == 404


async def test_delete_other_users_document_404(client: AsyncClient) -> None:
    headers_a = await _auth_headers(client, "a@example.com")
    headers_b = await _auth_headers(client, "b@example.com")
    _, doc = await _upload(client, headers_a)
    resp = await client.delete(f"/api/documents/{doc['id']}", headers=headers_b)
    assert resp.status_code == 404
    assert _FakeTaskQueue.purged == []
