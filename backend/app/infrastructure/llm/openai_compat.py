"""OpenAI-compatible streaming adapter(PHASE_1 §7;一個 adapter 打多家:OpenAI / Ollama /
本地推理伺服器)。

實作 `domain.ports.llm.LLMProvider`。供應商差異(欄位名、事件格式)一律於此吸收,
NEVER 讓 httpx 例外洩漏到 application 層——所有錯誤映射為 `StreamError` 五類後 yield。

錯誤映射(§7):

| 來源                                   | code            |
|----------------------------------------|-----------------|
| HTTP 401 / 403                         | auth            |
| HTTP 429                               | rate_limited    |
| HTTP 400 且 body 含 context/length 字樣 | context_length  |
| HTTP 5xx / timeout / 連線 / 串流中斷    | transient       |
| 其他(JSON 解析失敗等)                 | provider_error  |

P1 範圍:`tools=None`、`tool_choice="none"`、`stream=True`;不做自動重試(backlog:
pre-stream transient 單次重試)。tool 序列化與 tool_call 解析為 P6(phase-6 §7.4)。
"""
import json
import time
from collections.abc import AsyncIterator
from typing import Literal

import httpx
import structlog

from app.domain.ports.llm import (
    ChatMessage,
    ModelParams,
    ProviderErrorCode,
    StreamError,
    StreamEvent,
    StreamStop,
    TextDelta,
    ToolSpec,
    UsageInfo,
)

_logger = structlog.get_logger()

StopReason = Literal["end_turn", "tool_use", "max_tokens"]

_DATA_PREFIX = "data:"

# OpenAI finish_reason → StreamStop.stop_reason(§6 R1 映射;tool_calls 於 P1 不觸發)
_FINISH_MAP: dict[str, StopReason] = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
}

# 對使用者呈現的固定訊息(繁中);原始例外細節只進 log
_ERROR_MESSAGES: dict[ProviderErrorCode, str] = {
    "auth": "上游服務驗證失敗",
    "rate_limited": "上游服務忙碌中,請稍後再試",
    "context_length": "對話內容過長,超出模型上限",
    "transient": "上游服務暫時無法回應,請稍後再試",
    "provider_error": "上游服務回應異常",
}


class OpenAICompatProvider:
    name = "openai_compat"

    def __init__(self, *, base_url: str, api_key: str, timeout_s: float) -> None:
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(connect=10, read=timeout_s, write=10, pool=10),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None,
        tool_choice: Literal["auto", "none"],
        params: ModelParams,
        stream: bool,
    ) -> AsyncIterator[StreamEvent]:
        # P1:tools 恆為 None;tool 序列化為 P6(phase-6 §7.4),此處不提前實作。
        body: dict[str, object] = {
            "model": params.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": params.temperature,
            "stream": True,
            "stream_options": {"include_usage": True},  # provider 不支援時自動忽略
        }
        if params.max_tokens is not None:
            body["max_tokens"] = params.max_tokens

        t0 = time.perf_counter()
        emitted_error: ProviderErrorCode | None = None
        try:
            async for ev in self._stream(body):
                if isinstance(ev, StreamError):
                    emitted_error = ev.code
                yield ev
        except httpx.TimeoutException:
            emitted_error = "transient"
            yield self._error("transient")
        except httpx.HTTPError:
            # 連線 / 協定 / 串流中斷等傳輸層錯誤
            emitted_error = "transient"
            yield self._error("transient")
        except Exception:  # 兜底:JSON 解析等非傳輸層錯誤,NEVER 洩漏到呼叫端
            emitted_error = "provider_error"
            yield self._error("provider_error")
        finally:
            _logger.info(
                "llm.chat",
                provider=self.name,
                model=params.model,
                status="ok" if emitted_error is None else "error",
                error_code=emitted_error,
                duration_ms=int((time.perf_counter() - t0) * 1000),
            )

    async def _stream(self, body: dict[str, object]) -> AsyncIterator[StreamEvent]:
        """實際串流解析;傳輸層例外向上拋給 `chat` 統一映射。"""
        async with self._client.stream("POST", self._url, json=body) as resp:
            if resp.status_code >= 400:
                raw = await resp.aread()
                yield self._error(self._map_http_status(resp.status_code, raw))
                return

            stop_reason: StopReason = "end_turn"
            usage_event: UsageInfo | None = None
            saw_done = False
            async for raw_line in resp.aiter_lines():
                line = raw_line.strip()
                if not line.startswith(_DATA_PREFIX):
                    continue
                payload = line[len(_DATA_PREFIX) :].strip()
                if payload == "[DONE]":
                    saw_done = True
                    break
                obj = json.loads(payload)
                choices = obj.get("choices") or []
                if choices:
                    delta = choices[0].get("delta") or {}
                    content = delta.get("content")
                    if content:
                        yield TextDelta(text=content)
                    mapped = _FINISH_MAP.get(choices[0].get("finish_reason"))
                    if mapped is not None:
                        stop_reason = mapped
                usage = obj.get("usage")
                if usage:
                    usage_event = UsageInfo(
                        input_tokens=usage["prompt_tokens"],
                        output_tokens=usage["completion_tokens"],
                    )

        # 未見 [DONE] 即串流結束 = 途中斷線,映射 transient(§7 測試)
        if not saw_done:
            yield self._error("transient")
            return
        # 順序契約:usage(0..1)在終端 stop 之前
        if usage_event is not None:
            yield usage_event
        yield StreamStop(stop_reason=stop_reason)

    @staticmethod
    def _map_http_status(status: int, raw: bytes) -> ProviderErrorCode:
        if status in (401, 403):
            return "auth"
        if status == 429:
            return "rate_limited"
        if status == 400:
            text = raw.decode("utf-8", "ignore").lower()
            if "context" in text or "length" in text:
                return "context_length"
            return "provider_error"
        if status >= 500:
            return "transient"
        return "provider_error"

    @staticmethod
    def _error(code: ProviderErrorCode) -> StreamError:
        return StreamError(code=code, message=_ERROR_MESSAGES[code])
