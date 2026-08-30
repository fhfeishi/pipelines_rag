from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from rag_pdfs.model_runtime import (
    ModelCatalog,
    ModelFactory,
    ModelRole,
    ModelSpec,
    configure_model_cache_environment,
    embedding_fingerprint,
    load_model_catalog,
)

API_CATALOG = {
    "roles": {
        "answer": "answer_api",
        "flash": "answer_api",
        "vision_ingest": "vision_api",
        "embedding": "embedding_api",
    },
    "models": {
        "answer_api": {
            "id": "answer_api",
            "kind": "chat",
            "runtime": "openai_compatible",
            "source": "api",
            "model": "chat-model",
            "base_url": "https://models.example/v1",
            "api_key_env": "TEST_CHAT_KEY",
        },
        "vision_api": {
            "id": "vision_api",
            "kind": "chat",
            "runtime": "openai_compatible",
            "source": "api",
            "model": "vision-model",
            "capabilities": ["vision"],
        },
        "embedding_api": {
            "id": "embedding_api",
            "kind": "embedding",
            "runtime": "openai_compatible",
            "source": "api",
            "model": "embedding-model",
            "dimensions": 1024,
        },
    },
}


class ModelCatalogTests(unittest.TestCase):
    def test_role_kind_and_vision_capability_are_validated(self) -> None:
        invalid = {**API_CATALOG, "roles": {"vision_ingest": "answer_api"}}
        with self.assertRaises(ValidationError):
            ModelCatalog.model_validate(invalid)

    def test_load_toml_injects_model_ids(self) -> None:
        payload = """
[roles]
answer = "chat"
embedding = "embed"

[models.chat]
kind = "chat"
runtime = "openai_compatible"
source = "api"
model = "chat-model"

[models.embed]
kind = "embedding"
runtime = "openai_compatible"
source = "api"
model = "embed-model"
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "models.toml"
            path.write_text(payload, encoding="utf-8")
            catalog = load_model_catalog(path)
        self.assertEqual(catalog.models["chat"].id, "chat")
        self.assertEqual(catalog.spec_for_role(ModelRole.EMBEDDING).id, "embed")

    def test_embedding_fingerprint_changes_with_index_contract(self) -> None:
        base = ModelSpec.model_validate(API_CATALOG["models"]["embedding_api"])
        changed = base.model_copy(update={"normalize_embeddings": False})
        self.assertNotEqual(
            embedding_fingerprint(base), embedding_fingerprint(changed)
        )

    def test_cache_environment_uses_cache_vars_not_path(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.dict(os.environ, {}, clear=True),
        ):
            configure_model_cache_environment(tmp)
            self.assertEqual(
                os.environ["HF_HOME"], str(Path(tmp).resolve() / "huggingface")
            )
            self.assertIn("MODELSCOPE_CACHE", os.environ)
            self.assertNotIn("PATH", os.environ)


class ModelFactoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = ModelCatalog.model_validate(API_CATALOG)

    @patch("langchain_openai.ChatOpenAI")
    def test_chat_models_are_lazy_and_reused(self, chat_cls) -> None:
        factory = ModelFactory(
            self.catalog, secret_resolver=lambda _: "secret-value"
        )
        first = factory.for_role("answer")
        second = factory.for_role("flash")
        self.assertIs(first, second)
        chat_cls.assert_called_once()
        kwargs = chat_cls.call_args.kwargs
        self.assertEqual(kwargs["model"], "chat-model")
        self.assertEqual(kwargs["api_key"], "secret-value")
        self.assertEqual(kwargs["base_url"], "https://models.example/v1")

    @patch("langchain_openai.OpenAIEmbeddings")
    def test_embedding_dimensions_are_forwarded(self, embedding_cls) -> None:
        factory = ModelFactory(self.catalog)
        factory.for_role("embedding")
        kwargs = embedding_cls.call_args.kwargs
        self.assertEqual(kwargs["model"], "embedding-model")
        self.assertEqual(kwargs["dimensions"], 1024)
        self.assertEqual(kwargs["api_key"], "EMPTY")

    @patch("langchain_openai.ChatOpenAI")
    def test_build_spec_accepts_secret_override_without_persisting_it(
        self,
        chat_cls,
    ) -> None:
        spec = self.catalog.models["answer_api"]
        ModelFactory(self.catalog).build_spec(spec, api_key="cli-secret")
        self.assertEqual(chat_cls.call_args.kwargs["api_key"], "cli-secret")
        self.assertNotIn("cli-secret", spec.model_dump_json())

    def test_missing_local_directory_fails_before_model_loading(self) -> None:
        local = ModelSpec.model_validate(
            {
                "id": "local",
                "kind": "embedding",
                "runtime": "sentence_transformers",
                "source": "local",
                "model": "local-embedding",
                "local_path": "/definitely/not/a/model/directory",
            }
        )
        catalog = ModelCatalog.model_validate(
            {"roles": {"embedding": "local"}, "models": {"local": local}}
        )
        with self.assertRaises(FileNotFoundError):
            ModelFactory(catalog).for_role("embedding")


if __name__ == "__main__":
    unittest.main()
