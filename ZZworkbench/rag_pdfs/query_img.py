"""Query PDF image RAG indexes built by ingest_img.py.

Supports experiment strategies from plan.md:
    text_only   → 实验组 A
    inline      → 实验组 B（inline caption 已嵌入 text chunk）
    separate    → 实验组 C（混合检索 text + image_caption）

Retrieval modes:
    hybrid  → BM25 + vector cosine fusion (default)
    vector  → Chroma similarity_search only

Usage:
    uv run -m rag_pdfs.query_img --index ./tmp/index --strategy separate "如何开启 Cloud Sync？"
    uv run -m rag_pdfs.query_img --index ./tmp/index --retrieval hybrid --alpha 0.5 "question"
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser

from rag_pdfs.caption_chunks import CHUNK_TYPE_IMAGE_CAPTION
from rag_pdfs.hybrid_retrieve import (
    HybridRetriever,
    hybrid_retrieve_with_scores,
    load_corpus,
    load_corpus_and_vectors_from_chroma,
)
from rag_pdfs.index_manifest import validate_index_embedding
from rag_pdfs.model_runtime import ModelRole, ModelSpec
from rag_pdfs.prompts import ANSWER_PROMPT
from rag_pdfs.runtime import (
    ModelHandle,
    RAGRuntime,
    add_model_config_argument,
    get_setting,
)

Strategy = Literal["text_only", "inline", "separate"]
RetrievalMode = Literal["hybrid", "vector"]

STRATEGY_COLLECTIONS: dict[Strategy, tuple[str, str]] = {
    "text_only": ("text_only", "text_only_chunks"),
    "inline": ("inline_caption", "inline_caption_chunks"),
    "separate": ("separate_mixed", "separate_mixed_chunks"),
}


@dataclass(frozen=True)
class RetrievedEvidence:
    documents: list[Document]
    scores: list[dict[str, float]] | None = None


def load_vectorstore(
    index_dir: Path,
    strategy: Strategy,
    embedding_model_path: str | None,
    embeddings: Any | None = None,
    embedding_spec: ModelSpec | None = None,
    embedding_handle: ModelHandle | None = None,
    strict_manifest: bool = False,
) -> Chroma:
    collection_name, _ = STRATEGY_COLLECTIONS[strategy]
    persist_dir = index_dir / "chroma" / collection_name
    if not persist_dir.exists():
        raise FileNotFoundError(
            f"Chroma index not found: {persist_dir}. "
            "Run ingest with --build-chroma first."
        )
    fallback_handle = embedding_handle
    if embedding_spec is None:
        if fallback_handle is None:
            fallback_handle = RAGRuntime().embeddings(
                model_path=embedding_model_path
            )
        embedding_spec = fallback_handle.spec
    validate_index_embedding(
        index_dir,
        embedding_spec,
        collection_name=collection_name,
        strict_missing=strict_manifest,
    )
    if embeddings is None:
        if fallback_handle is None:
            fallback_handle = RAGRuntime().embeddings(
                model_path=embedding_model_path
            )
        embeddings = fallback_handle.instance
    return Chroma(
        persist_directory=str(persist_dir),
        embedding_function=embeddings,
        collection_name=collection_name,
    )


def format_evidence_block(doc: Document, rank: int) -> str:
    chunk_type = doc.metadata.get("chunk_type", "text")
    page = doc.metadata.get("page", "?")
    chunk_id = doc.metadata.get("chunk_id", "?")

    if chunk_type == CHUNK_TYPE_IMAGE_CAPTION:
        image_id = doc.metadata.get("image_id", chunk_id)
        image_path = doc.metadata.get("image_path", "")
        header = f"[Image evidence #{rank}] id={image_id} page={page}"
        if image_path:
            header += f" path={image_path}"
        return f"{header}\n{doc.page_content}"

    return f"[Text evidence #{rank}] id={chunk_id} page={page}\n{doc.page_content}"


def build_context(docs: list[Document]) -> str:
    if not docs:
        return "(no evidence retrieved)"
    return "\n\n".join(format_evidence_block(doc, index) for index, doc in enumerate(docs, 1))


def build_hybrid_retriever(
    vectorstore: Chroma,
    *,
    index_dir: Path,
    strategy: Strategy,
    embeddings: Any,
) -> HybridRetriever:
    """Reuse stored Chroma vectors; fall back to embedding the JSONL corpus."""

    try:
        corpus, vectors = load_corpus_and_vectors_from_chroma(vectorstore)
        return HybridRetriever(corpus, embeddings, doc_vectors=vectors)
    except (KeyError, OSError, TypeError, ValueError) as exc:
        print(
            "[warn] could not load stored Chroma embeddings; "
            f"falling back to embed_documents: {exc}"
        )
        return HybridRetriever(
            load_corpus(index_dir, strategy, vectorstore),
            embeddings,
        )


def retrieve_evidence(
    vectorstore: Chroma,
    question: str,
    *,
    fetch_k: int,
    final_k: int,
    retrieval: RetrievalMode = "hybrid",
    alpha: float = 0.5,
    index_dir: Path | None = None,
    strategy: Strategy | None = None,
    embeddings: Any | None = None,
    hybrid_retriever: HybridRetriever | None = None,
) -> RetrievedEvidence:
    if retrieval == "vector":
        docs = vectorstore.similarity_search(question, k=fetch_k)
        return RetrievedEvidence(documents=docs[:final_k])

    if index_dir is None or strategy is None or embeddings is None:
        raise ValueError("hybrid retrieval requires index_dir, strategy, and embeddings")

    if hybrid_retriever is not None:
        scored = hybrid_retriever.retrieve_with_scores(
            question,
            alpha=alpha,
            fetch_k=fetch_k,
            top_k=final_k,
        )
        return RetrievedEvidence(
            documents=[doc for doc, _scores in scored],
            scores=[scores for _doc, scores in scored],
        )

    corpus = load_corpus(index_dir, strategy, vectorstore)
    scored = hybrid_retrieve_with_scores(
        question,
        corpus,
        embeddings,
        alpha=alpha,
        fetch_k=fetch_k,
        top_k=final_k,
    )
    return RetrievedEvidence(
        documents=[doc for doc, _scores in scored],
        scores=[scores for _doc, scores in scored],
    )


def retrieve_documents(
    vectorstore: Chroma,
    question: str,
    *,
    fetch_k: int,
    final_k: int,
    retrieval: RetrievalMode = "hybrid",
    alpha: float = 0.5,
    index_dir: Path | None = None,
    strategy: Strategy | None = None,
    embeddings: Any | None = None,
    hybrid_retriever: HybridRetriever | None = None,
) -> list[Document]:
    """Compatibility wrapper for callers that only need documents."""

    return retrieve_evidence(
        vectorstore,
        question,
        fetch_k=fetch_k,
        final_k=final_k,
        retrieval=retrieval,
        alpha=alpha,
        index_dir=index_dir,
        strategy=strategy,
        embeddings=embeddings,
        hybrid_retriever=hybrid_retriever,
    ).documents


def build_llm(
    *,
    api_key: str,
    base_url: str,
    model: str,
) -> Any:
    """Backward-compatible wrapper around the canonical runtime."""

    return RAGRuntime().chat(
        ModelRole.ANSWER,
        api_key=api_key,
        base_url=base_url,
        model=model,
        temperature=0,
    ).instance


def build_answer_chain(llm: Any) -> Any:
    """Build a generation-only chain; retrieval happens exactly once upstream."""

    return ANSWER_PROMPT | llm | StrOutputParser()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question", help="User question")
    parser.add_argument(
        "--index",
        type=Path,
        required=True,
        help="Output directory from ingest_img.py",
    )
    parser.add_argument(
        "--strategy",
        choices=["text_only", "inline", "separate"],
        default="separate",
        help="Which experiment index to query (default: separate)",
    )
    parser.add_argument("--fetch-k", type=int, default=12, help="Initial retrieval depth")
    parser.add_argument("--top-k", type=int, default=6, help="Evidence chunks in final context")
    parser.add_argument(
        "--retrieval",
        choices=["hybrid", "vector"],
        default="hybrid",
        help="Retrieval mode: BM25+vector fusion or vector-only (default: hybrid)",
    )
    parser.add_argument(
        "--alpha",
        type=float,
        default=0.5,
        help="Hybrid fusion weight for vector score (default: 0.5)",
    )
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument(
        "--show-evidence",
        action="store_true",
        help="Print retrieved evidence before the answer",
    )
    parser.add_argument("--embedding-model-path", default=None)
    parser.add_argument(
        "--strict-index-manifest",
        action="store_true",
        help="Reject legacy indexes that do not contain index_manifest.json",
    )
    add_model_config_argument(parser)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    index_dir = args.index.expanduser().resolve()
    strategy: Strategy = args.strategy  # type: ignore[assignment]
    retrieval: RetrievalMode = args.retrieval  # type: ignore[assignment]

    runtime = RAGRuntime.from_model_config(args.model_config)
    embedding_handle = runtime.embeddings(model_path=args.embedding_model_path)
    vectorstore = load_vectorstore(
        index_dir,
        strategy,
        args.embedding_model_path,
        embedding_handle=embedding_handle,
        strict_manifest=args.strict_index_manifest,
    )
    embeddings = embedding_handle.instance
    hybrid_retriever = (
        build_hybrid_retriever(
            vectorstore,
            index_dir=index_dir,
            strategy=strategy,
            embeddings=embeddings,
        )
        if retrieval == "hybrid"
        else None
    )
    evidence = retrieve_evidence(
        vectorstore,
        args.question,
        fetch_k=args.fetch_k,
        final_k=args.top_k,
        retrieval=retrieval,
        alpha=args.alpha,
        index_dir=index_dir,
        strategy=strategy,
        embeddings=embeddings,
        hybrid_retriever=hybrid_retriever,
    )
    docs = evidence.documents
    context = build_context(docs)

    if args.show_evidence:
        print("=== Retrieved evidence ===")
        if evidence.scores is not None:
            for rank, (doc, scores) in enumerate(
                zip(docs, evidence.scores, strict=True),
                1,
            ):
                print(format_evidence_block(doc, rank))
                print(
                    f"  scores: fused={scores['fused']:.3f} "
                    f"vector={scores['norm_vector']:.3f} bm25={scores['norm_bm25']:.3f}"
                )
                print()
        else:
            print(context)
        print("=== Answer ===")

    answer_handle = runtime.chat(
        ModelRole.ANSWER,
        api_key=args.api_key,
        base_url=args.base_url,
        model=args.model,
        temperature=None if runtime.uses_catalog else 0,
        default_api_key_env="DEEPSEEK_API_KEY",
        default_base_url=get_setting(
            "deepseek_base_url",
            "https://api.deepseek.com",
        ),
        default_model=get_setting("deepseek_model", "deepseek-v4-flash"),
    )
    chain = build_answer_chain(answer_handle.instance)
    answer = chain.invoke({"question": args.question, "context": context})
    print(answer)


if __name__ == "__main__":
    main()
