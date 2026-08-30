from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.embeddings import DeterministicFakeEmbedding

from ZZworkbench.rag_langchain.text_retrieval import (
    DEFAULT_CORPUS_ROOT,
    ChunkConfig,
    EmbeddingConfig,
    build_embeddings,
    build_or_reuse_chroma,
    chinese_lexical_tokens,
    load_eval_cases,
    load_txt_documents,
    open_chroma,
    read_index_manifest,
    retrieve,
    retrieve_hybrid,
    split_documents,
)


class TextDocumentTests(unittest.TestCase):
    def test_chinese_tokenizer_keeps_exact_names_without_jieba(self) -> None:
        tokens = chinese_lexical_tokens("珠海110kV黄金输变电工程：主体结构封顶")
        self.assertIn("黄金", tokens)
        self.assertIn("主体", tokens)
        self.assertIn("结构封", tokens)
        self.assertIn("110kv", tokens)

    def test_v4_loads_as_stable_langchain_documents(self) -> None:
        first = load_txt_documents(DEFAULT_CORPUS_ROOT, version="v4")
        second = load_txt_documents(DEFAULT_CORPUS_ROOT, version="v4")

        self.assertEqual(8, len(first))
        self.assertEqual([doc.id for doc in first], [doc.id for doc in second])
        self.assertTrue(all(doc.id and doc.id.startswith("txt-") for doc in first))
        self.assertTrue(all(doc.metadata["corpus_version"] == "v4" for doc in first))
        self.assertTrue(
            all(
                isinstance(value, (str, int, float, bool))
                for doc in first
                for value in doc.metadata.values()
            )
        )

    def test_chinese_paragraph_chunking_is_deterministic(self) -> None:
        documents = load_txt_documents(DEFAULT_CORPUS_ROOT, version="v4")
        config = ChunkConfig(chunk_size=872, chunk_overlap=160)
        first = split_documents(documents, config)
        second = split_documents(documents, config)

        self.assertEqual(63, len(first))
        self.assertEqual([chunk.id for chunk in first], [chunk.id for chunk in second])
        self.assertLessEqual(max(len(chunk.page_content) for chunk in first), 872)
        target = next(
            chunk for chunk in first if "主体结构封顶" in chunk.page_content
        )
        self.assertIn("2024年10月24日", target.page_content)
        self.assertEqual(target.id, target.metadata["chunk_id"])
        self.assertIn("start_index", target.metadata)
        self.assertIn("document_id", target.metadata)


class TextIndexTests(unittest.TestCase):
    def test_index_manifest_reuse_and_scored_retriever(self) -> None:
        documents = load_txt_documents(DEFAULT_CORPUS_ROOT, version="v4")[:2]
        chunk_config = ChunkConfig(chunk_size=872, chunk_overlap=160)
        chunks = split_documents(documents, chunk_config)
        embeddings = DeterministicFakeEmbedding(size=64)
        embedding_config = EmbeddingConfig(
            backend="local",
            source="huggingface",
            model="deterministic-fake-64",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            first = build_or_reuse_chroma(
                documents,
                chunks,
                embeddings,
                embedding_config,
                persist_directory=temp_dir,
                corpus_root=DEFAULT_CORPUS_ROOT,
                version="v4",
                chunk_config=chunk_config,
            )
            second = build_or_reuse_chroma(
                documents,
                chunks,
                embeddings,
                embedding_config,
                persist_directory=temp_dir,
                corpus_root=DEFAULT_CORPUS_ROOT,
                version="v4",
                chunk_config=chunk_config,
            )

            self.assertFalse(first.reused)
            self.assertTrue(second.reused)
            self.assertEqual(len(chunks), first.document_count)
            self.assertEqual(
                first.fingerprint,
                read_index_manifest(temp_dir)["index_fingerprint"],
            )

            store = open_chroma(temp_dir, embeddings)
            hits = retrieve(store, "主体结构封顶", k=2)
            self.assertEqual(2, len(hits))
            self.assertEqual([1, 2], [hit.rank for hit in hits])
            self.assertTrue(
                all("retrieval_score" in hit.document.metadata for hit in hits)
            )

            hybrid_hits = retrieve_hybrid(
                store,
                "黄金输变电工程主体结构封顶",
                k=2,
                fetch_k=20,
                dense_weight=0.0,
            )
            self.assertIn("主体结构封顶", hybrid_hits[0].document.page_content)
            self.assertEqual(1, hybrid_hits[0].document.metadata["lexical_rank"])

            changed_config = ChunkConfig(chunk_size=700, chunk_overlap=100)
            changed_chunks = split_documents(documents, changed_config)
            with self.assertRaisesRegex(RuntimeError, "does not match"):
                build_or_reuse_chroma(
                    documents,
                    changed_chunks,
                    embeddings,
                    embedding_config,
                    persist_directory=temp_dir,
                    corpus_root=DEFAULT_CORPUS_ROOT,
                    version="v4",
                    chunk_config=changed_config,
                )

    def test_v4_eval_dataset_is_well_formed(self) -> None:
        path = DEFAULT_CORPUS_ROOT.parent / "evals" / "retrieval_v4.jsonl"
        cases = load_eval_cases(path)
        self.assertEqual(8, len(cases))
        self.assertTrue(all(case["expected_source"].endswith(".txt") for case in cases))


class EmbeddingFactoryTests(unittest.TestCase):
    @patch("langchain_huggingface.HuggingFaceEmbeddings")
    def test_local_factory_uses_langchain_huggingface_1_2_contract(self, factory) -> None:
        with tempfile.TemporaryDirectory() as model_dir:
            config = EmbeddingConfig(model=model_dir, source="local")
            build_embeddings(config)

        kwargs = factory.call_args.kwargs
        self.assertEqual(str(Path(model_dir).resolve()), kwargs["model"])
        self.assertNotIn("model_name", kwargs)
        self.assertTrue(kwargs["encode_kwargs"]["normalize_embeddings"])

    @patch("langchain_openai.OpenAIEmbeddings")
    def test_openai_compatible_factory_reads_key_from_named_environment(self, factory) -> None:
        config = EmbeddingConfig(
            backend="openai",
            model="embedding-model",
            base_url="https://models.example/v1",
            api_key_env="TEST_EMBEDDING_KEY",
        )
        with patch.dict("os.environ", {"TEST_EMBEDDING_KEY": "secret"}):
            build_embeddings(config)

        kwargs = factory.call_args.kwargs
        self.assertEqual("embedding-model", kwargs["model"])
        self.assertEqual("https://models.example/v1", kwargs["base_url"])
        self.assertEqual("secret", kwargs["api_key"])
        self.assertNotIn("secret", json.dumps(config.contract()))


if __name__ == "__main__":
    unittest.main()
