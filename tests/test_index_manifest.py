from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.documents import Document

from rag_pdfs.index_manifest import (
    load_index_manifest,
    validate_index_embedding,
    write_index_manifest,
)
from rag_pdfs.ingest_img import build_chroma_indexes
from rag_pdfs.model_runtime import (
    ModelCatalog,
    ModelSpec,
    embedding_fingerprint,
)
from rag_pdfs.runtime import RAGRuntime


class FakeEmbeddings:
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(text)), 1.0, 0.5] for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return [float(len(text)), 1.0, 0.5]


def embedding_spec(*, normalize: bool = True) -> ModelSpec:
    return ModelSpec.model_validate(
        {
            "id": "test-embedding",
            "kind": "embedding",
            "runtime": "sentence_transformers",
            "source": "local",
            "model": "test-model",
            "local_path": "/models/test-model",
            "normalize_embeddings": normalize,
        }
    )


class IndexManifestTests(unittest.TestCase):
    @patch(
        "langchain_openai.OpenAIEmbeddings",
        return_value=FakeEmbeddings(),
    )
    def test_chroma_build_writes_manifest_after_all_collections(self, _embedding) -> None:
        catalog = ModelCatalog.model_validate(
            {
                "roles": {"embedding": "test-embedding"},
                "models": {
                    "test-embedding": {
                        "id": "test-embedding",
                        "kind": "embedding",
                        "runtime": "openai_compatible",
                        "source": "api",
                        "model": "test-embedding-model",
                    }
                },
            }
        )
        docs = {
            key: [
                Document(
                    page_content=f"content for {key}",
                    metadata={"chunk_id": f"{key}-1"},
                )
            ]
            for key in (
                "text_only_chunks",
                "inline_caption_chunks",
                "separate_caption_chunks",
                "separate_mixed_chunks",
            )
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest = build_chroma_indexes(
                out_dir=Path(temp_dir),
                index_docs=docs,
                embedding_model_path=None,
                runtime=RAGRuntime(catalog),
            )
            loaded = load_index_manifest(temp_dir)
        self.assertEqual(manifest, loaded)
        self.assertEqual(
            manifest.collections,
            {
                "text_only": 1,
                "inline_caption": 1,
                "separate_caption": 1,
                "separate_mixed": 1,
            },
        )

    def test_round_trip_and_validate_embedding_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            spec = embedding_spec()
            written = write_index_manifest(
                temp_dir,
                spec,
                {"text_only": 3, "separate_mixed": 5},
            )
            loaded = load_index_manifest(temp_dir)
            validated = validate_index_embedding(
                temp_dir,
                spec,
                collection_name="text_only",
            )
        self.assertEqual(written, loaded)
        self.assertEqual(validated, loaded)
        self.assertEqual(
            written.embedding.fingerprint,
            embedding_fingerprint(spec),
        )

    def test_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            write_index_manifest(temp_dir, embedding_spec(), {"text_only": 1})
            with self.assertRaisesRegex(RuntimeError, "Embedding/index mismatch"):
                validate_index_embedding(temp_dir, embedding_spec(normalize=False))

    def test_legacy_index_warns_or_fails_in_strict_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertWarns(UserWarning):
                self.assertIsNone(
                    validate_index_embedding(temp_dir, embedding_spec())
                )
            with self.assertRaisesRegex(RuntimeError, "manifest not found"):
                validate_index_embedding(
                    temp_dir,
                    embedding_spec(),
                    strict_missing=True,
                )

    def test_undeclared_collection_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            write_index_manifest(temp_dir, embedding_spec(), {"text_only": 1})
            with self.assertRaisesRegex(RuntimeError, "not declared"):
                validate_index_embedding(
                    temp_dir,
                    embedding_spec(),
                    collection_name="separate_mixed",
                )


if __name__ == "__main__":
    unittest.main()
