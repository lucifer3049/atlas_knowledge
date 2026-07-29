"""ChatOrchestrator:使用者提問 → 串流回答核心迴路(PHASE_1 §8、T1.4;PHASE_2 §10)。

自管短交易(§D5):TXN A 載入/插入 user message、讀上下文 → commit 釋放連線 →
**LLM 串流期間 NEVER 持有 DB 連線** → TXN B 落 assistant message + usage log + citations。
對外 yield「應用層事件 dict」;SSE 序列化只在 router。

事件順序(§H.3、PHASE_2 §10.3):message_start → citations(0..1)→ delta* → (done | error)。
RAG(T2.6):`source_ids is None` = 純聊天(P1 行為位元級不變,NEVER 檢索、NEVER 送
citations);`[]` = 使用知識庫但不限來源;非空 list = 限定來源。**檢索結果為空時亦不送
citations 事件**(該事件只在有引用時出現)。檢索失敗仍先 message_start 再 error
(v1.2 §10 補遺)。
"""
import asyncio
import time
from collections.abc import AsyncGenerator, Coroutine, Sequence
from typing import Any, cast
from uuid import UUID

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.prompts import rag_system_prompt
from app.application.retrieval_service import RetrievalService
from app.core.config import Settings
from app.core.errors import ConversationNotFound, DuplicateMessage, SourceNotFound
from app.core.ids import new_id
from app.core.model_registry import resolve
from app.domain.entities.auth_context import AuthContext
from app.domain.entities.chunk import RetrievedChunk
from app.domain.ports.embedding import EmbeddingError
from app.domain.ports.llm import (
    ChatMessage,
    LLMProvider,
    ModelParams,
    Role,
    StreamError,
    StreamStop,
    TextDelta,
    UsageInfo,
)
from app.domain.ports.task_queue import TaskQueue
from app.infrastructure.db.models import Message, MessageCitation, ModelUsageLog
from app.infrastructure.db.repositories.citations import (
    SNIPPET_MAX_CHARS,
    MessageCitationRepository,
)
from app.infrastructure.db.repositories.conversations import ConversationRepository
from app.infrastructure.db.repositories.knowledge_sources import KnowledgeSourceRepository
from app.infrastructure.db.repositories.messages import MessageRepository
from app.infrastructure.db.repositories.usage import UsageRepository

_logger = structlog.get_logger()

# 檢索失敗(非 provider 分類的非預期例外)時對使用者呈現的固定訊息;細節只進 log。
_RETRIEVAL_FAILED_MESSAGE = "知識庫檢索暫時無法使用,請稍後再試"

# StreamStop.stop_reason → SSE done.finish_reason(§H.3;tool_use 於 P1 不觸發)
_FINISH_REASON = {"end_turn": "stop", "max_tokens": "length"}

# TXN B 失敗時對使用者呈現的固定訊息(§8/§10.4 2026-07-16 修訂);細節只進 log。
_PERSIST_FAILED_MESSAGE = "伺服器暫時發生錯誤,回覆未能儲存,請稍後再試"

# 客端斷線時的 partial 落庫需脫離被取消的請求任務才能完成 commit;以模組層集合持有
# 參照避免被 GC(done 後自動移除)。
_BACKGROUND_TASKS: set[asyncio.Task[None]] = set()


def _spawn_background(coro: Coroutine[object, object, None]) -> None:
    task = asyncio.ensure_future(coro)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


async def drain_background_tasks() -> None:
    """等待所有背景落庫任務結束(測試用;正式碼不需呼叫)。"""
    while _BACKGROUND_TASKS:
        await asyncio.gather(*tuple(_BACKGROUND_TASKS), return_exceptions=True)


