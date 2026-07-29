"""SSE chat endpoint 整合測試(測試 app + 測試 DB;PHASE_1 §14 T1.4 integration)。

以 app.dependency_overrides 注入 fake LLM(CI NEVER 打真實 API);對測試 app 發 POST
並解析 SSE frame 至 done。
"""
import contextlib
import json
from collections.abc import AsyncIterator, Iterator
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncConnection

from app.api.deps import get_llm, get_retrieval, get_task_queue
from app.domain.entities.chunk import RetrievedChunk
from app.domain.ports.llm import (
    ChatMessage,
    ModelParams,
    StreamEvent,
    StreamStop,
    TextDelta,
    UsageInfo,
)
from app.main import app
from tests.fakes import FakeChunkIndex, fake_retrieval

pytestmark = pytest.mark.anyio

_PASSWORD = "password123"


class _FakeLLM:
    name = "fake"

    def __init__(self, script: list[StreamEvent]) -> None:
        self._script = script

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: object,
        tool_choice: object,
        params: ModelParams,
        stream: bool,
    ) -> AsyncIterator[StreamEvent]:
        for ev in self._script:
            yield ev


class _FakeTaskQueue:
    def enqueue_generate_title(self, conversation_id: object) -> None:
        pass

    # TaskQueue port 的 P2 方法;chat 路徑不會呼叫,僅為滿足 Protocol。
    def enqueue_parse_document(self, document_id: object) -> None:
        pass

    def enqueue_chunk_document(self, document_id: object) -> None:
        pass

    def enqueue_embed_chunks(self, document_id: object) -> None:
        pass

    def enqueue_purge_document(self, storage_prefix: str) -> None:
        pass


class _NeverRetrieval:
    async def retrieve(self, *args: object, **kwargs: object) -> list[object]:
        raise AssertionError("純聊天 NEVER 檢索")


def _use_llm(script: list[StreamEvent]) -> None:
    app.dependency_overrides[get_llm] = lambda: _FakeLLM(script)


def _use_retrieval(chunks: list[RetrievedChunk]) -> FakeChunkIndex:
    index = FakeChunkIndex(chunks)
    app.dependency_overrides[get_retrieval] = lambda: fake_retrieval(index)
    return index


@pytest.fixture(autouse=True)
def _fake_queue() -> Iterator[None]:
    # 所有 chat API 測試不碰 broker:標題入列以 fake 取代(§C.5.7)。
    # 檢索預設為「呼叫即失敗」:純聊天路徑 MUST 完全不碰檢索(P1 行為回歸)。
    app.dependency_overrides[get_task_queue] = _FakeTaskQueue
    app.dependency_overrides[get_retrieval] = _NeverRetrieval
    yield
    app.dependency_overrides.clear()


async def _auth_headers(client: AsyncClient, email: str) -> dict[str, str]:
    await client.post("/api/auth/register", json={"email": email, "password": _PASSWORD})
    resp = await client.post("/api/auth/login", json={"email": email, "password": _PASSWORD})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _create_conversation(client: AsyncClient, headers: dict[str, str]) -> str:
    resp = await client.post("/api/conversations", json={}, headers=headers)
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


