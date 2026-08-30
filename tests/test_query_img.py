from __future__ import annotations

import unittest
from pathlib import Path

from langchain_core.documents import Document

from rag_pdfs.query_img import retrieve_evidence


class StubHybridRetriever:
    def __init__(self) -> None:
        self.calls = 0

    def retrieve_with_scores(self, question, *, alpha, fetch_k, top_k):
        self.calls += 1
        return [
            (
                Document(
                    page_content=f"evidence for {question}",
                    metadata={"chunk_id": "chunk-1"},
                ),
                {
                    "vector": 0.8,
                    "bm25": 0.6,
                    "norm_vector": 1.0,
                    "norm_bm25": 1.0,
                    "fused": alpha,
                },
            )
        ][:top_k]


class QueryEvidenceTests(unittest.TestCase):
    def test_hybrid_documents_and_debug_scores_share_one_retrieval(self) -> None:
        retriever = StubHybridRetriever()
        evidence = retrieve_evidence(
            vectorstore=object(),
            question="question",
            fetch_k=12,
            final_k=6,
            retrieval="hybrid",
            alpha=0.5,
            index_dir=Path("."),
            strategy="separate",
            embeddings=object(),
            hybrid_retriever=retriever,
        )
        self.assertEqual(retriever.calls, 1)
        self.assertEqual(evidence.documents[0].metadata["chunk_id"], "chunk-1")
        self.assertEqual(evidence.scores[0]["fused"], 0.5)

if __name__ == "__main__":
    unittest.main()
