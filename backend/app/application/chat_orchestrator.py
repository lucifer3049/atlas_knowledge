"""ChatOrchestrator:使用者提問 → 串流回答核心迴路(PHASE_1 §8、T1.4)。

自管短交易(§D5):TXN A 載入/插入 user message、讀上下文 → commit 釋放連線 →
**LLM 串流期間 NEVER 持有 DB 連線** → TXN B 落 assistant message + usage log。
對外 yield「應用層事件 dict」;SSE 序列化只在 router。

事件順序(§9):message_start → delta* → (done | error)。
"""
import asyncio
import time
from collections.abc import AsyncGenerator
from typing import cast
from uuid import UUID

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

# StreamStop.stop_reason → SSE done.finish_reason(§H.3;tool_use 於 P1 不觸發)
_FINISH_REASON = {"end_turn": "stop", "max_tokens": "length"}


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
                    await self._persist_error(ctx, conversation_id, turn, buffer, ev.code)
                    yield _event(
                        "error", code=ev.code, message=ev.message, trace_id=ctx.trace_id
                    )
                    return
                elif isinstance(ev, StreamStop):
                    stop_reason = ev.stop_reason
                    break
        except (asyncio.CancelledError, GeneratorExit):
            # 客端斷線 / 生成器關閉:持久化 partial(語意 aborted)後 re-raise(§8)
            await self._persist_cancelled(ctx, conversation_id, turn, buffer)
            raise

        latency_ms = int((time.perf_counter() - t0) * 1000)
        await self._persist_success(
            ctx, conversation_id, turn, buffer, tokens_in, tokens_out, latency_ms
        )
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
