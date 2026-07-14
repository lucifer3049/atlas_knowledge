"""system prompt 模板集中管理(MASTER_PLAN_v1 §C.5.9);變更走 code review,NEVER 散落各處。

P1 僅標題生成 prompt;chat RAG 指示、通路差異段等於後續 Phase 加入本檔。
"""
from app.domain.ports.llm import ChatMessage

_TITLE_SYSTEM = (
    "你是為對話生成標題的助理。根據使用者的第一則訊息與助理的回覆,"
    "產出一個不超過 20 字、精準概括主題的繁體中文標題。"
    "只輸出標題本身,不要加引號、句末標點或任何多餘說明。"
)


def title_prompt(user_content: str, assistant_content: str) -> list[ChatMessage]:
    return [
        ChatMessage(role="system", content=_TITLE_SYSTEM),
        ChatMessage(
            role="user",
            content=f"使用者:{user_content}\n助理:{assistant_content}",
        ),
    ]
