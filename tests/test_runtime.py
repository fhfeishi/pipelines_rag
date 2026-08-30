from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rag_pdfs.model_runtime import ModelCatalog
from rag_pdfs.runtime import RAGRuntime

CATALOG = ModelCatalog.model_validate(
    {
        "roles": {
            "answer": "answer",
            "embedding": "embedding",
        },
        "models": {
            "answer": {
                "id": "answer",
                "kind": "chat",
                "runtime": "openai_compatible",
                "source": "api",
                "model": "answer-model",
                "base_url": "https://models.example/v1",
                "api_key_env": "TEST_ANSWER_KEY",
            },
            "embedding": {
                "id": "embedding",
                "kind": "embedding",
                "runtime": "openai_compatible",
                "source": "api",
                "model": "embedding-model",
            },
        },
    }
)


class RuntimeTests(unittest.TestCase):
    @patch("langchain_openai.ChatOpenAI")
    def test_catalog_models_are_lazy_and_handle_reuses_instance(self, chat_cls) -> None:
        runtime = RAGRuntime(CATALOG)
        with patch.dict("os.environ", {"TEST_ANSWER_KEY": "secret"}):
            first_handle = runtime.chat("answer")
            second_handle = runtime.chat("answer")
            chat_cls.assert_not_called()
            self.assertIs(first_handle.instance, second_handle.instance)
        chat_cls.assert_called_once()

    @patch("langchain_openai.ChatOpenAI")
    def test_cli_values_override_catalog_without_storing_secret(self, chat_cls) -> None:
        handle = RAGRuntime(CATALOG).chat(
            "answer",
            api_key="cli-secret",
            base_url="https://override.example/v1",
            model="override-model",
        )
        _ = handle.instance
        kwargs = chat_cls.call_args.kwargs
        self.assertEqual(kwargs["api_key"], "cli-secret")
        self.assertEqual(kwargs["base_url"], "https://override.example/v1")
        self.assertEqual(kwargs["model"], "override-model")
        self.assertNotIn("cli-secret", handle.spec.model_dump_json())

    def test_legacy_embedding_path_produces_stable_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            first = RAGRuntime().embeddings(model_path=temp_dir)
            second = RAGRuntime().embeddings(model_path=temp_dir)
        self.assertEqual(first.spec.local_path, str(Path(temp_dir).resolve()))
        self.assertEqual(first.fingerprint, second.fingerprint)

    def test_legacy_chat_key_is_checked_only_when_materialized(self) -> None:
        handle = RAGRuntime().chat(
            "answer",
            default_api_key_env="DEFINITELY_MISSING_KEY",
            default_base_url="https://models.example/v1",
            default_model="answer-model",
        )
        self.assertEqual(handle.spec.model, "answer-model")
        with self.assertRaises(RuntimeError):
            _ = handle.instance


if __name__ == "__main__":
    unittest.main()
