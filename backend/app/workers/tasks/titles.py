"""對話標題生成任務(T1.7;PHASE_1 §14 T1.7、§F.3)。

取首輪 user + assistant → LLM 產 ≤20 字繁中標題 → `UPDATE ... WHERE title IS NULL`
(防 race)。sync 任務本體以單一 `run_async()` 包 async;失敗只 log warning,
NEVER 重試迴圈、NEVER 拋出。`_generate_title` 為可測純邏輯(fake LLM + 測試 DB)。
"""
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.prompts import title_prompt
from app.core.config import settings
from app.core.db import create_engine
from app.core.model_registry import resolve
from app.core.wiring import build_llm
from app.domain.ports.llm import LLMProvider, ModelParams, StreamError, TextDelta
from app.infrastructure.db.repositories.conversations import ConversationRepository
from app.infrastructure.db.repositories.messages import MessageRepository
from app.infrastructure.db.session import create_session_factory
from app.workers.celery_app import celery_app
from app.workers.run_async import run_async

_logger = structlog.get_logger()
_TITLE_MAX_CHARS = 20
_TITLE_TEMPERATURE = 0.3
_TITLE_MAX_TOKENS = 64  # PHASE_1 v1.2 §22
_STRIP_CHARS = " \t\r\n「」『』\"'“”‘’"


def _clean(raw: str) -> str:
    stripped = raw.strip()
    if not stripped:
        return ""
    first_line = stripped.splitlines()[0].strip(_STRIP_CHARS)
    return first_line[:_TITLE_MAX_CHARS]


async def _generate_title(
    conversation_id: UUID,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    llm: LLMProvider,
) -> None:
    async with session_factory() as session:
        conv = await ConversationRepository(session).get(conversation_id)
        if conv is None or conv.title is not None:
            return  # 對話已刪 / 標題已存在 → 不覆寫
        messages = MessageRepository(session)
        user_msg = await messages.first_of_role(conversation_id, "user")
        assistant_msg = await messages.first_of_role(conversation_id, "assistant")
        model_alias = conv.model_alias
    if user_msg is None or assistant_msg is None:
        return  # 尚無完整首輪

    resolved = resolve(model_alias)
    params = ModelParams(
        model=resolved.model, temperature=_TITLE_TEMPERATURE, max_tokens=_TITLE_MAX_TOKENS
    )
    parts: list[str] = []
    async for ev in llm.chat(
        title_prompt(user_msg.content, assistant_msg.content),
        tools=None,
        tool_choice="none",
        params=params,
        stream=True,
    ):
        if isinstance(ev, TextDelta):
            parts.append(ev.text)
        elif isinstance(ev, StreamError):
            _logger.warning(
                "title_llm_error", conversation_id=str(conversation_id), code=ev.code
            )
            return  # LLM 失敗不拋出、不重試

    title = _clean("".join(parts))
    if not title:
        return
    async with session_factory() as session:
        await ConversationRepository(session).set_title_if_null(conversation_id, title)
        await session.commit()


async def _run_title(conversation_id: UUID) -> None:
    # worker 內建立 engine/adapter(綁定當前 asyncio.run loop);用完即棄。
    engine = create_engine()
    session_factory = create_session_factory(engine)
    llm = build_llm(settings)
    try:
        await _generate_title(conversation_id, session_factory=session_factory, llm=llm)
    finally:
        aclose = getattr(llm, "aclose", None)
        if aclose is not None:
            await aclose()
        await engine.dispose()


# ignore_result=True:fire-and-forget,結果 NEVER 佔用 result backend
@celery_app.task(name="generate_title", ignore_result=True)  # type: ignore[untyped-decorator]  # celery 未附型別
def generate_title(conversation_id: str) -> None:
    try:
        run_async(_run_title(UUID(conversation_id)))
    except Exception:
        # 標題生成為 best-effort:任何失敗只 log(含 exc_info 供除錯),NEVER 進入重試迴圈。
        _logger.warning(
            "title_generation_failed", conversation_id=conversation_id, exc_info=True
        )
