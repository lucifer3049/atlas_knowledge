"""T2.3 chunking 測試(純單元;PHASE_2 §12.2 chunking 列)。

涵蓋:token 估算、heading_path 堆疊、跨 heading 強制切分、超長 block 遞迴切、
overlap 生效、table 委派 table_row、seq 連續、meta.kind(R6)。
"""
import pytest

from app.domain.entities.chunk import ChunkDraft
from app.domain.entities.document import Block, NormalizedDocument
from app.domain.ports.chunking import ChunkingConfig
from app.infrastructure.parsing.chunking.default_recursive import DefaultRecursiveChunking
from app.infrastructure.parsing.chunking.registry import (
    DEFAULT_STRATEGY,
    get_strategy,
    strategy_for_source_type,
)
from app.infrastructure.parsing.chunking.table_row import pack_table
from app.infrastructure.parsing.chunking.tokens import estimate_tokens

_CFG = ChunkingConfig(target_tokens=450, overlap_tokens=80)


def _doc(*blocks: Block) -> NormalizedDocument:
    return NormalizedDocument(blocks=list(blocks))


def _chunk(*blocks: Block, cfg: ChunkingConfig = _CFG) -> list[ChunkDraft]:
    return DefaultRecursiveChunking().chunk(_doc(*blocks), cfg)


def _para(text: str, page: int | None = None) -> Block:
    return Block(type="paragraph", text=text, page=page)


# --- token 估算(§7)---------------------------------------------------------

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", 0),
        ("中文測試", 4),  # cjk 一字一 token
        ("abcd", 1),  # 非 cjk / 4
        ("abcde", 2),  # ceil
        ("中文abcd", 3),
        ("，。！", 3),  # 全形標點視為 cjk
    ],
)
def test_estimate_tokens(text: str, expected: int) -> None:
    assert estimate_tokens(text) == expected


# --- heading 與 heading_path -------------------------------------------------

def test_heading_path_stacks_by_level() -> None:
    chunks = _chunk(
        Block(type="heading", text="第一章", level=1),
        _para("章內容。"),
        Block(type="heading", text="第一節", level=2),
        _para("節內容。"),
    )
    assert [c.meta["heading_path"] for c in chunks] == [["第一章"], ["第一章", "第一節"]]


def test_same_level_heading_pops_previous() -> None:
    chunks = _chunk(
        Block(type="heading", text="第一章", level=1),
        _para("A。"),
        Block(type="heading", text="第二章", level=1),
        _para("B。"),
    )
    assert [c.meta["heading_path"] for c in chunks] == [["第一章"], ["第二章"]]


def test_deeper_heading_pops_back_to_ancestor() -> None:
    chunks = _chunk(
        Block(type="heading", text="章", level=1),
        Block(type="heading", text="節", level=2),
        _para("A。"),
        Block(type="heading", text="另一節", level=2),
        _para("B。"),
    )
    assert [c.meta["heading_path"] for c in chunks] == [["章", "節"], ["章", "另一節"]]


def test_heading_is_merged_into_following_content_not_its_own_chunk() -> None:
    chunks = _chunk(Block(type="heading", text="標題", level=1), _para("內容。"))
    assert len(chunks) == 1
    assert chunks[0].text == "標題\n內容。"


def test_heading_forces_split_even_below_target() -> None:
    # 兩段都很短,若非 heading 邊界會併成一塊
    chunks = _chunk(
        Block(type="heading", text="A", level=1),
        _para("短。"),
        Block(type="heading", text="B", level=1),
        _para("也短。"),
    )
    assert len(chunks) == 2


# --- 累積與切分 --------------------------------------------------------------

def test_short_blocks_accumulate_into_one_chunk() -> None:
    chunks = _chunk(_para("第一段。"), _para("第二段。"), _para("第三段。"))
    assert len(chunks) == 1
    assert chunks[0].text == "第一段。\n第二段。\n第三段。"


def test_accumulation_stops_at_target_tokens() -> None:
    cfg = ChunkingConfig(target_tokens=10, overlap_tokens=0)
    chunks = _chunk(_para("一二三四五。"), _para("六七八九十。"), cfg=cfg)
    assert len(chunks) == 2


def test_oversized_block_is_split_recursively_below_target() -> None:
    cfg = ChunkingConfig(target_tokens=20, overlap_tokens=0)
    sentence = "這是一個測試句子。"  # 9 tokens
    chunks = _chunk(_para(sentence * 10), cfg=cfg)
    assert len(chunks) > 1
    assert all(c.tokens <= cfg.target_tokens for c in chunks)


def test_split_without_punctuation_falls_back_to_midpoint() -> None:
    cfg = ChunkingConfig(target_tokens=10, overlap_tokens=0)
    chunks = _chunk(_para("字" * 40), cfg=cfg)
    assert all(c.tokens <= cfg.target_tokens for c in chunks)
    assert "".join(c.text for c in chunks) == "字" * 40  # 內容不遺漏


