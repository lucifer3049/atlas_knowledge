"""ChatOrchestrator 的 RAG 路徑測試(T2.6;PHASE_2 §10、§12.2 chat 列)。

檢索一律 fake(CI NEVER 打真實 API);orchestrator 自管短交易,故連測試 DB。
"""
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.chat_orchestrator import ChatOrchestrator
from app.core.config import Settings, settings
from app.core.errors import SourceNotFound
from app.domain.entities.auth_context import AuthContext
from app.domain.entities.chunk import RetrievedChunk
from app.domain.ports.llm import StreamError, StreamStop, TextDelta
from app.infrastructure.db.models import (
    Conversation,
    KnowledgeSource,
    Message,
    MessageCitation,
    User,
)
from tests.fakes import FailingEmbeddingProvider, FakeChunkIndex, fake_retrieval
from tests.test_chat_orchestrator import FakeLLMProvider, FakeTaskQueue

pytestmark = pytest.mark.anyio


def _chunk(n: int, *, text: str | None = None) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid4(),
        document_id=uuid4(),
        filename=f"手冊{n}.pdf",
        text=text or f"第 {n} 段內容",
        score=1.0 / n,
        rank=n,
        meta={"kind": "document", "heading_path": ["第一章", f"第 {n} 節"]},
    )


