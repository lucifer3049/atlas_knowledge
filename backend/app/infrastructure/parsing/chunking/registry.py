"""Chunking strategy registry(PHASE_2 §7 末)。

P2 一律 `default_recursive`;P3 的 `law_hierarchical` 由此插入,呼叫端不改一行。
"""
from app.domain.ports.chunking import ChunkingStrategy
from app.infrastructure.parsing.chunking.default_recursive import DefaultRecursiveChunking
from app.infrastructure.parsing.chunking.table_row import TableRowChunking

DEFAULT_STRATEGY = "default_recursive"

_STRATEGIES: dict[str, ChunkingStrategy] = {
    strategy.name: strategy
    for strategy in (DefaultRecursiveChunking(), TableRowChunking())
}


def get_strategy(name: str = DEFAULT_STRATEGY) -> ChunkingStrategy:
    strategy = _STRATEGIES.get(name)
    if strategy is None:
        raise KeyError(f"未註冊的 chunking strategy: {name}")
    return strategy


def strategy_for_source_type(source_type: str) -> ChunkingStrategy:
    """依 knowledge_source.type 選策略;P2 只有 'upload',一律 default_recursive。"""
    return get_strategy(DEFAULT_STRATEGY)