def _parse_events(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for frame in text.split("\n\n"):
        event = data = None
        for line in frame.splitlines():
            if line.startswith("event:"):
                event = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data = line[len("data:") :].strip()
        if event is not None:
            out.append((event, data or ""))
    return out


async def test_sse_stream_to_done(client: AsyncClient) -> None:
    headers = await _auth_headers(client, "a@example.com")
    conv_id = await _create_conversation(client, headers)
    _use_llm(
        [TextDelta(text="你"), TextDelta(text="好"),
         UsageInfo(input_tokens=3, output_tokens=2), StreamStop(stop_reason="end_turn")]
    )

    resp = await client.post(
        f"/api/conversations/{conv_id}/messages", json={"content": "hi"}, headers=headers
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    events = _parse_events(resp.text)
    names = [e for e, _ in events]
    assert names == ["message_start", "delta", "delta", "done"]
    assert '"你"' in events[1][1] and '"好"' in events[2][1]
    assert '"finish_reason": "stop"' in events[-1][1]


async def test_post_other_users_conversation_404_json(client: AsyncClient) -> None:
    a = await _auth_headers(client, "a@example.com")
    b = await _auth_headers(client, "b@example.com")
    conv_id = await _create_conversation(client, a)
    _use_llm([TextDelta(text="x"), StreamStop(stop_reason="end_turn")])

    # 串流前錯誤走一般 JSON(非 SSE)
    resp = await client.post(
        f"/api/conversations/{conv_id}/messages", json={"content": "hi"}, headers=b
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "conversation_not_found"


async def test_missing_conversation_404_json(client: AsyncClient) -> None:
    headers = await _auth_headers(client, "a@example.com")
    _use_llm([TextDelta(text="x"), StreamStop(stop_reason="end_turn")])
    resp = await client.post(
        f"/api/conversations/{uuid4()}/messages", json={"content": "hi"}, headers=headers
    )
    assert resp.status_code == 404


async def test_duplicate_client_message_id_409_json(client: AsyncClient) -> None:
    headers = await _auth_headers(client, "a@example.com")
    conv_id = await _create_conversation(client, headers)
    cmid = str(uuid4())
    _use_llm([TextDelta(text="x"), StreamStop(stop_reason="end_turn")])

    first = await client.post(
        f"/api/conversations/{conv_id}/messages",
        json={"content": "hi", "client_message_id": cmid},
        headers=headers,
    )
    assert first.status_code == 200

    dup = await client.post(
        f"/api/conversations/{conv_id}/messages",
        json={"content": "hi", "client_message_id": cmid},
        headers=headers,
    )
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "duplicate_message"


async def test_content_validation_422(client: AsyncClient) -> None:
    headers = await _auth_headers(client, "a@example.com")
    conv_id = await _create_conversation(client, headers)
    _use_llm([TextDelta(text="x"), StreamStop(stop_reason="end_turn")])
    resp = await client.post(
        f"/api/conversations/{conv_id}/messages", json={"content": ""}, headers=headers
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


# --- RAG(T2.6;§10)---------------------------------------------------------

def _chunk(rank: int = 1, filename: str = "手冊.pdf", text: str = "報帳流程是 A 到 B") -> (
    RetrievedChunk
):
    return RetrievedChunk(
        chunk_id=uuid4(),
        document_id=uuid4(),
        filename=filename,
        text=text,
        score=0.0323,
        rank=rank,
        meta={"kind": "document", "heading_path": ["第一章"]},
    )


async def test_knowledge_scope_yields_citations_before_deltas(client: AsyncClient) -> None:
    headers = await _auth_headers(client, "a@example.com")
    conv_id = await _create_conversation(client, headers)
    _use_llm([TextDelta(text="依據 [1]"), StreamStop(stop_reason="end_turn")])
    _use_retrieval([_chunk()])

    resp = await client.post(
        f"/api/conversations/{conv_id}/messages",
        json={"content": "報帳怎麼跑?", "knowledge_scope": {"source_ids": []}},
        headers=headers,
    )
    assert resp.status_code == 200
    events = _parse_events(resp.text)
    assert [e for e, _ in events] == ["message_start", "citations", "delta", "done"]
    items = json.loads(events[1][1])["items"]
    assert items[0]["rank"] == 1 and items[0]["filename"] == "手冊.pdf"


async def test_pure_chat_sends_no_citations_event(client: AsyncClient) -> None:
    # 純聊天:_NeverRetrieval 會在被呼叫時炸開,故本測試同時證明「NEVER 檢索」
    headers = await _auth_headers(client, "a@example.com")
    conv_id = await _create_conversation(client, headers)
    _use_llm([TextDelta(text="哈囉"), StreamStop(stop_reason="end_turn")])

    resp = await client.post(
        f"/api/conversations/{conv_id}/messages", json={"content": "hi"}, headers=headers
    )
    assert [e for e, _ in _parse_events(resp.text)] == ["message_start", "delta", "done"]


async def test_unknown_source_id_404_before_stream(client: AsyncClient) -> None:
    headers = await _auth_headers(client, "a@example.com")
    conv_id = await _create_conversation(client, headers)
    _use_llm([TextDelta(text="x"), StreamStop(stop_reason="end_turn")])
    _use_retrieval([])

    resp = await client.post(
        f"/api/conversations/{conv_id}/messages",
        json={"content": "hi", "knowledge_scope": {"source_ids": [str(uuid4())]}},
        headers=headers,
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "source_not_found"


async def test_citations_returned_by_messages_api(client: AsyncClient) -> None:
    headers = await _auth_headers(client, "a@example.com")
    conv_id = await _create_conversation(client, headers)
    _use_llm([TextDelta(text="答案"), StreamStop(stop_reason="end_turn")])
    _use_retrieval([_chunk()])

    await client.post(
        f"/api/conversations/{conv_id}/messages",
        json={"content": "報帳怎麼跑?", "knowledge_scope": {"source_ids": []}},
        headers=headers,
    )
    page = await client.get(f"/api/conversations/{conv_id}/messages", headers=headers)
    assert page.status_code == 200
    by_role = {m["role"]: m for m in page.json()["items"]}
    assert by_role["assistant"]["citations"][0]["filename"] == "手冊.pdf"
    assert by_role["assistant"]["content_meta"]["rag"] == {"source_ids": [], "top_n": 1}
    assert by_role["user"]["citations"] == []  # 使用者訊息無引用


@contextlib.contextmanager
def _count_statements(connection: AsyncConnection) -> Iterator[list[str]]:
    """記錄期間內實際送到 DB 的 SQL(斷言無 N+1;§16)。"""
    seen: list[str] = []

    def _before(
        conn: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        seen.append(statement)

    sync_connection = connection.sync_connection
    assert sync_connection is not None
    event.listen(sync_connection, "before_cursor_execute", _before)
    try:
        yield seen
    finally:
        event.remove(sync_connection, "before_cursor_execute", _before)


async def test_messages_api_loads_citations_without_n_plus_1(
    client: AsyncClient, db_connection: AsyncConnection
) -> None:
    headers = await _auth_headers(client, "a@example.com")
    conv_id = await _create_conversation(client, headers)
    for _ in range(3):
        _use_llm([TextDelta(text="答案"), StreamStop(stop_reason="end_turn")])
        _use_retrieval([_chunk(), _chunk(rank=2, filename="附錄.pdf")])
        resp = await client.post(
            f"/api/conversations/{conv_id}/messages",
            json={"content": "報帳怎麼跑?", "knowledge_scope": {"source_ids": []}},
            headers=headers,
        )
        assert resp.status_code == 200

    with _count_statements(db_connection) as statements:
        page = await client.get(f"/api/conversations/{conv_id}/messages", headers=headers)

    assert page.status_code == 200
    assert len(page.json()["items"]) == 6  # 3 輪 = 3 user + 3 assistant
    citation_queries = [s for s in statements if "message_citations" in s]
    assert len(citation_queries) == 1  # 整頁一次載入,NEVER 逐則查詢
