"""ChatOrchestrator 單元測試(fake LLM/TaskQueue + 測試 DB;PHASE_1 §12.2、§14 T1.4)。

orchestrator 以 session_factory 自管短交易(§D5),故測試連測試 DB;LLM 與 TaskQueue
一律 fake,NEVER 打真實 API。
"""
from collections.abc import AsyncIterator
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.chat_orchestrator import ChatOrchestrator
from app.core.config import settings
from app.core.errors import DuplicateMessage
from app.domain.entities.auth_context import AuthContext
from app.domain.ports.llm import (
    ChatMessage,
    ModelParams,
    StreamError,
    StreamEvent,
    StreamStop,
    TextDelta,
    UsageInfo,
)
from app.infrastructure.db.models import Conversation, Message, ModelUsageLog, User

pytestmark = pytest.mark.anyio


class FakeLLMProvider:
    name = "fake"

    def __init__(self, script: list[StreamEvent]) -> None:
        self._script = script
        self.seen_messages: list[ChatMessage] = []

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: object,
        tool_choice: object,
        params: ModelParams,
        stream: bool,
    ) -> AsyncIterator[StreamEvent]:
        self.seen_messages = list(messages)
        for ev in self._script:
            yield ev


class FakeTaskQueue:
    def __init__(self) -> None:
        self.enqueued: list[UUID] = []

    def enqueue_generate_title(self, conversation_id: UUID) -> None:
        self.enqueued.append(conversation_id)


async def _seed(
    session_factory: async_sessionmaker[AsyncSession], *, title: str | None = None
) -> tuple[AuthContext, UUID]:
    async with session_factory() as session:
        user = User(email="u@example.com", password_hash="x")
        session.add(user)
        await session.flush()
        conv = Conversation(user_id=user.id, title=title, model_alias="local-default")
        session.add(conv)
        await session.flush()
        await session.commit()
        ctx = AuthContext(user_id=user.id, role="user", trace_id="trace-1")
        return ctx, conv.id


def _orch(
    session_factory: async_sessionmaker[AsyncSession],
    llm: FakeLLMProvider,
    tasks: FakeTaskQueue,
) -> ChatOrchestrator:
    return ChatOrchestrator(
        session_factory=session_factory, llm=llm, settings=settings, task_queue=tasks
    )


async def _events(agen: AsyncIterator[dict[str, object]]) -> list[dict[str, object]]:
    return [e async for e in agen]


async def _messages(
    session_factory: async_sessionmaker[AsyncSession], conv_id: UUID, role: str
) -> list[Message]:
    async with session_factory() as session:
        rows = await session.execute(
            select(Message)
            .where(Message.conversation_id == conv_id, Message.role == role)
            .order_by(Message.created_at)
        )
        return list(rows.scalars().all())


async def _usage(
    session_factory: async_sessionmaker[AsyncSession], conv_id: UUID
) -> list[ModelUsageLog]:
    async with session_factory() as session:
        rows = await session.execute(
            select(ModelUsageLog).where(ModelUsageLog.conversation_id == conv_id)
        )
        return list(rows.scalars().all())


# --- 正常流 -----------------------------------------------------------------

