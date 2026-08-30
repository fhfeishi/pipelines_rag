"""Hybrid retrieval: BM25 + vector cosine fusion (no cross-encoder reranker)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import numpy as np
from langchain_chroma import Chroma
from langchain_core.documents import Document
from rank_bm25 import BM25Okapi

Strategy = Literal["text_only", "inline", "separate"]

STRATEGY_JSONL: dict[Strategy, str] = {
    "text_only": "text_only_chunks.jsonl",
    "inline": "inline_caption_chunks.jsonl",
    "separate": "separate_mixed_chunks.jsonl",
}


def tokenize(text: str) -> list[str]:
    """Simple whitespace + lower-case tokenization (English/Chinese mix v1)."""
    return text.lower().split()


def row_to_document(row: dict[str, Any]) -> Document:
    metadata = dict(row.get("metadata") or {})
    if "chunk_id" not in metadata:
        metadata["chunk_id"] = row.get("id")
    for key in ("chunk_type", "source_pdf", "page"):
        if key in row and key not in metadata:
            metadata[key] = row[key]
    return Document(page_content=row["text"], metadata=metadata)


def load_corpus_from_jsonl(index_dir: Path, strategy: Strategy) -> list[Document]:
    jsonl_name = STRATEGY_JSONL[strategy]
    jsonl_path = index_dir / jsonl_name
    if not jsonl_path.exists():
        raise FileNotFoundError(f"Chunk jsonl not found: {jsonl_path}")
    documents: list[Document] = []
    with jsonl_path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                documents.append(row_to_document(json.loads(line)))
    return documents


def load_corpus_from_chroma(vectorstore: Chroma) -> list[Document]:
    data = vectorstore.get(include=["documents", "metadatas"])
    documents: list[Document] = []
    for text, metadata in zip(data["documents"], data["metadatas"], strict=True):
        if text:
            documents.append(Document(page_content=text, metadata=metadata or {}))
    return documents


def load_corpus_and_vectors_from_chroma(
    vectorstore: Chroma,
) -> tuple[list[Document], np.ndarray]:
    """Load stored documents and embeddings from Chroma for hybrid experiments."""
    data = vectorstore.get(include=["documents", "metadatas", "embeddings"])
    documents: list[Document] = []
    vectors: list[Any] = []
    for text, metadata, embedding in zip(
        data["documents"],
        data["metadatas"],
        data["embeddings"],
        strict=True,
    ):
        if text:
            documents.append(Document(page_content=text, metadata=metadata or {}))
            vectors.append(embedding)
    return documents, np.asarray(vectors, dtype=np.float64)


def load_corpus(index_dir: Path, strategy: Strategy, vectorstore: Chroma) -> list[Document]:
    jsonl_path = index_dir / STRATEGY_JSONL[strategy]
    if jsonl_path.exists():
        return load_corpus_from_jsonl(index_dir, strategy)
    return load_corpus_from_chroma(vectorstore)


def min_max_normalize(scores: dict[str, float]) -> dict[str, float]:
    if not scores:
        return {}
    values = list(scores.values())
    lo, hi = min(values), max(values)
    if hi == lo:
        return {key: 1.0 for key in scores}
    return {key: (value - lo) / (hi - lo) for key, value in scores.items()}


def chunk_key(doc: Document, index: int) -> str:
    return str(doc.metadata.get("chunk_id") or f"idx-{index}")


def vector_cosine_scores(
    question: str,
    documents: list[Document],
    embeddings: Any,
) -> dict[str, float]:
    if not documents:
        return {}
    query_vec = np.asarray(embeddings.embed_query(question), dtype=np.float64)
    doc_vecs = np.asarray(
        embeddings.embed_documents([doc.page_content for doc in documents]),
        dtype=np.float64,
    )
    raw_scores = doc_vecs @ query_vec
    return {
        chunk_key(doc, index): float(raw_scores[index])
        for index, doc in enumerate(documents)
    }


def bm25_scores(question: str, documents: list[Document]) -> dict[str, float]:
    if not documents:
        return {}
    tokenized_corpus = [tokenize(doc.page_content) for doc in documents]
    bm25 = BM25Okapi(tokenized_corpus)
    raw = bm25.get_scores(tokenize(question))
    return {
        chunk_key(doc, index): float(raw[index]) for index, doc in enumerate(documents)
    }


class HybridRetriever:
    """Reusable BM25 + vector retriever for experiment sweeps.

    The function helpers are fine for one-off queries, but experiment runs compare
    many questions, strategies, and alpha values. This class caches tokenized
    corpus, BM25, and document vectors so each query only embeds the question.
    """

    def __init__(
        self,
        documents: list[Document],
        embeddings: Any,
        *,
        doc_vectors: np.ndarray | None = None,
    ):
        self.documents = documents
        self.embeddings = embeddings
        self.keys = [chunk_key(doc, index) for index, doc in enumerate(documents)]
        self.key_to_doc = {
            key: doc for key, doc in zip(self.keys, documents, strict=True)
        }
        self.bm25 = (
            BM25Okapi([tokenize(doc.page_content) for doc in documents])
            if documents
            else None
        )
        if not documents:
            self.doc_vecs = np.empty((0, 0), dtype=np.float64)
        elif doc_vectors is not None:
            self.doc_vecs = np.asarray(doc_vectors, dtype=np.float64)
        else:
            self.doc_vecs = np.asarray(
                embeddings.embed_documents([doc.page_content for doc in documents]),
                dtype=np.float64,
            )

    def vector_scores(self, question: str) -> dict[str, float]:
        if not self.documents:
            return {}
        query_vec = np.asarray(self.embeddings.embed_query(question), dtype=np.float64)
        raw_scores = self.doc_vecs @ query_vec
        return {
            key: float(raw_scores[index]) for index, key in enumerate(self.keys)
        }

    def bm25_scores(self, question: str) -> dict[str, float]:
        if not self.documents:
            return {}
        if self.bm25 is None:
            return {}
        raw = self.bm25.get_scores(tokenize(question))
        return {
            key: float(raw[index]) for index, key in enumerate(self.keys)
        }

    def retrieve(
        self,
        question: str,
        *,
        alpha: float = 0.5,
        fetch_k: int,
        top_k: int,
    ) -> list[Document]:
        scored = self.retrieve_with_scores(
            question,
            alpha=alpha,
            fetch_k=fetch_k,
            top_k=top_k,
        )
        return [doc for doc, _scores in scored]

    def retrieve_with_scores(
        self,
        question: str,
        *,
        alpha: float = 0.5,
        fetch_k: int,
        top_k: int,
    ) -> list[tuple[Document, dict[str, float]]]:
        if not self.documents:
            return []

        vec_scores = self.vector_scores(question)
        bm25_map = self.bm25_scores(question)
        norm_vector = min_max_normalize(vec_scores)
        norm_bm25 = min_max_normalize(bm25_map)
        fused = fuse_scores(vec_scores, bm25_map, alpha=alpha)

        ranked_keys = sorted(fused, key=fused.get, reverse=True)[:fetch_k]
        results: list[tuple[Document, dict[str, float]]] = []
        for key in ranked_keys[:top_k]:
            doc = self.key_to_doc.get(key)
            if doc is None:
                continue
            results.append(
                (
                    doc,
                    {
                        "vector": vec_scores.get(key, 0.0),
                        "bm25": bm25_map.get(key, 0.0),
                        "norm_vector": norm_vector.get(key, 0.0),
                        "norm_bm25": norm_bm25.get(key, 0.0),
                        "fused": fused.get(key, 0.0),
                    },
                )
            )
        return results


def fuse_scores(
    vector_scores: dict[str, float],
    bm25_scores_map: dict[str, float],
    *,
    alpha: float,
) -> dict[str, float]:
    keys = set(vector_scores) | set(bm25_scores_map)
    norm_vector = min_max_normalize({k: vector_scores.get(k, 0.0) for k in keys})
    norm_bm25 = min_max_normalize({k: bm25_scores_map.get(k, 0.0) for k in keys})
    return {
        key: alpha * norm_vector[key] + (1.0 - alpha) * norm_bm25[key] for key in keys
    }


def hybrid_retrieve(
    question: str,
    documents: list[Document],
    embeddings: Any,
    *,
    alpha: float = 0.5,
    fetch_k: int,
    top_k: int,
) -> list[Document]:
    """Fuse normalized BM25 + cosine scores; return top_k after fetch_k pool."""
    if not documents:
        return []

    vec_scores = vector_cosine_scores(question, documents, embeddings)
    bm25_map = bm25_scores(question, documents)
    fused = fuse_scores(vec_scores, bm25_map, alpha=alpha)

    ranked_keys = sorted(fused, key=fused.get, reverse=True)[:fetch_k]
    key_to_doc = {chunk_key(doc, index): doc for index, doc in enumerate(documents)}
    return [key_to_doc[key] for key in ranked_keys[:top_k] if key in key_to_doc]


def hybrid_retrieve_with_scores(
    question: str,
    documents: list[Document],
    embeddings: Any,
    *,
    alpha: float = 0.5,
    fetch_k: int,
    top_k: int,
) -> list[tuple[Document, dict[str, float]]]:
    """Like hybrid_retrieve but attach per-signal scores for debugging."""
    if not documents:
        return []

    vec_scores = vector_cosine_scores(question, documents, embeddings)
    bm25_map = bm25_scores(question, documents)
    norm_vector = min_max_normalize(vec_scores)
    norm_bm25 = min_max_normalize(bm25_map)
    fused = fuse_scores(vec_scores, bm25_map, alpha=alpha)

    ranked_keys = sorted(fused, key=fused.get, reverse=True)[:fetch_k]
    key_to_doc = {chunk_key(doc, index): doc for index, doc in enumerate(documents)}
    results: list[tuple[Document, dict[str, float]]] = []
    for key in ranked_keys[:top_k]:
        doc = key_to_doc.get(key)
        if doc is None:
            continue
        results.append(
            (
                doc,
                {
                    "vector": vec_scores.get(key, 0.0),
                    "bm25": bm25_map.get(key, 0.0),
                    "norm_vector": norm_vector.get(key, 0.0),
                    "norm_bm25": norm_bm25.get(key, 0.0),
                    "fused": fused.get(key, 0.0),
                },
            )
        )
    return results
