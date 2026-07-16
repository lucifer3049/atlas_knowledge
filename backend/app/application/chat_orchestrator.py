"""ChatOrchestrator:使用者提問 → 串流回答核心迴路(PHASE_1 §8、T1.4)。

自管短交易(§D5):TXN A 載入/插入 user message、讀上下文 → commit 釋放連線 →
**LLM 串流期間 NEVER 持有 DB 連線** → TXN B 落 assistant message + usage log。
對外 yield「應用層事件 dict」;SSE 序列化只在 router。

事件順序(§9):message_start → delta* → (done | error)。
"""
import asyncio
import time
from collections.abc import AsyncGenerator, Coroutine
from typing import cast
from uuid import UUID

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import ConversationNotFound, DuplicateMessage
from app.core.ids import new_id
from app.core.model_registry import resolve
from app.domain.entities.auth_context import AuthContext
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
from app.infrastructure.db.models import Message, ModelUsageLog
from app.infrastructure.db.repositories.conversations import ConversationRepository
from app.infrastructure.db.repositories.messages import MessageRepository
from app.infrastructure.db.repositories.usage import UsageRepository

_logger = structlog.get_logger()

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
    """TXN A 的產出:串流所需、且已與 DB session 脫鉤的純資料。"""

    def __init__(
        self,
        *,
        user_message_id: UUID,
        assistant_message_id: UUID,
        prompt: list[ChatMessage],
        params: ModelParams,
        title_is_none: bool,
    ) -> None:
        self.user_message_id = user_message_id
        self.assistant_message_id = assistant_message_id
        self.prompt = prompt
        self.params = params
        self.title_is_none = title_is_none


class ChatOrchestrator:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        llm: LLMProvider,
        settings: Settings,
        task_queue: TaskQueue,
    ) -> None:
        self._session_factory = session_factory
        self._llm = llm
        self._settings = settings
        self._task_queue = task_queue

    async def stream_reply(
        self,
        ctx: AuthContext,
        conversation_id: UUID,
        content: str,
        client_message_id: UUID | None,
    ) -> AsyncGenerator[dict[str, object], None]:
        # 1. TXN A:載入 + 插 user message + 讀上下文(可能 raise 404 / 409,皆於串流前)
        turn = await self._prepare_turn(ctx, conversation_id, content, client_message_id)

        yield _event(
            "message_start",
            user_message_id=str(turn.user_message_id),
            assistant_message_id=str(turn.assistant_message_id),
        )

        # 2. 串流(NEVER 持有 DB 連線)
        buffer: list[str] = []
        tokens_in: int | None = None
        tokens_out: int | None = None
        stop_reason = "end_turn"
        t0 = time.perf_counter()
        try:
            async for ev in self._llm.chat(
                turn.prompt, tools=None, tool_choice="none", params=turn.params, stream=True
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
    ) -> _TurnContext:
        async with self._session_factory() as session:
            conversations = ConversationRepository(session)
            messages = MessageRepository(session)
            conv = await conversations.get_owned(ctx.user_id, conversation_id)
            if conv is None:
                # 查無 / 無權一律 404,NEVER 用 403 洩漏存在性(§5.3-3)
                raise ConversationNotFound()

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
            prompt = [ChatMessage(role="system", content=self._settings.chat_system_prompt)]
            prompt += [
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
            prompt=prompt,
            params=params,
            title_is_none=title_is_none,
        )

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
                    provider=self._llm.name,
                    model=turn.params.model,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    latency_ms=latency_ms,
                )
            )
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
                session.add(
                    self._assistant_partial(
                        conversation_id, turn, buffer, {"partial": True, "error_code": error_code}
                    )
                )
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
            await ConversationRepository(session).bump_updated_at(conversation_id)
            await UsageRepository(session).add(
                self._usage_log(ctx, conversation_id, turn, None, None, None, status="ok")
            )
            await session.commit()

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
            content_meta=meta,
            provider=self._llm.name,
            model=turn.params.model,
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