async def _seed(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[AuthContext, UUID, UUID]:
    async with session_factory() as session:
        user = User(email="rag@example.com", password_hash="x")
        session.add(user)
        await session.flush()
        source = KnowledgeSource(owner_id=user.id, name="我的上傳", type="upload")
        conv = Conversation(user_id=user.id, title="既有標題", model_alias="local-default")
        session.add_all([source, conv])
        await session.flush()
        await session.commit()
        ctx = AuthContext(user_id=user.id, role="user", trace_id="trace-rag")
        return ctx, conv.id, source.id


def _orch(
    session_factory: async_sessionmaker[AsyncSession],
    llm: FakeLLMProvider,
    *,
    chunks: list[RetrievedChunk] | None = None,
    failing_embedding: bool = False,
    settings_override: Settings | None = None,
) -> ChatOrchestrator:
    retrieval = fake_retrieval(
        FakeChunkIndex(chunks or []),
        embedding=FailingEmbeddingProvider() if failing_embedding else None,
    )
    return ChatOrchestrator(
        session_factory=session_factory,
        llm=llm,
        settings=settings_override or settings,
        task_queue=FakeTaskQueue(),
        retrieval=retrieval,
    )


async def _events(orch: ChatOrchestrator, ctx: AuthContext, conv_id: UUID,
                  source_ids: list[UUID] | None) -> list[dict[str, object]]:
    return [
        e async for e in orch.stream_reply(ctx, conv_id, "報帳流程?", None, source_ids)
    ]


async def _citations(
    session_factory: async_sessionmaker[AsyncSession], conv_id: UUID
) -> list[MessageCitation]:
    async with session_factory() as session:
        rows = await session.execute(
            select(MessageCitation)
            .join(Message, Message.id == MessageCitation.message_id)
            .where(Message.conversation_id == conv_id)
            .order_by(MessageCitation.rank)
        )
        return list(rows.scalars().all())


async def _assistant(
    session_factory: async_sessionmaker[AsyncSession], conv_id: UUID
) -> Message:
    async with session_factory() as session:
        rows = await session.execute(
            select(Message).where(
                Message.conversation_id == conv_id, Message.role == "assistant"
            )
        )
        return rows.scalars().one()


# --- citations 事件與落庫 -----------------------------------------------------

async def test_citations_event_precedes_deltas_and_is_persisted(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    ctx, conv_id, _ = await _seed(session_factory)
    llm = FakeLLMProvider([TextDelta(text="依據 [1]"), StreamStop(stop_reason="end_turn")])
    chunks = [_chunk(1), _chunk(2)]
    events = await _events(_orch(session_factory, llm, chunks=chunks), ctx, conv_id, [])

    assert [e["event"] for e in events] == ["message_start", "citations", "delta", "done"]
    data = events[1]["data"]
    assert isinstance(data, dict)
    items = cast(list[dict[str, object]], data["items"])
    assert [i["rank"] for i in items] == [1, 2]
    assert items[0]["filename"] == "手冊1.pdf"

    saved = await _citations(session_factory, conv_id)
    assert [(c.rank, c.filename) for c in saved] == [(1, "手冊1.pdf"), (2, "手冊2.pdf")]
    assert saved[0].chunk_id == chunks[0].chunk_id  # 軟引用保留原 id(D7)
    assert saved[0].snippet == chunks[0].text[:200]


async def test_assistant_content_meta_records_rag(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    ctx, conv_id, source_id = await _seed(session_factory)
    llm = FakeLLMProvider([TextDelta(text="好"), StreamStop(stop_reason="end_turn")])
    await _events(
        _orch(session_factory, llm, chunks=[_chunk(1)]), ctx, conv_id, [source_id]
    )

    assistant = await _assistant(session_factory, conv_id)
    assert assistant.content_meta == {
        "rag": {"source_ids": [str(source_id)], "top_n": 1}
    }


async def test_prompt_contains_numbered_context_blocks(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    ctx, conv_id, _ = await _seed(session_factory)
    llm = FakeLLMProvider([TextDelta(text="好"), StreamStop(stop_reason="end_turn")])
    await _events(_orch(session_factory, llm, chunks=[_chunk(1)]), ctx, conv_id, [])

    system = llm.seen_messages[0]
    assert system.role == "system"
    assert "[1] 手冊1.pdf｜第一章 > 第 1 節" in system.content
    assert "第 1 段內容" in system.content
    assert settings.chat_system_prompt in system.content  # 基底 prompt 仍在


async def test_empty_result_still_answers_without_citations_event(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    ctx, conv_id, _ = await _seed(session_factory)
    llm = FakeLLMProvider([TextDelta(text="資料不足"), StreamStop(stop_reason="end_turn")])
    events = await _events(_orch(session_factory, llm, chunks=[]), ctx, conv_id, [])

    # 空集合仍完成回答,但無引用 → NEVER 送 citations 事件(§10.3)
    assert [e["event"] for e in events] == ["message_start", "delta", "done"]
    assert "沒有檢索到相關資料" in llm.seen_messages[0].content
    assert await _citations(session_factory, conv_id) == []


async def test_context_budget_trims_blocks_and_citations_together(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # citations(SSE + 落庫)與進 prompt 的 blocks MUST 為同一份清單(v1.2 §10 補遺)
    ctx, conv_id, _ = await _seed(session_factory)
    llm = FakeLLMProvider([TextDelta(text="好"), StreamStop(stop_reason="end_turn")])
    chunks = [_chunk(n, text="內" * 200) for n in range(1, 6)]
    tight = settings.model_copy(update={"rag_context_char_budget": 500})
    events = await _events(
        _orch(session_factory, llm, chunks=chunks, settings_override=tight),
        ctx, conv_id, [],
    )

    data = events[1]["data"]
    assert isinstance(data, dict)
    items = cast(list[dict[str, object]], data["items"])
    assert 0 < len(items) < len(chunks)  # 預算裁掉了一部分
    assert llm.seen_messages[0].content.count("] 手冊") == len(items)
    assert len(await _citations(session_factory, conv_id)) == len(items)


# --- 失敗路徑 ----------------------------------------------------------------

async def test_retrieval_failure_yields_message_start_then_error(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    ctx, conv_id, _ = await _seed(session_factory)
    llm = FakeLLMProvider([TextDelta(text="不該被呼叫")])
    events = await _events(
        _orch(session_factory, llm, failing_embedding=True), ctx, conv_id, []
    )

    # 檢索失敗仍 MUST 先 message_start 再 error(v1.2 §10 補遺;順序契約不破)
    assert [e["event"] for e in events] == ["message_start", "error"]
    err = events[-1]["data"]
    assert isinstance(err, dict)
    assert err["code"] == "transient" and err["trace_id"] == "trace-rag"
    async with session_factory() as session:
        count = await session.execute(
            select(func.count())
            .select_from(Message)
            .where(Message.conversation_id == conv_id, Message.role == "assistant")
        )
        assert count.scalar_one() == 0  # 無 partial → 無 assistant 訊息


async def test_stream_error_partial_persists_citations(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    ctx, conv_id, _ = await _seed(session_factory)
    llm = FakeLLMProvider(
        [TextDelta(text="部分"), StreamError(code="transient", message="上游暫時無法回應")]
    )
    events = await _events(_orch(session_factory, llm, chunks=[_chunk(1)]), ctx, conv_id, [])

    assert [e["event"] for e in events] == ["message_start", "citations", "delta", "error"]
    assistant = await _assistant(session_factory, conv_id)
    assert assistant.content_meta["partial"] is True
    assert assistant.content_meta["rag"] == {"source_ids": [], "top_n": 1}
    assert len(await _citations(session_factory, conv_id)) == 1


async def test_unknown_source_id_raises_before_stream(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    ctx, conv_id, _ = await _seed(session_factory)
    llm = FakeLLMProvider([TextDelta(text="x"), StreamStop(stop_reason="end_turn")])
    agen = _orch(session_factory, llm).stream_reply(
        ctx, conv_id, "問句", None, [uuid4()]
    )
    with pytest.raises(SourceNotFound):
        await agen.__anext__()


async def test_other_users_source_id_raises(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    ctx, conv_id, _ = await _seed(session_factory)
    async with session_factory() as session:
        stranger = User(email="stranger@example.com", password_hash="x")
        session.add(stranger)
        await session.flush()
        theirs = KnowledgeSource(owner_id=stranger.id, name="我的上傳", type="upload")
        session.add(theirs)
        await session.flush()
        await session.commit()
        other_source_id = theirs.id

    llm = FakeLLMProvider([TextDelta(text="x"), StreamStop(stop_reason="end_turn")])
    agen = _orch(session_factory, llm).stream_reply(
        ctx, conv_id, "問句", None, [other_source_id]
    )
    with pytest.raises(SourceNotFound):  # 無權存取一律 404,NEVER 洩漏存在性
        await agen.__anext__()