class _TurnContext:
    """TXN A 的產出:串流所需、且已與 DB session 脫鉤的純資料。

    prompt 於檢索之後才組(system prompt 需併入 context blocks,§10.2),故 TXN A 只帶
    `history`;`citations` 由檢索階段填入,並同時決定 SSE 事件、落庫快照與 prompt blocks
    ——三者 MUST 為同一份清單(v1.2 §10 補遺)。
    """

    def __init__(
        self,
        *,
        user_message_id: UUID,
        assistant_message_id: UUID,
        history: list[ChatMessage],
        system_prompt: str,
        params: ModelParams,
        title_is_none: bool,
        source_ids: Sequence[UUID] | None,
    ) -> None:
        self.user_message_id = user_message_id
        self.assistant_message_id = assistant_message_id
        self.history = history
        self.system_prompt = system_prompt  # 檢索後可能被換成含 context blocks 的版本
        self.params = params
        self.title_is_none = title_is_none
        self.source_ids = source_ids
        self.citations: list[RetrievedChunk] = []

    def prompt(self) -> list[ChatMessage]:
        return [ChatMessage(role="system", content=self.system_prompt), *self.history]

    def content_meta(self, extra: dict[str, object] | None = None) -> dict[str, object]:
        meta: dict[str, object] = dict(extra or {})
        if self.source_ids is not None:
            # §10.2:top_n = 實際進 prompt 的 blocks 數(裁過 char budget 之後)。
            meta["rag"] = {
                "source_ids": [str(sid) for sid in self.source_ids],
                "top_n": len(self.citations),
            }
        return meta


