"""Query PDF image RAG indexes built by ingest_img.py.

Supports experiment strategies from plan.md:
    text_only   → 实验组 A
    inline      → 实验组 B（inline caption 已嵌入 text chunk）
    separate    → 实验组 C（混合检索 text + image_caption）

Retrieval modes:
    hybrid  → BM25 + vector cosine fusion (default)
    vector  → Chroma similarity_search only

Usage:
    uv run -m rag_langchain.query_img --index ./tmp/index --strategy separate "如何开启 Cloud Sync？"
    uv run -m rag_langchain.query_img --index ./tmp/index --retrieval hybrid --alpha 0.5 "question"
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any, Literal

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough

from rag_langchain.caption_chunks import CHUNK_TYPE_IMAGE_CAPTION
from rag_langchain.hybrid_retrieve import (
    hybrid_retrieve,
    hybrid_retrieve_with_scores,
    load_corpus,
)
from rag_langchain.ingest_img import build_embeddings, get_setting

Strategy = Literal["text_only", "inline", "separate"]
RetrievalMode = Literal["hybrid", "vector"]

STRATEGY_COLLECTIONS: dict[Strategy, tuple[str, str]] = {
    "text_only": ("text_only", "text_only_chunks"),
    "inline": ("inline_caption", "inline_caption_chunks"),
    "separate": ("separate_mixed", "separate_mixed_chunks"),
}

ANSWER_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You answer questions using only the provided evidence from a technical document. "
            "Distinguish text evidence from image caption evidence. "
            "When an image caption supports the answer, cite image_id and page. "
            "If evidence is insufficient, say so. Do not invent details.",
        ),
        (
            "human",
            "Question:\n{question}\n\nEvidence:\n{context}\n\nAnswer:",
        ),
    ]
)


def load_vectorstore(
    index_dir: Path,
    strategy: Strategy,
    embedding_model_path: str | None,
) -> Chroma:
    collection_name, _ = STRATEGY_COLLECTIONS[strategy]
    persist_dir = index_dir / "chroma" / collection_name
    if not persist_dir.exists():
        raise FileNotFoundError(
            f"Chroma index not found: {persist_dir}. "
            "Run ingest with --build-chroma first."
        )
    return Chroma(
        persist_directory=str(persist_dir),
        embedding_function=build_embeddings(embedding_model_path),
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
) -> list[Document]:
    if retrieval == "vector":
        docs = vectorstore.similarity_search(question, k=fetch_k)
        return docs[:final_k]

    if index_dir is None or strategy is None or embeddings is None:
        raise ValueError("hybrid retrieval requires index_dir, strategy, and embeddings")

    corpus = load_corpus(index_dir, strategy, vectorstore)
    return hybrid_retrieve(
        question,
        corpus,
        embeddings,
        alpha=alpha,
        fetch_k=fetch_k,
        top_k=final_k,
    )


def build_llm(
    *,
    api_key: str,
    base_url: str,
    model: str,
) -> Any:
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        api_key=api_key,
        base_url=base_url,
        model=model,
        temperature=0,
    )


def build_rag_chain(
    vectorstore: Chroma,
    llm: Any,
    *,
    fetch_k: int,
    final_k: int,
    retrieval: RetrievalMode,
    alpha: float,
    index_dir: Path,
    strategy: Strategy,
    embeddings: Any,
) -> Any:
    def get_context(question: str) -> str:
        docs = retrieve_documents(
            vectorstore,
            question,
            fetch_k=fetch_k,
            final_k=final_k,
            retrieval=retrieval,
            alpha=alpha,
            index_dir=index_dir,
            strategy=strategy,
            embeddings=embeddings,
        )
        return build_context(docs)

    return (
        {
            "question": RunnablePassthrough(),
            "context": lambda question: get_context(question),
        }
        | ANSWER_PROMPT
        | llm
        | StrOutputParser()
    )


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
    parser.add_argument("--api-key", default=get_setting("deepseek_api_key"))
    parser.add_argument(
        "--base-url",
        default=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
    )
    parser.add_argument("--model", default=os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"))
    parser.add_argument(
        "--show-evidence",
        action="store_true",
        help="Print retrieved evidence before the answer",
    )
    parser.add_argument("--embedding-model-path", default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    index_dir = args.index.expanduser().resolve()
    strategy: Strategy = args.strategy  # type: ignore[assignment]
    retrieval: RetrievalMode = args.retrieval  # type: ignore[assignment]

    embeddings = build_embeddings(args.embedding_model_path)
    vectorstore = load_vectorstore(
        index_dir,
        strategy,
        args.embedding_model_path,
    )
    if not args.api_key:
        raise RuntimeError("Missing API key. Pass --api-key or set DEEPSEEK_API_KEY.")

    docs = retrieve_documents(
        vectorstore,
        args.question,
        fetch_k=args.fetch_k,
        final_k=args.top_k,
        retrieval=retrieval,
        alpha=args.alpha,
        index_dir=index_dir,
        strategy=strategy,
        embeddings=embeddings,
    )
    context = build_context(docs)

    if args.show_evidence:
        print("=== Retrieved evidence ===")
        if retrieval == "hybrid":
            corpus = load_corpus(index_dir, strategy, vectorstore)
            scored = hybrid_retrieve_with_scores(
                args.question,
                corpus,
                embeddings,
                alpha=args.alpha,
                fetch_k=args.fetch_k,
                top_k=args.top_k,
            )
            for rank, (doc, scores) in enumerate(scored, 1):
                print(format_evidence_block(doc, rank))
                print(
                    f"  scores: fused={scores['fused']:.3f} "
                    f"vector={scores['norm_vector']:.3f} bm25={scores['norm_bm25']:.3f}"
                )
                print()
        else:
            print(context)
        print("=== Answer ===")

    llm = build_llm(
        api_key=args.api_key,
        base_url=args.base_url,
        model=args.model,
    )
    chain = build_rag_chain(
        vectorstore,
        llm,
        fetch_k=args.fetch_k,
        final_k=args.top_k,
        retrieval=retrieval,
        alpha=args.alpha,
        index_dir=index_dir,
        strategy=strategy,
        embeddings=embeddings,
    )
    answer = chain.invoke(args.question)
    print(answer)


if __name__ == "__main__":
    main()