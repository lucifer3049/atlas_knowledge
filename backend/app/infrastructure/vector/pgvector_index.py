"""`ChunkIndex` adapter:pgvector HNSW + jieba tsvector 的 RRF hybrid 檢索(T2.6;§9.2)。

SQL 為 §9.2 的 v1.2 修正版(R15/R16),三個修正點 NEVER 回退:
1. vec 的 `row_number()` MUST `over (order by c.embedding <=> :qvec)`——`over ()` 在
   seq scan 下拿到的是掃描序,RRF 名次會錯亂。
2. fts 先在子查詢 `order by rank desc limit :top_k` 再取名次——CTE 層直接 limit 而無
   order by 會保留任意列。
3. 查詢用 **OR 語意**(token 以 `|` join 餵 `to_tsquery`);`plainto_tsquery` 是 AND,
   長問句經 jieba 斷詞後 FTS 腿恆空。
ownership 過濾在 SQL 內(§9.2),`meta->>'kind'='law'` 放行為唯一例外(R16,法規全站
共享唯讀;P2 尚無 law chunk,條款先到位)。

同交易先 `set_config('hnsw.ef_search'/'hnsw.iterative_scan', ..., true)`:iterative_scan
(pgvector ≥ 0.8)讓 owner 過濾不再是純 post-filter,防語料成長後單使用者召回崩塌(R15)。
"""
from collections.abc import Sequence
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import Uuid, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.elements import TextClause

from app.domain.entities.chunk import RetrievedChunk
from app.infrastructure.db.models import EMBEDDING_DIM

# 檢索交易的 pgvector 參數(§9.2);set_config(..., true) = SET LOCAL,交易結束自動還原。
_SET_SCAN_PARAMS = text(
    "select set_config('hnsw.ef_search', :ef_search, true),"
    " set_config('hnsw.iterative_scan', 'relaxed_order', true)"
)

_VEC_CTE = """
with vec as (
  select c.id, row_number() over (order by c.embedding <=> :qvec) as r
  from document_chunks c
  join documents d on d.id = c.document_id
  join knowledge_sources s on s.id = d.source_id
  where d.status = 'ready' and s.enabled
    and (d.uploaded_by = :user_id or c.meta->>'kind' = 'law')
    and (:no_filter or d.source_id = any(:source_ids))
    and c.embedding is not null
  order by c.embedding <=> :qvec
  limit :top_k
)
"""

_FTS_CTE = """
, fts as (
  select id, row_number() over (order by rank desc) as r
  from (
    select c.id, ts_rank_cd(c.tsv, q) as rank
    from document_chunks c
    join documents d on d.id = c.document_id
    join knowledge_sources s on s.id = d.source_id,
    to_tsquery('simple', :query_tsquery) q
    where d.status = 'ready' and s.enabled
      and (d.uploaded_by = :user_id or c.meta->>'kind' = 'law')
      and (:no_filter or d.source_id = any(:source_ids))
      and c.tsv @@ q
    order by rank desc
    limit :top_k
  ) ranked
)
"""

_RRF_SELECT = """
select coalesce(v.id, f.id) as chunk_id,
       coalesce(1.0/(:rrf_k + v.r), 0) + coalesce(1.0/(:rrf_k + f.r), 0) as score
from vec v full outer join fts f using (id)
order by score desc
limit :top_n
"""

# FTS 腿被跳過(token 全數被濾除)時的純向量版本;RRF 公式退化為單腿(§9.2 末)。
_VEC_ONLY_SELECT = """
select id as chunk_id, 1.0/(:rrf_k + r) as score
from vec
order by score desc
limit :top_n
"""

_LOAD_CHUNKS = text(
    "select c.id, c.document_id, d.filename, c.text, c.meta"
    " from document_chunks c"
    " join documents d on d.id = c.document_id"
    " where c.id = any(:ids)"
).bindparams(bindparam("ids", type_=ARRAY(Uuid(as_uuid=True))))


def build_tsquery(query_tokens: str) -> str:
    """token 串 → OR 語意 tsquery(§9.2「組法見下」)。

    濾除單字元 token(雜訊過高)、去重、以雙引號包裹逸出後 `|` join。
    全部被濾除 → 回空字串,呼叫端跳過 FTS 腿。
    """
    seen: dict[str, None] = {}
    for token in query_tokens.split():
        if len(token) > 1:
            seen.setdefault(token, None)
    escaped = ['"' + t.replace("\\", "\\\\").replace('"', '\\"') + '"' for t in seen]
    return " | ".join(escaped)


class PgVectorChunkIndex:
    """自管短交易(需 SET LOCAL,且檢索發生在 LLM 串流之前,NEVER 跨串流持有連線)。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        top_k: int,
        rrf_k: int,
        ef_search: int,
    ) -> None:
        self._session_factory = session_factory
        self._top_k = top_k
        self._rrf_k = rrf_k
        self._ef_search = ef_search

    async def hybrid_search(
        self,
        *,
        user_id: UUID,
        source_ids: Sequence[UUID] | None,
        query_tokens: str,
        query_embedding: list[float],
        top_n: int,
    ) -> list[RetrievedChunk]:
        tsquery = build_tsquery(query_tokens)
        filter_ids = list(source_ids or ())
        params: dict[str, object] = {
            "qvec": query_embedding,
            "user_id": user_id,
            "no_filter": not filter_ids,
            "source_ids": filter_ids,
            "top_k": self._top_k,
            "rrf_k": self._rrf_k,
            "top_n": top_n,
            "query_tsquery": tsquery,
        }
        stmt = self._statement(with_fts=bool(tsquery))

        async with self._session_factory() as session:
            await session.execute(_SET_SCAN_PARAMS, {"ef_search": str(self._ef_search)})
            ranked = (await session.execute(stmt, params)).all()
            if not ranked:
                return []
            scores = {row.chunk_id: float(row.score) for row in ranked}
            rows = (
                await session.execute(_LOAD_CHUNKS, {"ids": list(scores)})
            ).all()

        by_id = {row.id: row for row in rows}
        # ranked 已依 score desc 排序;此處只做 1-based 編號,NEVER 在 Python 重新過濾。
        return [
            RetrievedChunk(
                chunk_id=row.id,
                document_id=row.document_id,
                filename=row.filename,
                text=row.text,
                score=scores[row.id],
                rank=rank,
                meta=row.meta,
            )
            for rank, row in enumerate(
                (by_id[chunk_id] for chunk_id in scores if chunk_id in by_id), start=1
            )
        ]

    def _statement(self, *, with_fts: bool) -> TextClause:
        sql = _VEC_CTE + _FTS_CTE + _RRF_SELECT if with_fts else _VEC_CTE + _VEC_ONLY_SELECT
        return text(sql).bindparams(
            bindparam("qvec", type_=Vector(EMBEDDING_DIM)),
            bindparam("user_id", type_=Uuid(as_uuid=True)),
            bindparam("source_ids", type_=ARRAY(Uuid(as_uuid=True))),
        )
