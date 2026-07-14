"""共用組裝(API deps 與 worker 共用;§C.2、PHASE_1 v1.2 §22)。

adapter 組裝邏輯集中於此,NEVER 在 deps 與 worker 各複製一份。P1 僅 LLM adapter。
"""
from app.core.config import Settings
from app.core.model_registry import default_alias, resolve
from app.domain.ports.llm import LLMProvider
from app.infrastructure.llm.openai_compat import OpenAICompatProvider


def build_llm(settings: Settings) -> LLMProvider:
    """由 default alias 組 LLM adapter(§R R2)。連線層(base_url/api_key/timeout)取自
    settings;model 名於呼叫端依 conversation.model_alias 解析。多 provider 為 P6 ModelRouter。"""
    resolved = resolve(default_alias())
    if resolved.provider != "openai_compat":
        raise RuntimeError(f"P1 僅支援 openai_compat provider,取得 {resolved.provider!r}")
    return OpenAICompatProvider(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        timeout_s=settings.llm_timeout_s,
    )
