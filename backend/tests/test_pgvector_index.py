"""hybrid 檢索 SQL 整合測試(測試 DB + 真實 chunks;T2.6、PHASE_2 §9.2、§12.2)。

向量由 `FakeEmbeddingProvider` 產生(決定性):查詢文字與某 chunk 完全相同時,該 chunk
的 cosine 距離必為 0 → 必為 vector 腿第一名,故排序可被斷言。
ownership / status / source 過濾 MUST 在 SQL 內(§9.2),本檔逐條驗證。
"""
from collections.abc import Sequence
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.entities.chunk import ChunkDraft, RetrievedChunk
from app.infrastructure.db.models import Document, KnowledgeSource, User
from app.infrastructure.db.repositories.chunks import ChunkRepository
from app.infrastructure.vector.pgvector_index import PgVectorChunkIndex, build_tsquery
from app.infrastructure.vector.tokenizer import index_tokens
from tests.fakes import FakeEmbeddingProvider

pytestmark = pytest.mark.anyio

_EMBEDDING = FakeEmbeddingProvider()


def _index(session_factory: async_sessionmaker[AsyncSession]) -> PgVectorChunkIndex:
    return PgVectorChunkIndex(session_factory, top_k=30, rrf_k=60, ef_search=60)


async def _user(session: AsyncSession, email: str) -> User:
    user = User(email=email, password_hash="x")
    session.add(user)
    await session.flush()
    return user


async def _source(
    session: AsyncSession, owner: User, *, name: str = "我的上傳", enabled: bool = True
) -> KnowledgeSource:
    source = KnowledgeSource(owner_id=owner.id, name=name, type="upload", enabled=enabled)
    session.add(source)
    await session.flush()
    return source


async def _document(
    session: AsyncSession,
    owner: User,
    source: KnowledgeSource,
    *,
    filename: str,
    status: str = "ready",
    checksum: str | None = None,
) -> Document:
    doc = Document(
        source_id=source.id,
        uploaded_by=owner.id,
        filename=filename,
        mime="text/plain",
        size_bytes=1,
        storage_key=f"documents/{filename}/original.txt",
        checksum=(checksum or filename).ljust(64, "0")[:64],
        status=status,
    )
    session.add(doc)
    await session.flush()
    return doc


async def _add_chunks(
    session: AsyncSession,
    document: Document,
    texts: Sequence[str],
    *,
    kind: str = "document",
) -> None:
    repo = ChunkRepository(session)
    drafts = [
        ChunkDraft(seq=i, text=t, tokens=len(t), meta={"kind": kind})
        for i, t in enumerate(texts)
    ]
    await repo.replace_for_document(document.id, drafts)
    chunks = await repo.list_unembedded(document.id, limit=len(drafts))
    vectors = await _EMBEDDING.embed([c.text for c in chunks])
    await repo.set_embeddings(
        dict(zip((c.id for c in chunks), vectors, strict=True)), version=_EMBEDDING.version
    )


async def _search(
    session_factory: async_sessionmaker[AsyncSession],
    user_id: UUID,
    query: str,
    *,
    source_ids: Sequence[UUID] | None = None,
    top_n: int = 8,
) -> list[RetrievedChunk]:
    [qvec] = await _EMBEDDING.embed([query])
    return await _index(session_factory).hybrid_search(
        user_id=user_id,
        source_ids=source_ids,
        query_tokens=index_tokens(query),
        query_embedding=qvec,
        top_n=top_n,
    )


# --- tsquery 組法(§9.2)-----------------------------------------------------

def test_tsquery_is_or_joined_and_drops_single_char_tokens() -> None:
    assert build_tsquery("台北 市 首都") == '"台北" | "首都"'


def test_tsquery_dedupes_and_escapes() -> None:
    assert build_tsquery('台北 台北 a"b') == '"台北" | "a\\"b"'


def test_tsquery_empty_when_all_tokens_filtered() -> None:
    assert build_tsquery("的 是 a") == ""


# --- 過濾(§12.2 檢索列)------------------------------------------------------

