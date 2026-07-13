"""SSE chat endpoint 整合測試(測試 app + 測試 DB;PHASE_1 §14 T1.4 integration)。

以 app.dependency_overrides 注入 fake LLM(CI NEVER 打真實 API);對測試 app 發 POST
並解析 SSE frame 至 done。
"""
from collections.abc import AsyncIterator, Iterator
from uuid import uuid4

import pytest
from httpx import AsyncClient

from app.api.deps import get_llm
from app.domain.ports.llm import (
    ChatMessage,
    ModelParams,
    StreamEvent,
    StreamStop,
    TextDelta,
    UsageInfo,
)
from app.main import app

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


def _use_llm(script: list[StreamEvent]) -> None:
    app.dependency_overrides[get_llm] = lambda: _FakeLLM(script)


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
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
