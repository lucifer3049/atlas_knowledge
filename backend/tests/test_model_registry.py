"""model_registry 測試(T1.4 resolve;review Q2:alias 自 yaml 移除的防護)。"""
from structlog.testing import capture_logs

from app.core.model_registry import default_alias, resolve


def test_resolve_known_alias() -> None:
    resolved = resolve(default_alias())
    assert resolved.provider == "openai_compat"
    assert resolved.model != ""


def test_resolve_unknown_alias_falls_back_to_default() -> None:
    # 舊 conversation 引用的 alias 被自 models.yaml 移除 → NEVER 500;
    # fallback 到 default_alias(與 phase-6 解析優先序同語意)並 log warning。
    with capture_logs() as logs:
        resolved = resolve("removed-alias")
    assert resolved == resolve(default_alias())
    entry = next(e for e in logs if e["event"] == "model_alias_fallback")
    assert entry["alias"] == "removed-alias"