async def test_stream_reply_persists_assistant_and_usage(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    ctx, conv_id = await _seed(session_factory)
    llm = FakeLLMProvider(
        [TextDelta(text="你"), TextDelta(text="好"), UsageInfo(input_tokens=10, output_tokens=2),
         StreamStop(stop_reason="end_turn")]
    )
    tasks = FakeTaskQueue()
    orch = _orch(session_factory, llm, tasks)
    events = await _events(orch.stream_reply(ctx, conv_id, "hi", None))

    assert [e["event"] for e in events] == ["message_start", "delta", "delta", "done"]
    done = events[-1]["data"]
    assert isinstance(done, dict)
    assert done["finish_reason"] == "stop"
    assert (done["tokens_in"], done["tokens_out"]) == (10, 2)

    saved = await _messages(session_factory, conv_id, "assistant")
    assert len(saved) == 1
    assert saved[0].content == "你好"
    assert saved[0].provider == "fake" and saved[0].tokens_in == 10 and saved[0].tokens_out == 2

    usage = await _usage(session_factory, conv_id)
    assert len(usage) == 1 and usage[0].status == "ok"
    assert tasks.enqueued == [conv_id]  # title 為 None → 首輪觸發標題任務


async def test_no_usage_event_leaves_tokens_null(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    ctx, conv_id = await _seed(session_factory)
    llm = FakeLLMProvider([TextDelta(text="hi"), StreamStop(stop_reason="end_turn")])
    events = await _events(
        _orch(session_factory, llm, FakeTaskQueue()).stream_reply(ctx, conv_id, "hi", None)
    )

    done = events[-1]["data"]
    assert isinstance(done, dict)
    assert done["tokens_in"] is None and done["tokens_out"] is None
    saved = await _messages(session_factory, conv_id, "assistant")
    assert saved[0].tokens_in is None and saved[0].tokens_out is None


async def test_title_not_enqueued_when_already_set(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    ctx, conv_id = await _seed(session_factory, title="既有標題")
    llm = FakeLLMProvider([TextDelta(text="x"), StreamStop(stop_reason="end_turn")])
    tasks = FakeTaskQueue()
    await _events(_orch(session_factory, llm, tasks).stream_reply(ctx, conv_id, "hi", None))
    assert tasks.enqueued == []


# --- StreamError → partial + usage(error) ----------------------------------

async def test_stream_error_persists_partial_and_error_usage(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    ctx, conv_id = await _seed(session_factory)
    llm = FakeLLMProvider(
        [TextDelta(text="部分"), StreamError(code="transient", message="上游暫時無法回應")]
    )
    events = await _events(
        _orch(session_factory, llm, FakeTaskQueue()).stream_reply(ctx, conv_id, "hi", None)
    )

    assert [e["event"] for e in events] == ["message_start", "delta", "error"]
    err = events[-1]["data"]
    assert isinstance(err, dict)
    assert err["code"] == "transient" and err["trace_id"] == "trace-1"

    saved = await _messages(session_factory, conv_id, "assistant")
    assert len(saved) == 1 and saved[0].content == "部分"
    assert saved[0].content_meta == {"partial": True, "error_code": "transient"}
    usage = await _usage(session_factory, conv_id)
    assert usage[0].status == "error" and usage[0].error_code == "transient"


# --- 客端斷線 → partial ------------------------------------------------------

async def test_client_disconnect_persists_partial(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    ctx, conv_id = await _seed(session_factory)
    # 消費到第一個 delta 後 aclose() 模擬客端斷線
    llm = FakeLLMProvider(
        [TextDelta(text="片"), TextDelta(text="段"), StreamStop(stop_reason="end_turn")]
    )
    agen = _orch(session_factory, llm, FakeTaskQueue()).stream_reply(ctx, conv_id, "hi", None)

    seen = []
    async for ev in agen:
        seen.append(ev["event"])
        if ev["event"] == "delta":
            await agen.aclose()  # GeneratorExit → orchestrator 落 partial
            break

    assert seen == ["message_start", "delta"]
    saved = await _messages(session_factory, conv_id, "assistant")
    assert len(saved) == 1 and saved[0].content == "片"
    assert saved[0].content_meta == {"partial": True}
    usage = await _usage(session_factory, conv_id)
    assert usage[0].status == "ok"


# --- client_message_id 冪等 --------------------------------------------------

async def test_duplicate_client_message_id_raises(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    ctx, conv_id = await _seed(session_factory)
    cmid = UUID("11111111-1111-1111-1111-111111111111")

    llm = FakeLLMProvider([TextDelta(text="x"), StreamStop(stop_reason="end_turn")])
    first_orch = _orch(session_factory, llm, FakeTaskQueue())
    await _events(first_orch.stream_reply(ctx, conv_id, "hi", cmid))

    agen = _orch(session_factory, FakeLLMProvider([]), FakeTaskQueue()).stream_reply(
        ctx, conv_id, "hi again", cmid
    )
    with pytest.raises(DuplicateMessage):
        await agen.__anext__()


# --- 上下文裁切至 N ----------------------------------------------------------

async def test_history_truncated_to_limit(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    ctx, conv_id = await _seed(session_factory)
    async with session_factory() as session:
        for i in range(8):
            session.add(Message(conversation_id=conv_id, role="user", content=f"old-{i}"))
        await session.commit()

    limited = settings.model_copy(update={"chat_history_max_messages": 5})
    llm = FakeLLMProvider([TextDelta(text="ok"), StreamStop(stop_reason="end_turn")])
    orch = ChatOrchestrator(
        session_factory=session_factory, llm=llm, settings=limited, task_queue=FakeTaskQueue()
    )
    await _events(orch.stream_reply(ctx, conv_id, "newest", None))

    # prompt = 1 system + 最近 5 則(含本次 user);且最後一則為本次 user content
    assert len(llm.seen_messages) == 6
    assert llm.seen_messages[0].role == "system"
    assert llm.seen_messages[-1].content == "newest"
