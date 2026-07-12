"""OpenAI-compatible adapter 測試(T1.3;PHASE_1 §7 / §12.3)。

錄放:respx 攔截 httpx，CI NEVER 打真實 LLM(MASTER_PLAN_v1 §0.3-10)。
斷言 StreamEvent 序列符合 §6(phase-6 §3)五型別契約。
"""
import json
from collections.abc import Sequence
from pathlib import Path

import httpx
import pytest
import respx

from app.domain.ports.llm import (
    ChatMessage,
    ModelParams,
    StreamError,
    StreamEvent,
    StreamStop,
    TextDelta,
    UsageInfo,
)
from app.infrastructure.llm.openai_compat import OpenAICompatProvider

_FIXTURES = Path(__file__).parent / "fixtures" / "llm"
_BASE_URL = "http://llm.test/v1"
_URL = f"{_BASE_URL}/chat/completions"
_SSE_HEADERS = {"content-type": "text/event-stream"}


def _load(name: str) -> bytes:
    return (_FIXTURES / name).read_bytes()


async def _run(provider: OpenAICompatProvider) -> list[StreamEvent]:
    events = [
        ev
        async for ev in provider.chat(
            [ChatMessage(role="user", content="hi")],
            tools=None,
            tool_choice="none",
            params=ModelParams(model="test-model"),
            stream=True,
        )
    ]
    return events


async def _collect(
    *,
    body: bytes | None = None,
    status: int = 200,
    side_effect: Exception | None = None,
) -> list[StreamEvent]:
    route = respx.post(_URL)
    if side_effect is not None:
        route.mock(side_effect=side_effect)
    else:
        route.mock(return_value=httpx.Response(status, content=body, headers=_SSE_HEADERS))
    provider = OpenAICompatProvider(base_url=_BASE_URL, api_key="x", timeout_s=5)
    try:
        return await _run(provider)
    finally:
        await provider.aclose()


def _types(events: Sequence[StreamEvent]) -> list[str]:
    return [ev.type for ev in events]


@pytest.mark.anyio
@respx.mock
async def test_parses_ok_stream_text_usage_stop() -> None:
    events = await _collect(body=_load("chat_stream_ok.txt"))

    # 事件順序契約:0+ text_delta → 0..1 usage → 恰好 1 終端(stop)
    assert _types(events) == ["text_delta", "text_delta", "usage", "stop"]
    text = "".join(e.text for e in events if isinstance(e, TextDelta))
    assert text == "你好"
    usage = next(e for e in events if isinstance(e, UsageInfo))
    assert (usage.input_tokens, usage.output_tokens) == (10, 2)
    stop = events[-1]
    assert isinstance(stop, StreamStop) and stop.stop_reason == "end_turn"


@pytest.mark.anyio
@respx.mock
async def test_no_usage_chunk_yields_no_usage_event() -> None:
    events = await _collect(body=_load("chat_stream_no_usage.txt"))

    assert _types(events) == ["text_delta", "text_delta", "stop"]
    assert not any(isinstance(e, UsageInfo) for e in events)
    assert isinstance(events[-1], StreamStop)


@pytest.mark.anyio
@respx.mock
async def test_truncated_stream_maps_to_transient() -> None:
    # 收到部分 delta 後串流中斷(無 [DONE])→ 已產文字 + 終端 transient error
    events = await _collect(body=_load("chat_stream_truncated.txt"))

    assert _types(events) == ["text_delta", "text_delta", "error"]
    err = events[-1]
    assert isinstance(err, StreamError) and err.code == "transient"


@pytest.mark.anyio
@respx.mock
async def test_http_429_maps_to_rate_limited() -> None:
    events = await _collect(body=_load("error_rate_limited.json"), status=429)

    assert len(events) == 1
    err = events[0]
    assert isinstance(err, StreamError) and err.code == "rate_limited"


@pytest.mark.anyio
@respx.mock
async def test_http_401_maps_to_auth() -> None:
    events = await _collect(body=b'{"error":"unauthorized"}', status=401)

    assert isinstance(events[0], StreamError) and events[0].code == "auth"


@pytest.mark.anyio
@respx.mock
async def test_http_400_context_maps_to_context_length() -> None:
    body = b'{"error":{"message":"maximum context length exceeded"}}'
    events = await _collect(body=body, status=400)

    assert isinstance(events[0], StreamError) and events[0].code == "context_length"


@pytest.mark.anyio
@respx.mock
async def test_http_500_maps_to_transient() -> None:
    events = await _collect(body=b"upstream boom", status=500)

    assert isinstance(events[0], StreamError) and events[0].code == "transient"


@pytest.mark.anyio
@respx.mock
async def test_timeout_maps_to_transient() -> None:
    events = await _collect(side_effect=httpx.ReadTimeout("timed out"))

    assert len(events) == 1
    assert isinstance(events[0], StreamError) and events[0].code == "transient"


@pytest.mark.anyio
@respx.mock
async def test_connect_error_maps_to_transient() -> None:
    events = await _collect(side_effect=httpx.ConnectError("refused"))

    assert isinstance(events[0], StreamError) and events[0].code == "transient"


@pytest.mark.anyio
@respx.mock
async def test_error_never_raises_and_is_terminal() -> None:
    # NEVER 拋出到呼叫端:即使上游炸掉,也只會拿到一個終端 error 事件
    events = await _collect(side_effect=httpx.ConnectError("refused"))

    assert [ev.type for ev in events].count("error") == 1
    assert events[-1].type == "error"


@pytest.mark.anyio
@respx.mock
async def test_request_body_shape() -> None:
    route = respx.post(_URL).mock(
        return_value=httpx.Response(
            200, content=_load("chat_stream_no_usage.txt"), headers=_SSE_HEADERS
        )
    )
    provider = OpenAICompatProvider(base_url=_BASE_URL, api_key="secret", timeout_s=5)
    try:
        await _run(provider)
    finally:
        await provider.aclose()

    sent = json.loads(route.calls.last.request.content)
    assert sent["model"] == "test-model"
    assert sent["stream"] is True
    assert sent["stream_options"] == {"include_usage": True}
    assert sent["messages"] == [{"role": "user", "content": "hi"}]
    # P1 tools=None:body NEVER 帶 tools / tool_choice
    assert "tools" not in sent and "tool_choice" not in sent
    # Bearer 帶入,但 api_key NEVER 進 body
    assert route.calls.last.request.headers["authorization"] == "Bearer secret"
