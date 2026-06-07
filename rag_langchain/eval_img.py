"""Run image-index RAG experiments over multiple questions and strategies.

Question JSONL schema:
    {
      "id": "bp-tests-001",
      "index": "tmp/outs/BackpressureIsAllYouNeed",
      "question": "How do automated tests act as backpressure?",
      "gold_answer": "... optional ...",
      "gold_image_ids": ["imgcap-0013"],
      "gold_chunk_ids": ["text-0027"],
      "image_role": "illustrative"
    }

Usage:
    uv run -m rag_langchain.eval_img --questions tmp/eval_questions.jsonl --retrieve-only
    uv run -m rag_langchain.eval_img --questions tmp/eval_questions.jsonl --alphas 0.3,0.5,0.7
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser

from rag_langchain.caption_chunks import CHUNK_TYPE_IMAGE_CAPTION
from rag_langchain.hybrid_retrieve import (
    HybridRetriever,
    load_corpus,
    load_corpus_and_vectors_from_chroma,
)
from rag_langchain.query_img import (
    ANSWER_PROMPT,
    RetrievalMode,
    Strategy,
    build_context,
    build_llm,
    load_vectorstore,
    retrieve_documents,
)
from rag_langchain.ingest_img import build_embeddings, get_setting


DEFAULT_STRATEGIES: tuple[Strategy, ...] = ("text_only", "inline", "separate")
DEFAULT_RETRIEVALS: tuple[RetrievalMode, ...] = ("hybrid",)
INLINE_IMAGE_ID_RE = re.compile(r"\[Image\s+id=(imgcap-\d+)")


@dataclass(frozen=True)
class ExperimentCase:
    id: str
    question: str
    index: Path
    gold_answer: str = ""
    gold_image_ids: tuple[str, ...] = ()
    gold_chunk_ids: tuple[str, ...] = ()
    image_role: str = ""
    metadata: dict[str, Any] | None = None


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON at {path}:{line_number}: {exc}") from exc
    return rows


def parse_case(row: dict[str, Any], *, default_index: Path | None) -> ExperimentCase:
    case_id = str(row.get("id") or row.get("qid") or "").strip()
    question = str(row.get("question") or "").strip()
    if not case_id:
        raise ValueError(f"Question row is missing id/qid: {row}")
    if not question:
        raise ValueError(f"Question row is missing question: {row}")

    index_raw = row.get("index") or row.get("index_dir") or default_index
    if not index_raw:
        raise ValueError(f"Question row is missing index and --index was not provided: {row}")

    return ExperimentCase(
        id=case_id,
        question=question,
        index=Path(index_raw).expanduser(),
        gold_answer=str(row.get("gold_answer") or ""),
        gold_image_ids=tuple(str(value) for value in row.get("gold_image_ids", [])),
        gold_chunk_ids=tuple(str(value) for value in row.get("gold_chunk_ids", [])),
        image_role=str(row.get("image_role") or ""),
        metadata=dict(row.get("metadata") or {}),
    )


def parse_csv_choices(raw: str, allowed: set[str], *, field_name: str) -> list[str]:
    values = [part.strip() for part in raw.split(",") if part.strip()]
    if not values:
        raise ValueError(f"{field_name} must not be empty")
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise ValueError(
            f"Unknown {field_name}: {', '.join(unknown)}. "
            f"Allowed: {', '.join(sorted(allowed))}"
        )
    return values


def parse_alpha_list(raw: str) -> list[float]:
    values: list[float] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        value = float(part)
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"alpha must be between 0 and 1, got {value}")
        values.append(value)
    if not values:
        raise ValueError("--alphas must contain at least one value")
    return values


def doc_identity(doc: Document) -> dict[str, Any]:
    metadata = doc.metadata
    chunk_type = metadata.get("chunk_type", "text")
    chunk_id = str(metadata.get("chunk_id") or "")
    image_id = str(metadata.get("image_id") or "") if chunk_type == CHUNK_TYPE_IMAGE_CAPTION else ""
    inline_image_ids = INLINE_IMAGE_ID_RE.findall(doc.page_content)
    return {
        "chunk_id": chunk_id,
        "chunk_type": chunk_type,
        "image_id": image_id,
        "inline_image_ids": inline_image_ids,
        "page": metadata.get("page"),
        "source_pdf": metadata.get("source_pdf"),
        "image_path": metadata.get("image_path"),
    }


def evidence_rows(docs: list[Document]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for rank, doc in enumerate(docs, 1):
        row = doc_identity(doc)
        row["rank"] = rank
        row["text_preview"] = doc.page_content[:500]
        rows.append(row)
    return rows


def retrieved_ids(docs: list[Document]) -> tuple[set[str], set[str]]:
    chunk_ids: set[str] = set()
    image_ids: set[str] = set()
    for doc in docs:
        metadata = doc.metadata
        chunk_id = metadata.get("chunk_id")
        if chunk_id:
            chunk_ids.add(str(chunk_id))
        if metadata.get("chunk_type") == CHUNK_TYPE_IMAGE_CAPTION:
            image_id = metadata.get("image_id") or chunk_id
            if image_id:
                image_ids.add(str(image_id))
        image_ids.update(INLINE_IMAGE_ID_RE.findall(doc.page_content))
    return chunk_ids, image_ids


def estimate_context_chars(docs: list[Document]) -> int:
    return len(build_context(docs))


def evaluate_retrieval(case: ExperimentCase, docs: list[Document]) -> dict[str, Any]:
    chunk_ids, image_ids = retrieved_ids(docs)
    gold_chunks = set(case.gold_chunk_ids)
    gold_images = set(case.gold_image_ids)
    image_docs = [doc for doc in docs if doc.metadata.get("chunk_type") == CHUNK_TYPE_IMAGE_CAPTION]
    inline_image_mentions = sum(
        len(INLINE_IMAGE_ID_RE.findall(doc.page_content)) for doc in docs
    )

    return {
        "image_evidence_count": len(image_docs),
        "inline_image_mention_count": inline_image_mentions,
        "image_reference_count": len(image_docs) + inline_image_mentions,
        "image_evidence_ratio": round(len(image_docs) / len(docs), 4) if docs else 0.0,
        "context_chars": estimate_context_chars(docs),
        "gold_chunk_hit": bool(gold_chunks and gold_chunks & chunk_ids),
        "gold_image_hit": bool(gold_images and gold_images & image_ids),
        "gold_chunk_hit_count": len(gold_chunks & chunk_ids),
        "gold_image_hit_count": len(gold_images & image_ids),
        "retrieved_chunk_ids": sorted(chunk_ids),
        "retrieved_image_ids": sorted(image_ids),
    }


def summarize_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[tuple[str, str, float], list[dict[str, Any]]] = defaultdict(list)
    for row in results:
        groups[(row["strategy"], row["retrieval"], float(row["alpha"]))].append(row)

    by_variant: list[dict[str, Any]] = []
    for (strategy, retrieval, alpha), rows in sorted(groups.items()):
        with_gold_image = [row for row in rows if row["gold_image_ids"]]
        with_gold_chunk = [row for row in rows if row["gold_chunk_ids"]]
        by_variant.append(
            {
                "strategy": strategy,
                "retrieval": retrieval,
                "alpha": alpha,
                "runs": len(rows),
                "avg_image_evidence_count": round(
                    sum(row["metrics"]["image_evidence_count"] for row in rows) / len(rows),
                    4,
                ),
                "avg_inline_image_mention_count": round(
                    sum(row["metrics"]["inline_image_mention_count"] for row in rows)
                    / len(rows),
                    4,
                ),
                "avg_image_reference_count": round(
                    sum(row["metrics"]["image_reference_count"] for row in rows)
                    / len(rows),
                    4,
                ),
                "avg_context_chars": round(
                    sum(row["metrics"]["context_chars"] for row in rows) / len(rows),
                    2,
                ),
                "gold_image_recall_at_k": (
                    round(
                        sum(1 for row in with_gold_image if row["metrics"]["gold_image_hit"])
                        / len(with_gold_image),
                        4,
                    )
                    if with_gold_image
                    else None
                ),
                "gold_chunk_recall_at_k": (
                    round(
                        sum(1 for row in with_gold_chunk if row["metrics"]["gold_chunk_hit"])
                        / len(with_gold_chunk),
                        4,
                    )
                    if with_gold_chunk
                    else None
                ),
            }
        )

    return {
        "run_count": len(results),
        "case_count": len({row["case_id"] for row in results}),
        "by_variant": by_variant,
    }


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def run_experiments(args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    question_rows = read_jsonl(args.questions)
    if args.limit is not None:
        question_rows = question_rows[: args.limit]
    default_index = args.index.expanduser() if args.index else None
    cases = [parse_case(row, default_index=default_index) for row in question_rows]

    strategies = parse_csv_choices(
        args.strategies,
        allowed=set(DEFAULT_STRATEGIES),
        field_name="strategies",
    )
    retrievals = parse_csv_choices(
        args.retrievals,
        allowed=set(DEFAULT_RETRIEVALS) | {"vector"},
        field_name="retrievals",
    )
    alphas = parse_alpha_list(args.alphas)

    embeddings = build_embeddings(args.embedding_model_path)
    llm = None
    if not args.retrieve_only:
        api_key = args.api_key or get_setting("deepseek_api_key")
        if not api_key:
            raise RuntimeError(
                "Missing API key. Pass --api-key, set DEEPSEEK_API_KEY, "
                "or use --retrieve-only."
            )
        llm = build_llm(
            api_key=api_key,
            base_url=args.base_url,
            model=args.model,
        )

    vectorstores: dict[tuple[Path, str], Any] = {}
    hybrid_retrievers: dict[tuple[Path, str], HybridRetriever] = {}
    results: list[dict[str, Any]] = []
    answer_chain = (ANSWER_PROMPT | llm | StrOutputParser()) if llm is not None else None

    for case in cases:
        index_dir = case.index.resolve()
        for strategy_raw in strategies:
            strategy: Strategy = strategy_raw  # type: ignore[assignment]
            key = (index_dir, strategy)
            if key not in vectorstores:
                vectorstores[key] = load_vectorstore(
                    index_dir,
                    strategy,
                    args.embedding_model_path,
                    embeddings=embeddings,
                )
            vectorstore = vectorstores[key]

            for retrieval_raw in retrievals:
                retrieval: RetrievalMode = retrieval_raw  # type: ignore[assignment]
                alpha_values = alphas if retrieval == "hybrid" else [1.0]
                for alpha in alpha_values:
                    started = time.perf_counter()
                    cached_hybrid = None
                    if retrieval == "hybrid":
                        cached_hybrid = hybrid_retrievers.get(key)
                        if cached_hybrid is None:
                            try:
                                corpus, vectors = load_corpus_and_vectors_from_chroma(
                                    vectorstore
                                )
                                cached_hybrid = HybridRetriever(
                                    corpus,
                                    embeddings,
                                    doc_vectors=vectors,
                                )
                            except Exception as exc:
                                print(
                                    "[warn] could not load stored Chroma embeddings; "
                                    f"falling back to embed_documents: {exc}"
                                )
                                cached_hybrid = HybridRetriever(
                                    load_corpus(index_dir, strategy, vectorstore),
                                    embeddings,
                                )
                            hybrid_retrievers[key] = cached_hybrid
                    docs = retrieve_documents(
                        vectorstore,
                        case.question,
                        fetch_k=args.fetch_k,
                        final_k=args.top_k,
                        retrieval=retrieval,
                        alpha=alpha,
                        index_dir=index_dir,
                        strategy=strategy,
                        embeddings=embeddings,
                        hybrid_retriever=cached_hybrid,
                    )
                    answer = ""
                    if answer_chain is not None:
                        answer = answer_chain.invoke(
                            {
                                "question": case.question,
                                "context": build_context(docs),
                            }
                        )

                    elapsed = round(time.perf_counter() - started, 3)
                    metrics = evaluate_retrieval(case, docs)
                    row = {
                        "case_id": case.id,
                        "question": case.question,
                        "index": str(index_dir),
                        "strategy": strategy,
                        "retrieval": retrieval,
                        "alpha": alpha,
                        "fetch_k": args.fetch_k,
                        "top_k": args.top_k,
                        "image_role": case.image_role,
                        "gold_answer": case.gold_answer,
                        "gold_image_ids": list(case.gold_image_ids),
                        "gold_chunk_ids": list(case.gold_chunk_ids),
                        "metrics": metrics,
                        "evidence": evidence_rows(docs),
                        "answer": answer,
                        "elapsed_seconds": elapsed,
                        "metadata": case.metadata or {},
                    }
                    results.append(row)
                    print(
                        f"[eval] {case.id} strategy={strategy} retrieval={retrieval} "
                        f"alpha={alpha} images={metrics['image_evidence_count']} "
                        f"gold_image_hit={metrics['gold_image_hit']} elapsed={elapsed}s"
                    )

    return results, summarize_results(results)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--questions",
        type=Path,
        required=True,
        help="JSONL question set. Each row needs id, question, and index unless --index is set.",
    )
    parser.add_argument(
        "--index",
        type=Path,
        default=None,
        help="Default ingest output directory for rows that omit index.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("tmp/eval_runs/image_rag_eval.jsonl"),
        help="Per-run JSONL output path.",
    )
    parser.add_argument(
        "--summary-out",
        type=Path,
        default=None,
        help="Summary JSON path. Defaults to <out>.summary.json.",
    )
    parser.add_argument(
        "--strategies",
        default="text_only,inline,separate",
        help="Comma-separated strategies: text_only,inline,separate.",
    )
    parser.add_argument(
        "--retrievals",
        default="hybrid",
        help="Comma-separated retrieval modes: hybrid,vector.",
    )
    parser.add_argument(
        "--alphas",
        default="0.5",
        help="Comma-separated hybrid vector weights, e.g. 0.3,0.5,0.7.",
    )
    parser.add_argument("--fetch-k", type=int, default=12)
    parser.add_argument("--top-k", type=int, default=6)
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only run the first N question rows; useful for smoke tests.",
    )
    parser.add_argument(
        "--retrieve-only",
        action="store_true",
        help="Only retrieve/evaluate evidence; do not call the answer LLM.",
    )
    parser.add_argument("--api-key", default=None)
    parser.add_argument(
        "--base-url",
        default=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
    )
    parser.add_argument("--model", default=os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"))
    parser.add_argument("--embedding-model-path", default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    results, summary = run_experiments(args)
    write_jsonl(args.out, results)

    summary_path = args.summary_out or args.out.with_suffix(args.out.suffix + ".summary.json")
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[done] wrote {len(results)} rows -> {args.out}")
    print(f"[done] summary -> {summary_path}")


if __name__ == "__main__":
    main()
