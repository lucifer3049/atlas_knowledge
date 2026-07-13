"""chat router:SSE 串流提問(PHASE_1 §9、T1.4)。

`POST /api/conversations/{id}/messages` 回 `text/event-stream`。
router 僅做:pre-stream 錯誤浮現(404/409/422 走一般 JSON)、把 orchestrator 的
應用層事件 dict 序列化為 SSE frame、每 15s 送 `: ping` 心跳。業務邏輯全在 orchestrator。
"""
import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.api.deps import get_auth, get_orchestrator
from app.api.schemas.conversations import ChatSendRequest
from app.application.chat_orchestrator import ChatOrchestrator
from app.domain.entities.auth_context import AuthContext

router = APIRouter(prefix="/conversations", tags=["chat"])

_HEARTBEAT_S = 15
_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _format(event: dict[str, object]) -> str:
    data = json.dumps(event["data"], ensure_ascii=False)
    return f"event: {event['event']}\ndata: {data}\n\n"


@router.post("/{conversation_id}/messages")
async def send_message(
    conversation_id: UUID,
    body: ChatSendRequest,
    auth: Annotated[AuthContext, Depends(get_auth)],
    orchestrator: Annotated[ChatOrchestrator, Depends(get_orchestrator)],
) -> StreamingResponse:
    agen = orchestrator.stream_reply(
        auth, conversation_id, body.content, body.client_message_id
    )
    # 先取第一個事件(message_start)以觸發 TXN A;串流前錯誤(ConversationNotFound /
    # DuplicateMessage)在此 raise → 走一般 JSON 錯誤 handler,不進 SSE(§9)。
    first = await agen.__anext__()

    async def source() -> AsyncIterator[str]:
        queue: asyncio.Queue[dict[str, object] | None] = asyncio.Queue()

        async def produce() -> None:
            try:
                async for ev in agen:
                    await queue.put(ev)
            finally:
                await queue.put(None)  # 結束哨兵

        task = asyncio.create_task(produce())
        try:
            yield _format(first)
            while True:
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=_HEARTBEAT_S)
                except TimeoutError:
                    yield ": ping\n\n"  # 心跳:防 proxy 閒置斷線
                    continue
                if ev is None:
                    break
                yield _format(ev)
        finally:
            # 客端斷線:取消 producer → orchestrator 收到 CancelledError 落 partial(§8)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    return StreamingResponse(source(), media_type="text/event-stream", headers=_SSE_HEADERS)
