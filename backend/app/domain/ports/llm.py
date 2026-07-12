"""`LLMProvider` port 與 `StreamEvent`(跨供應商唯一契約)。

凍結契約 = **phase-6-spec §3 五型別版**(MASTER_PLAN_v1 §R R1):自 P1 起即實作最終形,
P6 不做介面遷移,僅實際啟用 tool 事件與其他 adapter。

事件順序契約:0+ 個 `TextDelta`(或 `ToolCallRequest`)→ 0..1 個 `UsageInfo`
→ 恰好 1 個終端事件(`StreamStop` 或 `StreamError`)。消費端 MUST 忽略未知 type
(前向相容規則)。

P1 範圍:`tools=None`、`tool_choice="none"`、`stream=True`;`ToolCallRequest` 不會發生。
本檔為純 domain,NEVER import 任何框架 / SDK(MASTER_PLAN_v1 §C.2)。
"""
from collections.abc import AsyncIterator
from typing import Any, Literal, Protocol

from pydantic import BaseModel

Role = Literal["system", "user", "assistant", "tool"]


class ChatMessage(BaseModel):
    role: Role
    content: str


class ModelParams(BaseModel):
    """解析 alias(models.yaml)後傳入 adapter 的模型參數。`model` = 實際 model id。"""

    model: str
    temperature: float = 0.7
    max_tokens: int | None = None


class ToolSpec(BaseModel):
    """由 `ToolDefinition.args_model.model_json_schema()` 導出(P6);P1 不使用。"""

    name: str
    description: str
    json_schema: dict[str, Any]


# ── StreamEvent 五型別(凍結) ────────────────────────────────────────────────
class TextDelta(BaseModel):
    type: Literal["text_delta"] = "text_delta"
    text: str


class ToolCallRequest(BaseModel):
    """adapter 已聚合完成的完整呼叫,非片段;解析與驗證是 Dispatcher 的責任(P6)。"""

    type: Literal["tool_call"] = "tool_call"
    call_id: str
    name: str
    arguments_json: str


class UsageInfo(BaseModel):
    """僅在 provider 回報時發出(0..1 次,R7);欄位為必填 int,NEVER 自行估算。"""

    type: Literal["usage"] = "usage"
    input_tokens: int
    output_tokens: int


class StreamStop(BaseModel):
    type: Literal["stop"] = "stop"
    stop_reason: Literal["end_turn", "tool_use", "max_tokens"]


ProviderErrorCode = Literal[
    "rate_limited", "auth", "context_length", "transient", "provider_error"
]


class StreamError(BaseModel):
    type: Literal["error"] = "error"
    code: ProviderErrorCode
    message: str


StreamEvent = TextDelta | ToolCallRequest | UsageInfo | StreamStop | StreamError


class LLMProvider(Protocol):
    """事件順序契約見模組 docstring。P6 會擴充 tool_call 事件,消費端 MUST 忽略未知 type。"""

    name: str

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None,
        tool_choice: Literal["auto", "none"],
        params: ModelParams,
        stream: bool,
    ) -> AsyncIterator[StreamEvent]:  # 以 async generator 實作即符合此簽章
        ...
