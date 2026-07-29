"""system prompt 模板集中管理(MASTER_PLAN_v1 §C.5.9);變更走 code review,NEVER 散落各處。

P1 標題生成 prompt;T2.6 加入 RAG 指示與 context blocks 組裝(§10.2)。
通路差異段等於後續 Phase 加入本檔。
"""
from app.domain.entities.chunk import RetrievedChunk
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


# --- RAG(T2.6;§10.2)--------------------------------------------------------

_RAG_INSTRUCTION = (
    "僅依據下列資料回答使用者的問題;資料不足以回答時明白說出「提供的資料不足」,"
    "NEVER 自行編造或補充資料以外的內容。引用時在句末以 [n] 標註對應資料編號。"
)
_RAG_EMPTY = "(這次沒有檢索到相關資料)"


def _render_block(chunk: RetrievedChunk, rank: int) -> str:
    """`[n] 檔名｜heading_path\\n內容`(§10.2)。"""
    headings = chunk.meta.get("heading_path") or []
    path = " > ".join(str(h) for h in headings) if isinstance(headings, list) else ""
    title = f"{chunk.filename}｜{path}" if path else chunk.filename
    return f"[{rank}] {title}\n{chunk.text}"


def rag_system_prompt(
    base: str, chunks: list[RetrievedChunk], *, char_budget: int
) -> tuple[str, list[RetrievedChunk]]:
    """組「基底 + RAG 指示 + context blocks」,並回傳**實際採用**的 chunks。

    採用清單 MUST 同時作為 citations 的來源(v1.2 §10 補遺:兩者為同一份清單),
    故先依 `char_budget` 裁定再回傳。裁切為前綴語意:第一個放不下的 block 即停止,
    名次因此保持連續(1..N)。
    """
    blocks: list[str] = []
    used: list[RetrievedChunk] = []
    remaining = char_budget
    for chunk in chunks:
        block = _render_block(chunk, len(used) + 1)
        if len(block) > remaining:
            break
        remaining -= len(block)
        blocks.append(block)
        used.append(chunk)

    context = "\n\n".join(blocks) if blocks else _RAG_EMPTY
    return f"{base}\n\n{_RAG_INSTRUCTION}\n\n{context}", used
