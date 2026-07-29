"""FTS 斷詞(T2.4;PHASE_2 §8.2、§9.2)。

中文斷詞只用 jieba(§0-4)。**索引側與查詢側 MUST 用同一模式**(§9.2 R15):
一律 `jieba.cut_for_search`,再交給 PostgreSQL `to_tsvector('simple', ...)`(索引側)
或 `to_tsquery`(查詢側,T2.6)。此處只負責產生「以空白分隔的 token 字串」,
NEVER 直接下 SQL(SQL 只在 repository)。
"""
import jieba


def index_tokens(text: str) -> str:
    """索引側斷詞:回傳以空白分隔的 token 串,餵給 `to_tsvector('simple', ...)`。"""
    tokens = [tok.strip() for tok in jieba.cut_for_search(text)]
    return " ".join(tok for tok in tokens if tok)
