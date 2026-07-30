"""RAG 評測腳本測試(T2.8;PHASE_2 §14)。

評測邏輯(命中判定、Hit@K、MRR、top-k 截斷)以 stub retrieve 純測;
「對 seed 資料可重現執行」則連測試 DB 走真實 hybrid 檢索(向量由 FakeEmbeddingProvider
產生,決定性)。CI NEVER 打真實 embedding API。
"""
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.entities.auth_context import AuthContext
from app.domain.entities.chunk import ChunkDraft, RetrievedChunk
from app.infrastructure.db.models import Document, KnowledgeSource, User
from app.infrastructure.db.repositories.chunks import ChunkRepository
from app.infrastructure.vector.pgvector_index import PgVectorChunkIndex
from scripts.rag_eval import (
    DEFAULT_GOLDEN,
    DEFAULT_TOP_K,
    GoldenCase,
    evaluate,
    format_report,
    load_golden,
    matches,
    parse_args,
)
from tests.fakes import FakeEmbeddingProvider, fake_retrieval

pytestmark = pytest.mark.anyio

_EMBEDDING = FakeEmbeddingProvider()


def _chunk(filename: str, text: str, rank: int) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid4(),
        document_id=uuid4(),
        filename=filename,
        text=text,
        score=1.0 / rank,
        rank=rank,
        meta={"kind": "document"},
    )


def _stub(chunks: list[RetrievedChunk]):  # type: ignore[no-untyped-def]
    async def retrieve(question: str, top_k: int) -> list[RetrievedChunk]:
        return chunks[:top_k]

    return retrieve


# --- 命中判定 ----------------------------------------------------------------

def test_keyword_must_appear_in_hit_chunk() -> None:
    case = GoldenCase("報銷期限?", "expense_policy.md", "三十日")
    assert matches(case, _chunk("expense_policy.md", "應於三十日內完成報銷", 1))
    # 檔案對但段落錯 NEVER 算命中
    assert not matches(case, _chunk("expense_policy.md", "住宿費上限為三千五百元", 1))
    assert not matches(case, _chunk("security_policy.md", "應於三十日內完成報銷", 1))


def test_keyword_optional() -> None:
    case = GoldenCase("報銷期限?", "expense_policy.md")
    assert matches(case, _chunk("expense_policy.md", "任何內容", 1))


# --- Hit@K / MRR -------------------------------------------------------------

async def test_hit_rank_and_metrics() -> None:
    cases = [
        GoldenCase("命中第一名", "a.md"),
        GoldenCase("命中第二名", "b.md"),
        GoldenCase("未命中", "z.md"),
    ]
    chunks = [_chunk("a.md", "甲", 1), _chunk("b.md", "乙", 2)]

    report = await evaluate(cases, _stub(chunks), top_k=5)

    assert [r.hit_rank for r in report.results] == [1, 2, None]
    assert report.hit_rate == pytest.approx(2 / 3)
    assert report.mrr == pytest.approx((1.0 + 0.5 + 0.0) / 3)


async def test_top_k_truncates_scoring_window() -> None:
    # --top-k 生效:排在第 2 名的正解在 top_k=1 時 NEVER 算命中
    cases = [GoldenCase("命中第二名", "b.md")]
    chunks = [_chunk("a.md", "甲", 1), _chunk("b.md", "乙", 2)]

    assert (await evaluate(cases, _stub(chunks), top_k=5)).hit_rate == 1.0
    tight = await evaluate(cases, _stub(chunks), top_k=1)
    assert tight.hit_rate == 0.0
    assert len(tight.results[0].chunks) == 1


async def test_empty_retrieval_is_a_miss_not_an_error() -> None:
    report = await evaluate([GoldenCase("查無資料", "a.md")], _stub([]), top_k=5)
    assert report.results[0].hit_rank is None
    assert report.hit_rate == 0.0
    assert "(無檢索結果)" in format_report(report)


async def test_format_report_shows_metrics_and_marks_hit() -> None:
    cases = [GoldenCase("命中第一名", "a.md")]
    report = await evaluate(cases, _stub([_chunk("a.md", "甲", 1)]), top_k=3)
    text = format_report(report)

    assert "Hit@3 = 1.00" in text
    assert "MRR = 1.000" in text
    assert "*1. a.md" in text  # 命中列有標記