class ChatOrchestrator:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        llm: LLMProvider,
        settings: Settings,
        task_queue: TaskQueue,
        retrieval: RetrievalService,
    ) -> None:
        self._session_factory = session_factory
        self._llm = llm
        self._settings = settings
        self._task_queue = task_queue
        self._retrieval = retrieval

    async def stream_reply(
        self,
        ctx: AuthContext,
        conversation_id: UUID,
        content: str,
        client_message_id: UUID | None,
        source_ids: Sequence[UUID] | None = None,
    ) -> AsyncGenerator[dict[str, object], None]:
        """`source_ids`:None = 純聊天;`[]` = 使用知識庫不限來源;list = 限定來源(§10.1)。"""
        # 1. TXN A:載入 + 插 user message + 讀上下文(可能 raise 404 / 409,皆於串流前)
        turn = await self._prepare_turn(
            ctx, conversation_id, content, client_message_id, source_ids
        )

        yield _event(
            "message_start",
            user_message_id=str(turn.user_message_id),
            assistant_message_id=str(turn.assistant_message_id),
        )

        # 2. 檢索(§10.2;純聊天略過)。失敗一律「message_start 之後」才發 error。
        if source_ids is not None:
            try:
                turn.citations = await self._retrieve(ctx, content, turn)
            except EmbeddingError as exc:
                await self._persist_error_guarded(ctx, conversation_id, turn, [], exc.code)
                yield _event(
                    "error", code=exc.code, message=exc.message, trace_id=ctx.trace_id
                )
                return
            except Exception:
                _logger.error("retrieval_failed", conversation_id=str(conversation_id),
                              exc_info=True)
                await self._persist_error_guarded(
                    ctx, conversation_id, turn, [], "internal_error"
                )
                yield _event(
                    "error",
                    code="internal_error",
                    message=_RETRIEVAL_FAILED_MESSAGE,
                    trace_id=ctx.trace_id,
                )
                return
            # 空結果 NEVER 送 citations 事件(§10.3「0..1 次」);prompt 仍註明無資料。
            if turn.citations:
                yield _event("citations", items=_citation_items(turn.citations))

        # 3. 串流(NEVER 持有 DB 連線)
        buffer: list[str] = []
        tokens_in: int | None = None
        tokens_out: int | None = None
        stop_reason = "end_turn"
        t0 = time.perf_counter()
        try:
            async for ev in self._llm.chat(
                turn.prompt(), tools=None, tool_choice="none", params=turn.params, stream=True
            ):
                if isinstance(ev, TextDelta):
                    buffer.append(ev.text)
                    yield _event("delta", text=ev.text)
                elif isinstance(ev, UsageInfo):
                    tokens_in, tokens_out = ev.input_tokens, ev.output_tokens
                elif isinstance(ev, StreamError):
                    # partial 落庫失敗只 log;原 provider 錯誤仍為唯一終端事件(§8 修訂)
                    try:
                        await self._persist_error(ctx, conversation_id, turn, buffer, ev.code)
                    except Exception:
                        self._log_persist_failed("stream_error", conversation_id, turn)
                    yield _event(
                        "error", code=ev.code, message=ev.message, trace_id=ctx.trace_id
                    )
                    return
                elif isinstance(ev, StreamStop):
                    stop_reason = ev.stop_reason
                    break
        except GeneratorExit:
            # 生成器關閉(aclose):呼叫端會等待 cleanup,直接 await 即可完成 commit。
            # 落庫失敗只 log,NEVER 洩漏出 aclose(§8 修訂)。
            try:
                await self._persist_cancelled(ctx, conversation_id, turn, buffer)
            except Exception:
                self._log_persist_failed("cancelled", conversation_id, turn)
            raise
        except asyncio.CancelledError:
            # 客端斷線:本任務被取消,若在此 await 會被連鎖取消切斷而漏存;
            # 故把 partial 落庫脫離為背景任務(不隨請求任務被取消),確保 commit(§8)。
            _spawn_background(
                self._persist_cancelled_guarded(ctx, conversation_id, turn, buffer)
            )
            raise

        latency_ms = int((time.perf_counter() - t0) * 1000)
        try:
            await self._persist_success(
                ctx, conversation_id, turn, buffer, tokens_in, tokens_out, latency_ms
            )
        except Exception:
            # TXN B 失敗 NEVER 讓串流無終端事件斷線:發 error(internal_error) 收尾
            # (§8/§10.4 2026-07-16 修訂;internal_error 為應用層碼,非 ProviderErrorCode)
            self._log_persist_failed("success", conversation_id, turn)
            yield _event(
                "error",
                code="internal_error",
                message=_PERSIST_FAILED_MESSAGE,
                trace_id=ctx.trace_id,
            )
            return
        yield _event(
            "done",
            message_id=str(turn.assistant_message_id),
            finish_reason=_FINISH_REASON.get(stop_reason, "stop"),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
        )

    # ── 交易片段 ──────────────────────────────────────────────────────────────
    async def _prepare_turn(
        self,
        ctx: AuthContext,
        conversation_id: UUID,
        content: str,
        client_message_id: UUID | None,
        source_ids: Sequence[UUID] | None,
    ) -> _TurnContext:
        async with self._session_factory() as session:
            conversations = ConversationRepository(session)
            messages = MessageRepository(session)
            conv = await conversations.get_owned(ctx.user_id, conversation_id)
            if conv is None:
                # 查無 / 無權一律 404,NEVER 用 403 洩漏存在性(§5.3-3)
                raise ConversationNotFound()

            # 來源存在性 / ownership 於串流前檢查 → 404 走一般 JSON(§10.1)
            await self._assert_sources_owned(session, ctx, source_ids)

            user_message_id = new_id()
            try:
                await messages.add(
                    Message(
                        id=user_message_id,
                        conversation_id=conversation_id,
                        role="user",
                        content=content,
                        client_message_id=client_message_id,
                    )
                )
            except IntegrityError as exc:
                await session.rollback()
                raise DuplicateMessage() from exc

            history = await messages.list_recent(
                conversation_id, limit=self._settings.chat_history_max_messages
            )
            context = [
                ChatMessage(role=cast(Role, m.role), content=m.content) for m in history
            ]
            resolved = resolve(conv.model_alias)
            params = ModelParams(
                model=resolved.model,
                temperature=resolved.temperature,
                max_tokens=resolved.max_tokens,
            )
            title_is_none = conv.title is None
            await session.commit()

        return _TurnContext(
            user_message_id=user_message_id,
            assistant_message_id=new_id(),
            history=context,
            system_prompt=self._settings.chat_system_prompt,
            params=params,
            title_is_none=title_is_none,
            source_ids=source_ids,
        )

    async def _assert_sources_owned(
        self, session: AsyncSession, ctx: AuthContext, source_ids: Sequence[UUID] | None
    ) -> None:
        if not source_ids:  # None(純聊天)與 []( 不限來源)皆無需檢查
            return
        sources = KnowledgeSourceRepository(session)
        for source_id in source_ids:
            if await sources.get_owned(ctx.user_id, source_id) is None:
                raise SourceNotFound()

    # ── 檢索(§9.1、§10.2)────────────────────────────────────────────────────
    async def _retrieve(
        self, ctx: AuthContext, content: str, turn: _TurnContext
    ) -> list[RetrievedChunk]:
        """檢索 query = 本次 user content 原文(多輪指代改寫列 backlog,v1.2 §10 補遺)。"""
        chunks = await self._retrieval.retrieve(
            ctx,
            content,
            source_ids=turn.source_ids or None,  # [] = 不限來源
            top_n=self._settings.retrieval_top_n,
        )
        # 先依 char budget 裁定實際採用的 blocks,再由回傳值產 citations(同一份清單)
        turn.system_prompt, used = rag_system_prompt(
            self._settings.chat_system_prompt,
            chunks,
            char_budget=self._settings.rag_context_char_budget,
        )
        return used

    async def _persist_success(
        self,
        ctx: AuthContext,
        conversation_id: UUID,
        turn: _TurnContext,
        buffer: list[str],
        tokens_in: int | None,
        tokens_out: int | None,
        latency_ms: int,
    ) -> None:
        async with self._session_factory() as session:
            session.add(
                Message(
                    id=turn.assistant_message_id,
                    conversation_id=conversation_id,
                    role="assistant",
                    content="".join(buffer),
                    content_meta=turn.content_meta(),
                    provider=self._llm.name,
                    model=turn.params.model,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    latency_ms=latency_ms,
                )
            )
            self._add_citations(session, turn)
            await ConversationRepository(session).bump_updated_at(conversation_id)
            await UsageRepository(session).add(
                self._usage_log(
                    ctx, conversation_id, turn, tokens_in, tokens_out, latency_ms, status="ok"
                )
            )
            await session.commit()

        if turn.title_is_none:
            self._task_queue.enqueue_generate_title(conversation_id)

    async def _persist_error(
        self,
        ctx: AuthContext,
        conversation_id: UUID,
        turn: _TurnContext,
        buffer: list[str],
        error_code: str,
    ) -> None:
        async with self._session_factory() as session:
            if buffer:
                # partial 路徑一併落 message_citations(v1.2 §10 補遺);
                # 無 partial 訊息時 NEVER 落 citations(message_id 為 FK,無訊息即無引用)。
                session.add(
                    self._assistant_partial(
                        conversation_id, turn, buffer, {"partial": True, "error_code": error_code}
                    )
                )
                self._add_citations(session, turn)
            await ConversationRepository(session).bump_updated_at(conversation_id)
            await UsageRepository(session).add(
                self._usage_log(
                    ctx, conversation_id, turn, None, None, None, status="error",
                    error_code=error_code,
                )
            )
            await session.commit()

    async def _persist_cancelled_guarded(
        self,
        ctx: AuthContext,
        conversation_id: UUID,
        turn: _TurnContext,
        buffer: list[str],
    ) -> None:
        # 背景任務無人 await 例外:失敗必須在此 log,否則靜默消失(§8 修訂)。
        try:
            await self._persist_cancelled(ctx, conversation_id, turn, buffer)
        except Exception:
            self._log_persist_failed("cancelled", conversation_id, turn)

    def _log_persist_failed(
        self, phase: str, conversation_id: UUID, turn: _TurnContext
    ) -> None:
        _logger.error(
            "chat_persist_failed",
            phase=phase,
            conversation_id=str(conversation_id),
            assistant_message_id=str(turn.assistant_message_id),
            exc_info=True,
        )

    async def _persist_cancelled(
        self,
        ctx: AuthContext,
        conversation_id: UUID,
        turn: _TurnContext,
        buffer: list[str],
    ) -> None:
        if not buffer:
            return
        async with self._session_factory() as session:
            session.add(self._assistant_partial(conversation_id, turn, buffer, {"partial": True}))
            self._add_citations(session, turn)
            await ConversationRepository(session).bump_updated_at(conversation_id)
            await UsageRepository(session).add(
                self._usage_log(ctx, conversation_id, turn, None, None, None, status="ok")
            )
            await session.commit()

    async def _persist_error_guarded(
        self,
        ctx: AuthContext,
        conversation_id: UUID,
        turn: _TurnContext,
        buffer: list[str],
        error_code: str,
    ) -> None:
        # 檢索失敗路徑:usage 落庫失敗 NEVER 蓋掉原始錯誤事件(同 §8 StreamError 處理)。
        try:
            await self._persist_error(ctx, conversation_id, turn, buffer, error_code)
        except Exception:
            self._log_persist_failed("retrieval_error", conversation_id, turn)

    # ── 建構器小工具 ──────────────────────────────────────────────────────────
    def _assistant_partial(
        self,
        conversation_id: UUID,
        turn: _TurnContext,
        buffer: list[str],
        meta: dict[str, object],
    ) -> Message:
        return Message(
            id=turn.assistant_message_id,
            conversation_id=conversation_id,
            role="assistant",
            content="".join(buffer),
            content_meta=turn.content_meta(meta),
            provider=self._llm.name,
            model=turn.params.model,
        )

    def _add_citations(self, session: AsyncSession, turn: _TurnContext) -> None:
        """快照落庫(D7):與 assistant message 同一 TXN B;chunk/document 為軟引用。"""
        MessageCitationRepository(session).add_all(
            [
                MessageCitation(
                    message_id=turn.assistant_message_id,
                    chunk_id=chunk.chunk_id,
                    document_id=chunk.document_id,
                    filename=chunk.filename,
                    snippet=chunk.text[:SNIPPET_MAX_CHARS],
                    score=chunk.score,
                    rank=rank,
                )
                for rank, chunk in enumerate(turn.citations, start=1)
            ]
        )

    def _usage_log(
        self,
        ctx: AuthContext,
        conversation_id: UUID,
        turn: _TurnContext,
        tokens_in: int | None,
        tokens_out: int | None,
        latency_ms: int | None,
        *,
        status: str,
        error_code: str | None = None,
    ) -> ModelUsageLog:
        return ModelUsageLog(
            user_id=ctx.user_id,
            conversation_id=conversation_id,
            message_id=turn.assistant_message_id,
            provider=self._llm.name,
            model=turn.params.model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            status=status,
            error_code=error_code,
        )


def _event(name: str, **data: object) -> dict[str, object]:
    return {"event": name, "data": data}


def _citation_items(chunks: list[RetrievedChunk]) -> list[dict[str, Any]]:
    """SSE `citations` 事件 payload(§10.3);與落庫快照、CitationOut 欄位一致。"""
    return [
        {
            "rank": rank,
            "chunk_id": str(chunk.chunk_id),
            "document_id": str(chunk.document_id),
            "filename": chunk.filename,
            "snippet": chunk.text[:SNIPPET_MAX_CHARS],
            "score": chunk.score,
        }
        for rank, chunk in enumerate(chunks, start=1)
    ]
