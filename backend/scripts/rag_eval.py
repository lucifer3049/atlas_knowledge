#!/usr/bin/env python3
"""RAG 檢索評測腳本(T2.8;PHASE_2 §14、§15-7)。

用途:對**本地環境**跑 golden set,輸出每題的 top-K 命中表與總 Hit@K / MRR。
定位是**冒煙**——確認「問得到正確文件」的迴路沒壞;檢索品質(rerank 等決策)的評測
MUST 於上線前以真實內部文件另建 golden set(v1.2 §17 補遺、§G.2)。

前置:
  1. 把 `tests/golden/corpus/` 的檔案上傳到要評測的帳號,等狀態變 ready
     (檢索只看 `status='ready'` 的文件,D11)。
  2. embedding 服務(預設 Ollama)可達——評測走真實 provider,分數才有意義。

用法(MUST 於 backend/ 執行,settings 讀的是 backend/.env):
  python scripts/rag_eval.py --user-email you@example.com
  python scripts/rag_eval.py --user-email you@example.com --top-k 10
  python scripts/rag_eval.py --user-email you@example.com --golden path/to/qa.yaml

本腳本只讀不寫:NEVER 建立 / 修改 / 刪除任何資料。
"""
import argparse
import asyncio
import sys
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

from app.application.retrieval_service import RetrievalService
from app.core.config import settings
from app.core.db import create_engine
from app.core.redis import create_redis_client
from app.core.wiring import build_embedding, build_retrieval
from app.domain.entities.auth_context import AuthContext
from app.domain.entities.chunk import RetrievedChunk
from app.infrastructure.db.repositories.users import UserRepository
from app.infrastructure.db.session import create_session_factory

_BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_GOLDEN = _BACKEND_DIR / "tests" / "golden" / "qa.yaml"
DEFAULT_TOP_K = 5
_SNIPPET_CHARS = 40

# 檢索函式:(問句, top_k) → chunks。以參數注入,評測邏輯因此可用 fake adapter 直接測。
Retrieve = Callable[[str, int], Awaitable[list[RetrievedChunk]]]


@dataclass(frozen=True)
class GoldenCase:
    question: str
    expected_document: str
    expected_keyword: str | None = None


@dataclass(frozen=True)
class CaseResult:
    case: GoldenCase
    chunks: list[RetrievedChunk]
    hit_rank: int | None  # 1-based;None = top-K 內未命中

    @property
    def reciprocal_rank(self) -> float:
        return 0.0 if self.hit_rank is None else 1.0 / self.hit_rank


@dataclass(frozen=True)
class Report:
    results: list[CaseResult]
    top_k: int

    @property
    def hit_rate(self) -> float:
        if not self.results:
            return 0.0
        return sum(1 for r in self.results if r.hit_rank is not None) / len(self.results)

    @property
    def mrr(self) -> float:
        if not self.results:
            return 0.0
        return sum(r.reciprocal_rank for r in self.results) / len(self.results)


def load_golden(path: Path) -> list[GoldenCase]:
    """讀 golden set;欄位缺漏一律 fail-fast,NEVER 靜默略過題目(會虛報 Hit@K)。"""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"{path} 應為非空的題目清單")
    cases: list[GoldenCase] = []
    for i, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"第 {i} 題格式錯誤:應為 mapping")
        try:
            cases.append(
                GoldenCase(
                    question=str(item["question"]),
                    expected_document=str(item["expected_document"]),
                    expected_keyword=(
                        None if item.get("expected_keyword") is None
                        else str(item["expected_keyword"])
                    ),
                )
            )
        except KeyError as exc:
            raise ValueError(f"第 {i} 題缺少必要欄位 {exc}") from exc
    return cases


def matches(case: GoldenCase, chunk: RetrievedChunk) -> bool:
    """命中 = 檔名相符;若題目給了 keyword,chunk 內容也 MUST 含之(擋「檔案對、段落錯」)。"""
    if chunk.filename != case.expected_document:
        return False
    return case.expected_keyword is None or case.expected_keyword in chunk.text


async def evaluate(
    cases: Sequence[GoldenCase], retrieve: Retrieve, *, top_k: int = DEFAULT_TOP_K
) -> Report:
    results: list[CaseResult] = []
    for case in cases:
        chunks = (await retrieve(case.question, top_k))[:top_k]
        hit_rank = next(
            (i for i, chunk in enumerate(chunks, start=1) if matches(case, chunk)), None
        )
        results.append(CaseResult(case=case, chunks=chunks, hit_rank=hit_rank))
    return Report(results=results, top_k=top_k)


def _snippet(text: str) -> str:
    flat = " ".join(text.split())
    return flat[:_SNIPPET_CHARS] + ("…" if len(flat) > _SNIPPET_CHARS else "")


def format_report(report: Report) -> str:
    lines: list[str] = []
    for i, result in enumerate(report.results, start=1):
        expected = result.case.expected_document
        if result.case.expected_keyword is not None:
            expected += f"(關鍵字:{result.case.expected_keyword})"
        verdict = "未命中" if result.hit_rank is None else f"命中 #{result.hit_rank}"
        lines.append(f"[{i:>2}] {result.case.question}")
        lines.append(f"     期望:{expected} → {verdict}")
        if not result.chunks:
            lines.append("     (無檢索結果)")
        for rank, chunk in enumerate(result.chunks, start=1):
            mark = "*" if matches(result.case, chunk) else " "
            lines.append(
                f"     {mark}{rank}. {chunk.filename:<28} {chunk.score:.4f}  {_snippet(chunk.text)}"
            )
        lines.append("")

    k = report.top_k
    lines.append(
        f"總計:{len(report.results)} 題  "
        f"Hit@{k} = {report.hit_rate:.2f}  MRR = {report.mrr:.3f}"
    )
    return "\n".join(lines)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RAG 檢索評測(golden set 冒煙)")
    parser.add_argument("--user-email", required=True, help="評測對象帳號(檢索其名下文件)")
    parser.add_argument(
        "--top-k", type=int, default=DEFAULT_TOP_K, help=f"取前 K 筆計分(預設 {DEFAULT_TOP_K})"
    )
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN, help="golden set 路徑")
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace) -> int:
    cases = load_golden(args.golden)
    engine = create_engine()
    session_factory = create_session_factory(engine)
    embedding = build_embedding(settings)
    redis = create_redis_client(settings)
    try:
        async with session_factory() as session:
            user = await UserRepository(session).get_by_email(args.user_email.strip().lower())
        if user is None:
            print(f"找不到帳號 {args.user_email}", file=sys.stderr)
            return 1

        ctx = AuthContext(user_id=user.id, role=user.role, trace_id="rag-eval")
        service: RetrievalService = build_retrieval(
            settings, session_factory=session_factory, embedding=embedding, redis=redis
        )

        async def retrieve(question: str, top_k: int) -> list[RetrievedChunk]:
            return await service.retrieve(ctx, question, source_ids=None, top_n=top_k)

        report = await evaluate(cases, retrieve, top_k=args.top_k)
    finally:
        aclose = getattr(embedding, "aclose", None)
        if aclose is not None:
            await aclose()
        await redis.aclose()
        await engine.dispose()

    print(format_report(report))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(_run(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