# --- golden set 與 CLI -------------------------------------------------------

def test_shipped_golden_set_is_loadable_and_large_enough() -> None:
    cases = load_golden(DEFAULT_GOLDEN)
    assert len(cases) >= 10  # §14:golden set ≥ 10 題
    assert all(c.question and c.expected_document for c in cases)
    # 期望文件 MUST 對應 corpus 內真實存在的檔案(打錯檔名會讓 Hit@K 永遠是 0)
    corpus = {p.name for p in (DEFAULT_GOLDEN.parent / "corpus").iterdir()}
    assert {c.expected_document for c in cases} <= corpus


def test_load_golden_rejects_missing_field(tmp_path: Path) -> None:
    path = tmp_path / "qa.yaml"
    path.write_text("- question: 只有問題\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_golden(path)


def test_parse_args_top_k() -> None:
    assert parse_args(["--user-email", "a@example.com"]).top_k == DEFAULT_TOP_K
    args = parse_args(["--user-email", "a@example.com", "--top-k", "10"])
    assert args.top_k == 10 and args.user_email == "a@example.com"


# --- 對 seed 資料可重現執行(整合;§14 測試項)------------------------------

async def _seed(session_factory: async_sessionmaker[AsyncSession]) -> UUID:
    async with session_factory() as session:
        user = User(email="eval@example.com", password_hash="x")
        session.add(user)
        await session.flush()
        source = KnowledgeSource(owner_id=user.id, name="我的上傳", type="upload")
        session.add(source)
        await session.flush()
        for name, text in (
            ("expense_policy.md", "費用發生後應於三十日內完成報銷申請"),
            ("security_policy.md", "密碼長度至少十二個字元並每一百八十天更換"),
        ):
            doc = Document(
                source_id=source.id,
                uploaded_by=user.id,
                filename=name,
                mime="text/markdown",
                size_bytes=1,
                storage_key=f"documents/{name}/original.md",
                checksum=name.ljust(64, "0")[:64],
                status="ready",
            )
            session.add(doc)
            await session.flush()
            repo = ChunkRepository(session)
            await repo.replace_for_document(
                doc.id, [ChunkDraft(seq=0, text=text, tokens=len(text), meta={"kind": "document"})]
            )
            chunks = await repo.list_unembedded(doc.id, limit=10)
            vectors = await _EMBEDDING.embed([c.text for c in chunks])
            await repo.set_embeddings(
                dict(zip((c.id for c in chunks), vectors, strict=True)),
                version=_EMBEDDING.version,
            )
        await session.commit()
        return user.id


async def test_evaluate_against_seeded_corpus_is_reproducible(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user_id = await _seed(session_factory)
    # 檢索走真實 hybrid SQL,只把 embedding / 快取換成 fake(CI NEVER 打真實 API)
    service = fake_retrieval(
        PgVectorChunkIndex(session_factory, top_k=30, rrf_k=60, ef_search=60),
        embedding=_EMBEDDING,
    )
    ctx = AuthContext(user_id=user_id, role="user", trace_id="rag-eval-test")

    async def retrieve(question: str, top_k: int) -> list[RetrievedChunk]:
        return await service.retrieve(ctx, question, source_ids=None, top_n=top_k)

    cases = [
        GoldenCase("費用發生後應於三十日內完成報銷申請", "expense_policy.md", "三十日"),
        GoldenCase("密碼長度至少十二個字元並每一百八十天更換", "security_policy.md", "密碼"),
    ]
    first = await evaluate(cases, retrieve, top_k=5)
    second = await evaluate(cases, retrieve, top_k=5)

    assert [r.hit_rank for r in first.results] == [1, 1]  # 查詢文字 = chunk 文字 → 必為 top1
    assert first.hit_rate == 1.0 and first.mrr == pytest.approx(1.0)
    assert [r.hit_rank for r in second.results] == [r.hit_rank for r in first.results]
    assert [[c.filename for c in r.chunks] for r in second.results] == [
        [c.filename for c in r.chunks] for r in first.results
    ]
