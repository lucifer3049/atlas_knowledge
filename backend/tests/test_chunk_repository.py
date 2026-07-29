"""ChunkRepository 整合測試(測試 DB;T2.4;PHASE_2 §12.1、§8.2)。

驗證:tsv 以 jieba token 可被 tsquery 命中、先刪後插冪等、embedding 游標與回寫。
tsv 為 PostgreSQL 計算欄位,MUST 對真實 DB 測試(非 fake)。
"""
from uuid import UUID

import jieba
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.entities.chunk import ChunkDraft
from app.infrastructure.db.models import Document, KnowledgeSource, User
from app.infrastructure.db.repositories.chunks import ChunkRepository

pytestmark = pytest.mark.anyio


async def _seed_document(session_factory: async_sessionmaker[AsyncSession]) -> UUID:
    async with session_factory() as session:
        user = User(email="c@example.com", password_hash="x")
        session.add(user)
        await session.flush()
        source = KnowledgeSource(owner_id=user.id, name="我的上傳", type="upload")
        session.add(source)
        await session.flush()
        doc = Document(
            source_id=source.id,
            uploaded_by=user.id,
            filename="a.txt",
            mime="text/plain",
            size_bytes=1,
            storage_key="documents/x/original.txt",
            checksum="0" * 64,
            status="ready",
        )
        session.add(doc)
        await session.flush()
        await session.commit()
        return doc.id


def _draft(seq: int, text_: str) -> ChunkDraft:
    return ChunkDraft(seq=seq, text=text_, tokens=len(text_), meta={"kind": "document"})


def _tsquery(word: str) -> str:
    tokens = [tok.strip() for tok in jieba.cut_for_search(word) if tok.strip()]
    return " | ".join(tokens)


async def test_written_chunk_tsv_is_matchable_by_jieba_tsquery(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    doc_id = await _seed_document(session_factory)
    async with session_factory() as session:
        await ChunkRepository(session).replace_for_document(
            doc_id, [_draft(0, "台北市是台灣的首都與最大城市")]
        )
        await session.commit()

    async with session_factory() as session:
        hit = await session.execute(
            text(
                "select count(*) from document_chunks "
                "where document_id = :d and tsv @@ to_tsquery('simple', :q)"
            ),
            {"d": doc_id, "q": _tsquery("首都")},
        )
        assert hit.scalar_one() == 1

        miss = await session.execute(
            text(
                "select count(*) from document_chunks "
                "where document_id = :d and tsv @@ to_tsquery('simple', :q)"
            ),
            {"d": doc_id, "q": _tsquery("量子力學")},
        )
        assert miss.scalar_one() == 0


async def test_chunks_persist_seq_text_meta_tokens(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    doc_id = await _seed_document(session_factory)
    drafts = [_draft(0, "第一塊內容"), _draft(1, "第二塊內容")]
    async with session_factory() as session:
        await ChunkRepository(session).replace_for_document(doc_id, drafts)
        await session.commit()

    async with session_factory() as session:
        rows = (
            await session.execute(
                text("select seq, text, tokens, meta, embedding from document_chunks "
                     "where document_id = :d order by seq"),
                {"d": doc_id},
            )
        ).all()
    assert [r.seq for r in rows] == [0, 1]
    assert [r.text for r in rows] == ["第一塊內容", "第二塊內容"]
    assert all(r.meta["kind"] == "document" for r in rows)
    assert all(r.embedding is None for r in rows)  # D11:embedding 先留 NULL


async def test_replace_is_idempotent_no_duplicate_chunks(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    doc_id = await _seed_document(session_factory)
    drafts = [_draft(0, "內容甲"), _draft(1, "內容乙")]
    for _ in range(2):  # 重跑 chunk 階段
        async with session_factory() as session:
            await ChunkRepository(session).replace_for_document(doc_id, drafts)
            await session.commit()

    async with session_factory() as session:
        count = await session.execute(
            text("select count(*) from document_chunks where document_id = :d"),
            {"d": doc_id},
        )
        assert count.scalar_one() == 2


async def test_replace_with_empty_drafts_clears_existing(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    doc_id = await _seed_document(session_factory)
    async with session_factory() as session:
        repo = ChunkRepository(session)
        await repo.replace_for_document(doc_id, [_draft(0, "先寫一塊")])
        await session.commit()
    async with session_factory() as session:
        await ChunkRepository(session).replace_for_document(doc_id, [])
        await session.commit()
    async with session_factory() as session:
        count = await session.execute(
            text("select count(*) from document_chunks where document_id = :d"),
            {"d": doc_id},
        )
        assert count.scalar_one() == 0


# --- embedding 游標與回寫(§8.2)---------------------------------------------

async def test_unembedded_cursor_and_writeback(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    doc_id = await _seed_document(session_factory)
    drafts = [_draft(i, f"第{i}塊") for i in range(3)]
    async with session_factory() as session:
        await ChunkRepository(session).replace_for_document(doc_id, drafts)
        await session.commit()

    async with session_factory() as session:
        repo = ChunkRepository(session)
        assert await repo.count_unembedded(doc_id) == 3
        batch = await repo.list_unembedded(doc_id, limit=2)
        assert [c.seq for c in batch] == [0, 1]  # 依 seq 排序
        await repo.set_embeddings(
            {batch[0].id: [0.1] * 1024, batch[1].id: [0.2] * 1024}, version="bge-m3@1024"
        )
        await session.commit()

    async with session_factory() as session:
        repo = ChunkRepository(session)
        # 已嵌入的不再出現在游標 → 剩 1 塊(續傳語意)
        assert await repo.count_unembedded(doc_id) == 1
        remaining = await repo.list_unembedded(doc_id, limit=10)
        assert [c.seq for c in remaining] == [2]

    async with session_factory() as session:
        row = (
            await session.execute(
                text("select embedding_version from document_chunks "
                     "where document_id = :d and seq = 0"),
                {"d": doc_id},
            )
        ).scalar_one()
        assert row == "bge-m3@1024"
