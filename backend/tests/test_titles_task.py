"""generate_title 任務本體測試(fake LLM + 測試 DB;PHASE_1 §14 T1.7、§C.5.7)。

直接測 `_generate_title` 純邏輯:寫入 / 不覆寫 / LLM 失敗不拋出 / 截斷。NEVER 走 broker。
"""
from collections.abc import AsyncIterator
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.ports.llm import (
    ChatMessage,
    ModelParams,
    StreamError,
    StreamEvent,
    StreamStop,
    TextDelta,
)
from app.infrastructure.db.models import Conversation, Message, User
from app.workers.tasks.titles import _generate_title

pytestmark = pytest.mark.anyio


class _FakeLLM:
    name = "fake"

    def __init__(self, script: list[StreamEvent]) -> None:
        self._script = script
        self.calls = 0

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: object,
        tool_choice: object,
        params: ModelParams,
        stream: bool,
    ) -> AsyncIterator[StreamEvent]:
        self.calls += 1
        for ev in self._script:
            yield ev


async def _seed(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    title: str | None = None,
    with_assistant: bool = True,
) -> UUID:
    async with session_factory() as session:
        user = User(email="t@example.com", password_hash="x")
        session.add(user)
        await session.flush()
        conv = Conversation(user_id=user.id, title=title, model_alias="local-default")
        session.add(conv)
        await session.flush()
        session.add(Message(conversation_id=conv.id, role="user", content="怎麼報稅?"))
        if with_assistant:
            session.add(Message(conversation_id=conv.id, role="assistant", content="請先..."))
        await session.commit()
        return conv.id


async def _title(session_factory: async_sessionmaker[AsyncSession], conv_id: UUID) -> str | None:
    async with session_factory() as session:
        conv = await session.get(Conversation, conv_id)
        assert conv is not None
        return conv.title


async def test_writes_title(session_factory: async_sessionmaker[AsyncSession]) -> None:
    conv_id = await _seed(session_factory)
    llm = _FakeLLM(
        [TextDelta(text="報稅"), TextDelta(text="流程"), StreamStop(stop_reason="end_turn")]
    )
    await _generate_title(conv_id, session_factory=session_factory, llm=llm)
    assert await _title(session_factory, conv_id) == "報稅流程"


async def test_does_not_overwrite_existing_title(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    conv_id = await _seed(session_factory, title="既有標題")
    llm = _FakeLLM([TextDelta(text="新標題"), StreamStop(stop_reason="end_turn")])
    await _generate_title(conv_id, session_factory=session_factory, llm=llm)
    assert await _title(session_factory, conv_id) == "既有標題"
    assert llm.calls == 0  # 已有標題 → 根本不呼叫 LLM


async def test_llm_error_leaves_title_null_and_does_not_raise(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    conv_id = await _seed(session_factory)
    llm = _FakeLLM([StreamError(code="transient", message="上游暫時無法回應")])
    await _generate_title(conv_id, session_factory=session_factory, llm=llm)  # 不拋出
    assert await _title(session_factory, conv_id) is None


async def test_no_assistant_message_skips(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    conv_id = await _seed(session_factory, with_assistant=False)
    llm = _FakeLLM([TextDelta(text="x"), StreamStop(stop_reason="end_turn")])
    await _generate_title(conv_id, session_factory=session_factory, llm=llm)
    assert await _title(session_factory, conv_id) is None


async def test_title_cleaned_and_truncated(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    conv_id = await _seed(session_factory)
    # 含引號 + 多行 + 超過 20 字:取首行、去引號、截斷至 20
    llm = _FakeLLM(
        [TextDelta(text='「一二三四五六七八九十一二三四五六七八九十二十一」\n多餘'),
         StreamStop(stop_reason="end_turn")]
    )
    await _generate_title(conv_id, session_factory=session_factory, llm=llm)
    title = await _title(session_factory, conv_id)
    assert title is not None
    assert len(title) == 20 and "\n" not in title and "「" not in title