async def test_other_users_documents_never_returned(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        owner = await _user(session, "owner@example.com")
        other = await _user(session, "other@example.com")
        doc = await _document(session, owner, await _source(session, owner), filename="a.txt")
        await _add_chunks(session, doc, ["年度報帳流程說明"])
        await session.commit()

    mine = await _search(session_factory, owner.id, "年度報帳流程說明")
    theirs = await _search(session_factory, other.id, "年度報帳流程說明")
    assert [c.text for c in mine] == ["年度報帳流程說明"]
    assert theirs == []


async def test_law_chunks_are_shared_across_users(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # R16:`meta.kind='law'` 全站唯讀共享,是 ownership 過濾的唯一例外(P3 才有真實法規)
    async with session_factory() as session:
        owner = await _user(session, "admin@example.com")
        other = await _user(session, "reader@example.com")
        doc = await _document(session, owner, await _source(session, owner), filename="law.txt")
        await _add_chunks(session, doc, ["勞動基準法第十五條之一"], kind="law")
        await session.commit()

    hits = await _search(session_factory, other.id, "勞動基準法第十五條之一")
    assert [c.text for c in hits] == ["勞動基準法第十五條之一"]


async def test_non_ready_documents_are_excluded(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        owner = await _user(session, "owner@example.com")
        source = await _source(session, owner)
        ready = await _document(session, owner, source, filename="ready.txt")
        pending = await _document(
            session, owner, source, filename="pending.txt", status="embedding"
        )
        await _add_chunks(session, ready, ["可用的內容片段"])
        await _add_chunks(session, pending, ["尚未完成的內容片段"])
        await session.commit()

    hits = await _search(session_factory, owner.id, "尚未完成的內容片段")
    assert [c.filename for c in hits] == ["ready.txt"]  # D11:只查 status='ready'


async def test_disabled_source_is_excluded(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        owner = await _user(session, "owner@example.com")
        disabled = await _source(session, owner, name="停用來源", enabled=False)
        doc = await _document(session, owner, disabled, filename="off.txt")
        await _add_chunks(session, doc, ["停用來源的內容"])
        await session.commit()

    assert await _search(session_factory, owner.id, "停用來源的內容") == []


async def test_source_ids_filter_applies(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # P2 每位使用者僅有一個 type='upload' 來源(ux_sources_owner_default,§11 補遺),
    # 故以「自己的來源 vs 他人的來源」驗證 `= any(:source_ids)` 過濾。
    async with session_factory() as session:
        owner = await _user(session, "owner@example.com")
        stranger = await _user(session, "stranger@example.com")
        mine = await _source(session, owner)
        theirs = await _source(session, stranger)
        doc = await _document(session, owner, mine, filename="a.txt")
        await _add_chunks(session, doc, ["共同的關鍵內容"])
        await session.commit()

    scoped = await _search(session_factory, owner.id, "共同的關鍵內容", source_ids=[mine.id])
    assert [c.filename for c in scoped] == ["a.txt"]

    other_scope = await _search(
        session_factory, owner.id, "共同的關鍵內容", source_ids=[theirs.id]
    )
    assert other_scope == []

    unscoped = await _search(session_factory, owner.id, "共同的關鍵內容")
    assert [c.filename for c in unscoped] == ["a.txt"]  # None = 全部 enabled 來源


# --- RRF 融合(§9.2)---------------------------------------------------------

async def test_vector_only_and_fts_only_hits_both_enter_rrf(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        owner = await _user(session, "owner@example.com")
        source = await _source(session, owner)
        doc = await _document(session, owner, source, filename="mix.txt")
        # 「請問差旅費報銷需要哪些單據」= 查詢原文 → 向量距離 0(vector 腿必中);
        # 另一塊只共用 token「單據」→ 僅 FTS 腿命中(向量為不相關的 hash 方向)。
        await _add_chunks(
            session,
            doc,
            ["請問差旅費報銷需要哪些單據", "單據"],
        )
        await session.commit()

    hits = await _search(session_factory, owner.id, "請問差旅費報銷需要哪些單據")
    assert [c.text for c in hits][0] == "請問差旅費報銷需要哪些單據"
    assert "單據" in [c.text for c in hits]  # fts 腿命中者也進 RRF 結果
    assert [c.rank for c in hits] == list(range(1, len(hits) + 1))
    assert hits[0].score > hits[-1].score


async def test_vector_only_path_when_tsquery_is_empty(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # token 全為單字元 → 跳過 FTS 腿,純向量仍須有結果(§9.2 末)
    async with session_factory() as session:
        owner = await _user(session, "owner@example.com")
        doc = await _document(session, owner, await _source(session, owner), filename="v.txt")
        await _add_chunks(session, doc, ["某"])
        await session.commit()

    hits = await _search(session_factory, owner.id, "某")
    assert [c.text for c in hits] == ["某"]


async def test_top_n_caps_result_size(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        owner = await _user(session, "owner@example.com")
        doc = await _document(session, owner, await _source(session, owner), filename="n.txt")
        await _add_chunks(session, doc, [f"報帳規定第 {i} 條說明" for i in range(6)])
        await session.commit()

    hits = await _search(session_factory, owner.id, "報帳規定第 1 條說明", top_n=2)
    assert len(hits) == 2


async def test_chunks_without_embedding_are_skipped_by_vector_leg(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # embedding 尚未回寫(D11 nullable)的 chunk 不進向量腿;有 FTS 命中仍可出現
    async with session_factory() as session:
        owner = await _user(session, "owner@example.com")
        doc = await _document(session, owner, await _source(session, owner), filename="p.txt")
        await ChunkRepository(session).replace_for_document(
            doc.id,
            [ChunkDraft(seq=0, text="尚未嵌入的內容片段", tokens=9, meta={"kind": "document"})],
        )
        await session.commit()

    hits = await _search(session_factory, owner.id, "尚未嵌入的內容片段")
    assert [c.text for c in hits] == ["尚未嵌入的內容片段"]  # 純 FTS 腿命中
