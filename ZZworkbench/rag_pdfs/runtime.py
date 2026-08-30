"""Application composition root for RAG model dependencies.

The lower-level model_runtime module knows how to instantiate providers. This
module selects role-bound catalog models or builds backward-compatible CLI
overrides, while keeping secrets out of persistent configuration.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

from rag_pdfs.model_runtime import (
    ModelCatalog,
    ModelFactory,
    ModelKind,
    ModelRole,
    ModelRuntime,
    ModelSource,
    ModelSpec,
    embedding_fingerprint,
    load_model_catalog,
    resolve_secret,
)

MODEL_CONFIG_ENV = "RAG_MODEL_CONFIG"


def get_setting(name: str, default: str = "") -> str:
    """Read a setting from the process first, then configs/.env."""

    if value := os.getenv(name.upper()):
        return value
    try:
        from configs.config import settings

        return str(getattr(settings, name.lower(), default) or default)
    except (AttributeError, ImportError):
        return default


def add_model_config_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--model-config",
        type=Path,
        default=None,
        help=(
            "TOML model catalog. Defaults to RAG_MODEL_CONFIG when set; "
            "otherwise legacy CLI model options remain active."
        ),
    )


@dataclass
class ModelHandle:
    """A resolved model contract with lazy, process-local instantiation."""

    role: ModelRole
    spec: ModelSpec
    origin: str
    _builder: Callable[[], Any] = field(repr=False)

    @cached_property
    def instance(self) -> Any:
        return self._builder()

    @property
    def fingerprint(self) -> str:
        if self.spec.kind is not ModelKind.EMBEDDING:
            raise ValueError("Only embedding handles have an index fingerprint")
        return embedding_fingerprint(self.spec)


class RAGRuntime:
    """Resolve application roles to validated, lazily-built LangChain models."""

    def __init__(
        self,
        catalog: ModelCatalog | None = None,
        *,
        config_path: Path | None = None,
    ) -> None:
        self.catalog = catalog
        self.config_path = config_path
        factory_catalog = catalog or ModelCatalog(roles={}, models={})
        self.factory = ModelFactory(factory_catalog)

    @classmethod
    def from_model_config(cls, path: str | Path | None = None) -> RAGRuntime:
        raw_path = path or os.getenv(MODEL_CONFIG_ENV)
        if not raw_path:
            return cls()
        config_path = Path(raw_path).expanduser().resolve()
        return cls(load_model_catalog(config_path), config_path=config_path)

    @property
    def uses_catalog(self) -> bool:
        return self.catalog is not None

    def embeddings(self, *, model_path: str | None = None) -> ModelHandle:
        """Resolve the embedding role, with the legacy local path as override."""

        if self.catalog is not None and model_path is None:
            spec = self.catalog.spec_for_role(ModelRole.EMBEDDING)
            return ModelHandle(
                role=ModelRole.EMBEDDING,
                spec=spec,
                origin=f"catalog:{self.config_path or '<memory>'}",
                _builder=lambda: self.factory.for_role(ModelRole.EMBEDDING),
            )

        resolved_path = model_path or get_setting("qwen3_embedding_06b_path")
        if not resolved_path:
            raise RuntimeError(
                "Missing embedding model. Set role 'embedding' in --model-config, "
                "pass --embedding-model-path, or set QWEN3_EMBEDDING_06B_PATH."
            )
        local_path = Path(resolved_path).expanduser().resolve()
        spec = ModelSpec(
            id="legacy_local_embedding",
            kind=ModelKind.EMBEDDING,
            runtime=ModelRuntime.SENTENCE_TRANSFORMERS,
            source=ModelSource.LOCAL,
            model=local_path.name or str(local_path),
            local_path=str(local_path),
            capabilities=frozenset({"text_embedding"}),
            device="cpu",
            normalize_embeddings=True,
        )
        return ModelHandle(
            role=ModelRole.EMBEDDING,
            spec=spec,
            origin="legacy:embedding-model-path",
            _builder=lambda: self.factory.build_spec(spec),
        )

    def chat(
        self,
        role: ModelRole | str,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        default_api_key_env: str | None = None,
        default_base_url: str | None = None,
        default_model: str | None = None,
        capabilities: Iterable[str] = ("text",),
    ) -> ModelHandle:
        """Resolve a chat role; explicit CLI values override catalog fields."""

        normalized_role = ModelRole(role)
        explicit_api_override = any(
            value is not None for value in (api_key, base_url, model)
        )

        if self.catalog is not None:
            base_spec = self.catalog.spec_for_role(normalized_role)
            if explicit_api_override and base_spec.runtime is not ModelRuntime.OPENAI_COMPATIBLE:
                raise ValueError(
                    f"CLI API overrides cannot modify non-API model {base_spec.id!r}"
                )
            updates = {
                key: value
                for key, value in {
                    "base_url": base_url,
                    "model": model,
                    "timeout": timeout,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                }.items()
                if value is not None
            }
            spec = base_spec.model_copy(update=updates) if updates else base_spec
            if not updates and api_key is None:
                builder = lambda: self.factory.for_role(normalized_role)
            else:
                builder = lambda: self.factory.build_spec(spec, api_key=api_key)
            return ModelHandle(
                role=normalized_role,
                spec=spec,
                origin=f"catalog:{self.config_path or '<memory>'}",
                _builder=builder,
            )

        resolved_model = model or default_model
        if not resolved_model:
            raise RuntimeError(f"Missing model name for role {normalized_role.value!r}")
        spec = ModelSpec(
            id=f"legacy_{normalized_role.value}_api",
            kind=ModelKind.CHAT,
            runtime=ModelRuntime.OPENAI_COMPATIBLE,
            source=ModelSource.API,
            model=resolved_model,
            base_url=base_url or default_base_url,
            capabilities=frozenset(capabilities),
            timeout=timeout if timeout is not None else 120.0,
            temperature=temperature if temperature is not None else 0.0,
            max_tokens=max_tokens,
        )

        def build_legacy_chat() -> Any:
            resolved_key = api_key
            if resolved_key is None and default_api_key_env:
                resolved_key = resolve_secret(default_api_key_env)
            if default_api_key_env and not resolved_key:
                raise RuntimeError(
                    f"Missing API key for role {normalized_role.value!r}. "
                    f"Pass --api-key or set {default_api_key_env}."
                )
            return self.factory.build_spec(spec, api_key=resolved_key)

        return ModelHandle(
            role=normalized_role,
            spec=spec,
            origin="legacy:cli",
            _builder=build_legacy_chat,
        )


__all__ = [
    "MODEL_CONFIG_ENV",
    "ModelHandle",
    "RAGRuntime",
    "add_model_config_argument",
    "get_setting",
]