# --- overlap ----------------------------------------------------------------

def test_overlap_prefixes_tail_of_previous_chunk() -> None:
    # 每句 5 tokens;target 6 → 每句自成一塊,第二塊帶前一塊尾句作為重疊前綴
    cfg = ChunkingConfig(target_tokens=6, overlap_tokens=5)
    chunks = _chunk(_para("第一句話。"), _para("第二句話。"), cfg=cfg)
    assert chunks[0].text == "第一句話。"
    assert chunks[1].text == "第一句話。\n第二句話。"


def test_zero_overlap_produces_no_prefix() -> None:
    cfg = ChunkingConfig(target_tokens=6, overlap_tokens=0)
    chunks = _chunk(_para("第一句話。"), _para("第二句話。"), cfg=cfg)
    assert [c.text for c in chunks] == ["第一句話。", "第二句話。"]


def test_overlap_is_skipped_when_last_sentence_exceeds_budget() -> None:
    # 尾句 5 tokens > 預算 2 → 不重疊(NEVER 讓 overlap 撐大到接近 target)
    cfg = ChunkingConfig(target_tokens=6, overlap_tokens=2)
    chunks = _chunk(_para("第一句話。"), _para("第二句話。"), cfg=cfg)
    assert chunks[1].text == "第二句話。"


def test_overlap_never_crosses_heading_boundary() -> None:
    cfg = ChunkingConfig(target_tokens=6, overlap_tokens=5)
    chunks = _chunk(
        _para("第一句話。"),
        Block(type="heading", text="新章節", level=1),
        _para("第二句話。"),
        cfg=cfg,
    )
    assert chunks[1].text == "新章節\n第二句話。"


# --- table 委派(§7-5)------------------------------------------------------

def test_table_block_is_delegated_to_table_row() -> None:
    table = Block(type="table", rows=[["品名", "數量"], ["蘋果", "3"], ["香蕉", "5"]])
    chunks = _chunk(table)
    assert len(chunks) == 1
    assert chunks[0].text == "品名: 蘋果｜數量: 3\n品名: 香蕉｜數量: 5"
    assert chunks[0].meta["block_type"] == "table"


def test_table_is_not_merged_with_surrounding_text() -> None:
    chunks = _chunk(
        _para("前言。"),
        Block(type="table", rows=[["a", "b"], ["1", "2"]]),
        _para("後記。"),
    )
    assert [c.meta["block_type"] for c in chunks] == ["text", "table", "text"]


def test_table_rows_are_packed_up_to_target_tokens() -> None:
    cfg = ChunkingConfig(target_tokens=10, overlap_tokens=0)
    table = Block(type="table", rows=[["欄"], ["值一"], ["值二"], ["值三"]])
    packed = pack_table(table, cfg)
    assert len(packed) > 1


def test_header_only_table_still_produces_content() -> None:
    packed = pack_table(Block(type="table", rows=[["只有表頭", "第二欄"]]), _CFG)
    assert packed == ["只有表頭｜第二欄"]


def test_empty_table_produces_nothing() -> None:
    assert pack_table(Block(type="table", rows=[]), _CFG) == []


# --- meta / seq -------------------------------------------------------------

def test_seq_is_contiguous_from_zero() -> None:
    cfg = ChunkingConfig(target_tokens=10, overlap_tokens=0)
    chunks = _chunk(
        _para("一二三四五六七八九十。"),
        Block(type="table", rows=[["a"], ["1"], ["2"]]),
        _para("再來一段文字。"),
        cfg=cfg,
    )
    assert [c.seq for c in chunks] == list(range(len(chunks)))


def test_meta_kind_is_document_for_every_chunk() -> None:
    # R6:P2 chunking 即寫入 meta.kind,P3 的 backfill 因此為 no-op
    chunks = _chunk(_para("文字。"), Block(type="table", rows=[["a"], ["1"]]))
    assert {c.meta["kind"] for c in chunks} == {"document"}


def test_page_is_carried_from_first_block_of_chunk() -> None:
    chunks = _chunk(_para("第一頁內容。", page=3))
    assert chunks[0].meta["page"] == 3


def test_tokens_field_matches_estimate() -> None:
    chunks = _chunk(_para("一些內容。"))
    assert chunks[0].tokens == estimate_tokens(chunks[0].text)


def test_blocks_without_text_types_are_ignored() -> None:
    assert _chunk(Block(type="heading", text="只有標題", level=1)) == []


# --- registry ---------------------------------------------------------------

def test_registry_returns_default_strategy() -> None:
    assert get_strategy().name == DEFAULT_STRATEGY
    assert strategy_for_source_type("upload").name == DEFAULT_STRATEGY


def test_registry_rejects_unknown_strategy() -> None:
    with pytest.raises(KeyError):
        get_strategy("law_hierarchical")  # P3 才註冊
