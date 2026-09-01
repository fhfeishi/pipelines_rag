from __future__ import annotations

import unittest

from langchain_core.documents import Document

from ZZworkbench.project_progress_reliable import (
    ProjectProgressKnowledgeBase,
    evaluate_reliable_lookup,
    normalize_lookup_text,
)
from ZZworkbench.rag_langchain.text_retrieval import (
    DEFAULT_CORPUS_ROOT,
    ChunkConfig,
    RetrievalHit,
    load_eval_cases,
    load_txt_documents,
    split_documents,
)


class ProjectProgressReliableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.documents = load_txt_documents(DEFAULT_CORPUS_ROOT, version="v4")
        cls.chunks = split_documents(cls.documents, ChunkConfig())
        cls.knowledge_base = ProjectProgressKnowledgeBase(cls.documents, cls.chunks)

    def test_v4_record_audit_is_complete(self) -> None:
        audit = self.knowledge_base.audit()

        self.assertEqual(8, audit["documents"])
        self.assertEqual(63, audit["chunks"])
        self.assertEqual(541, audit["records"])
        self.assertEqual(0, audit["records_without_chunk"])
        self.assertEqual(0, audit["records_without_start"])
        self.assertEqual(0, audit["records_without_end"])

    def test_alias_normalization_keeps_voltage_and_project_name(self) -> None:
        normalized = normalize_lookup_text("珠海 110kV 黄金输变电工程")

        self.assertEqual("珠海110千伏黄金输变电工程", normalized)

    def test_existing_retrieval_cases_pass_the_evidence_gate(self) -> None:
        cases = load_eval_cases(
            DEFAULT_CORPUS_ROOT.parent / "evals" / "retrieval_v4.jsonl"
        )
        report = evaluate_reliable_lookup(self.knowledge_base, cases)

        self.assertEqual(8, report["cases"])
        self.assertEqual(1.0, report["pass_rate"])

    def test_reliability_cases_cover_alias_ambiguity_missing_and_fields(self) -> None:
        cases = load_eval_cases(
            DEFAULT_CORPUS_ROOT.parent / "evals" / "reliability_v4.jsonl"
        )
        report = evaluate_reliable_lookup(self.knowledge_base, cases)

        self.assertEqual(5, report["cases"])
        self.assertEqual(1.0, report["pass_rate"])

    def test_ambiguous_task_abstains_until_project_is_known(self) -> None:
        result = self.knowledge_base.lookup("施工准备什么时候完成？")

        self.assertEqual("ambiguous", result.status)
        self.assertGreater(len({record.source_name for record in result.records}), 1)
        self.assertIn("请补充工程范围", result.answer)

    def test_parent_constraint_selects_the_parent_record(self) -> None:
        result = self.knowledge_base.lookup(
            "110千伏节点计划中父任务试桩什么时候完成？"
        )

        self.assertEqual("exact", result.status)
        self.assertEqual("4", result.records[0].task_id)
        self.assertTrue(result.records[0].is_parent)
        self.assertEqual("2028年2月29日", result.records[0].end_date)

    def test_structured_lookup_returns_all_requested_fields(self) -> None:
        result = self.knowledge_base.lookup_fields(
            project="南溪输变电工程",
            task="地基基础施工",
            fields=("start_date", "end_date", "duration"),
        )

        self.assertEqual("exact", result.status)
        record = result.records[0]
        self.assertEqual("2025年8月2日", record.start_date)
        self.assertEqual("2025年10月22日", record.end_date)
        self.assertEqual("82工作日", record.duration)

    def test_candidate_retrieval_must_cover_the_exact_record(self) -> None:
        record = self.knowledge_base.lookup(
            "黄金输变电工程主体结构封顶什么时候完成？"
        ).records[0]
        candidate = Document(
            id=record.chunk_id,
            page_content=record.raw_text,
            metadata={
                "chunk_id": record.chunk_id,
                "source_name": record.source_name,
                "dense_rank": 2,
                "lexical_rank": 1,
            },
        )
        calls: list[str] = []

        def retrieve_once(query: str) -> list[RetrievalHit]:
            calls.append(query)
            return [RetrievalHit(candidate, rank=1, score=0.9, distance=0.1)]

        result = self.knowledge_base.lookup(
            "黄金输变电工程主体结构封顶什么时候完成？",
            retriever=retrieve_once,
        )

        self.assertEqual("exact", result.status)
        self.assertEqual(1, result.retrieval_calls)
        self.assertEqual(1, len(calls))
        self.assertTrue(result.diagnostics["record_in_retrieved_chunks"])
        self.assertEqual(1, result.diagnostics["lexical_rank"])

    def test_missing_candidate_is_insufficient_instead_of_bypassing_retrieval(self) -> None:
        unrelated = Document(
            id="unrelated",
            page_content="不相关候选",
            metadata={"chunk_id": "unrelated", "source_name": "other.txt"},
        )
        result = self.knowledge_base.lookup(
            "黄金输变电工程主体结构封顶什么时候完成？",
            retriever=lambda _: [
                RetrievalHit(unrelated, rank=1, score=0.9, distance=0.1)
            ],
        )

        self.assertEqual("insufficient", result.status)
        self.assertFalse(result.diagnostics["record_in_retrieved_chunks"])


if __name__ == "__main__":
    unittest.main()
