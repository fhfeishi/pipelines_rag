"""Typed model catalog and LangChain model factory.

The catalog keeps four concerns separate: model kind, application role,
runtime adapter, and model source. The canonical ingest/query CLIs do not use
this factory yet; it is a small runtime boundary they can migrate to without
changing parser or retrieval contracts at the same time.
"""

from __future__ import annotations

import hashlib
import json
import os
import tomllib
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ModelKind(StrEnum):
    CHAT = "chat"
    EMBEDDING = "embedding"


class ModelRuntime(StrEnum):
    OPENAI_COMPATIBLE = "openai_compatible"
    SENTENCE_TRANSFORMERS = "sentence_transformers"
    TRANSFORMERS = "transformers"


class ModelSource(StrEnum):
    API = "api"
    HUGGINGFACE = "huggingface"
    MODELSCOPE = "modelscope"
    LOCAL = "local"


class ModelRole(StrEnum):
    ANSWER = "answer"
    EMBEDDING = "embedding"
    EVALUATOR = "evaluator"
    FLASH = "flash"
    QUERY_REWRITE = "query_rewrite"
    SUMMARY = "summary"
    VISION_INGEST = "vision_ingest"


class ModelSpec(BaseModel):
    """Non-secret configuration for one model implementation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    kind: ModelKind
    runtime: ModelRuntime
    source: ModelSource
    model: str

    api_key_env: str | None = None
    base_url: str | None = None
    local_path: str | None = None
    cache_dir: str | None = None
    revision: str | None = None

    capabilities: frozenset[str] = Field(default_factory=frozenset)
    device: str = "cpu"
    timeout: float = 120.0
    temperature: float = 0.0
    max_tokens: int | None = None
    dimensions: int | None = None
    batch_size: int = 32
    normalize_embeddings: bool = True
    trust_remote_code: bool = False
    show_progress: bool = False

    model_kwargs: dict[str, Any] = Field(default_factory=dict)
    generation_kwargs: dict[str, Any] = Field(default_factory=dict)
    encode_kwargs: dict[str, Any] = Field(default_factory=dict)
    query_encode_kwargs: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_runtime_contract(self) -> ModelSpec:
        if self.source is ModelSource.API:
            if self.runtime is not ModelRuntime.OPENAI_COMPATIBLE:
                raise ValueError("API sources currently require openai_compatible runtime")
        elif self.runtime is ModelRuntime.OPENAI_COMPATIBLE:
            raise ValueError("openai_compatible runtime requires source='api'")

        if self.source is ModelSource.LOCAL and not self.local_path:
            raise ValueError("local sources require local_path")

        if self.kind is ModelKind.EMBEDDING:
            allowed = {
                ModelRuntime.OPENAI_COMPATIBLE,
                ModelRuntime.SENTENCE_TRANSFORMERS,
            }
            if self.runtime not in allowed:
                raise ValueError(
                    "embedding models require openai_compatible or sentence_transformers"
                )
        elif self.runtime not in {
            ModelRuntime.OPENAI_COMPATIBLE,
            ModelRuntime.TRANSFORMERS,
        }:
            raise ValueError("chat models require openai_compatible or transformers")

        if self.batch_size < 1:
            raise ValueError("batch_size must be positive")
        if self.dimensions is not None and self.dimensions < 1:
            raise ValueError("dimensions must be positive")
        return self


class ModelCatalog(BaseModel):
    """A validated registry plus role-to-model bindings."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    cache_root: str | None = None
    roles: dict[ModelRole, str]
    models: dict[str, ModelSpec]

    @model_validator(mode="after")
    def validate_role_bindings(self) -> ModelCatalog:
        for model_id, spec in self.models.items():
            if spec.id != model_id:
                raise ValueError(
                    f"model key {model_id!r} does not match spec id {spec.id!r}"
                )

        for role, model_id in self.roles.items():
            spec = self.models.get(model_id)
            if spec is None:
                raise ValueError(
                    f"role {role.value!r} references unknown model {model_id!r}"
                )
            expected_kind = (
                ModelKind.EMBEDDING
                if role is ModelRole.EMBEDDING
                else ModelKind.CHAT
            )
            if spec.kind is not expected_kind:
                raise ValueError(
                    f"role {role.value!r} requires {expected_kind.value}, "
                    f"but {model_id!r} is {spec.kind.value}"
                )
            if (
                role is ModelRole.VISION_INGEST
                and "vision" not in spec.capabilities
            ):
                raise ValueError(
                    f"vision_ingest model {model_id!r} must declare capability 'vision'"
                )
        return self

    def spec_for_role(self, role: ModelRole | str) -> ModelSpec:
        normalized = ModelRole(role)
        try:
            return self.models[self.roles[normalized]]
        except KeyError as exc:
            raise KeyError(f"No model configured for role {normalized.value!r}") from exc


def load_model_catalog(path: str | Path) -> ModelCatalog:
    """Load a model catalog from TOML without reading or storing API secrets."""

    catalog_path = Path(path).expanduser()
    with catalog_path.open("rb") as handle:
        raw = tomllib.load(handle)

    raw_models = raw.get("models", {})
    models = {
        model_id: ModelSpec.model_validate({"id": model_id, **values})
        for model_id, values in raw_models.items()
    }
    return ModelCatalog.model_validate(
        {
            "cache_root": raw.get("cache_root"),
            "roles": raw.get("roles", {}),
            "models": models,
        }
    )


def resolve_secret(env_name: str) -> str:
    """Resolve a secret from the process first, then configs/.env settings."""

    if value := os.getenv(env_name):
        return value.strip()
    try:
        from configs.config import settings

        return str(getattr(settings, env_name.lower(), "") or "").strip()
    except (AttributeError, ImportError):
        return ""


def configure_model_cache_environment(cache_root: str | Path | None) -> None:
    """Configure model caches before importing Hugging Face or ModelScope.

    Model caches are directories, not executable search paths, so this function
    intentionally never modifies PATH.
    """

    if not cache_root:
        return
    root = Path(cache_root).expanduser().resolve()
    os.environ.setdefault("HF_HOME", str(root / "huggingface"))
    os.environ.setdefault(
        "HUGGINGFACE_HUB_CACHE", str(root / "huggingface" / "hub")
    )
    os.environ.setdefault("MODELSCOPE_CACHE", str(root / "modelscope"))


def embedding_contract(spec: ModelSpec) -> dict[str, Any]:
    """Return the secret-free settings that define an embedding index."""

    if spec.kind is not ModelKind.EMBEDDING:
        raise ValueError("embedding_contract requires an embedding model")
    return {
        "runtime": spec.runtime.value,
        "source": spec.source.value,
        "model": spec.model,
        "local_path": (
            str(Path(spec.local_path).expanduser()) if spec.local_path else None
        ),
        "revision": spec.revision,
        "dimensions": spec.dimensions,
        "normalize_embeddings": spec.normalize_embeddings,
        "model_kwargs": spec.model_kwargs,
        "encode_kwargs": spec.encode_kwargs,
        "query_encode_kwargs": spec.query_encode_kwargs,
    }


def embedding_fingerprint(spec: ModelSpec) -> str:
    """Return a stable, secret-free identity for an embedding index contract."""

    payload = embedding_contract(spec)
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


SecretResolver = Callable[[str], str]


class ModelFactory:
    """Lazily build and reuse LangChain chat/embedding model instances."""

    def __init__(
        self,
        catalog: ModelCatalog,
        *,
        secret_resolver: SecretResolver = resolve_secret,
    ) -> None:
        self.catalog = catalog
        self.secret_resolver = secret_resolver
        self._instances: dict[str, Any] = {}
        configure_model_cache_environment(catalog.cache_root)

    def for_role(self, role: ModelRole | str) -> Any:
        return self.build(self.catalog.spec_for_role(role).id)

    def build(self, model_id: str) -> Any:
        if model_id in self._instances:
            return self._instances[model_id]
        try:
            spec = self.catalog.models[model_id]
        except KeyError as exc:
            raise KeyError(f"Unknown model id {model_id!r}") from exc

        instance = self.build_spec(spec)
        self._instances[model_id] = instance
        return instance

    def build_spec(
        self,
        spec: ModelSpec,
        *,
        api_key: str | None = None,
    ) -> Any:
        """Build an ad-hoc validated spec without adding it to the instance cache."""

        if spec.kind is ModelKind.CHAT:
            return self._build_chat(spec, api_key_override=api_key)
        return self._build_embeddings(spec, api_key_override=api_key)

    def clear(self) -> None:
        """Drop process-local instances; this does not delete model caches."""

        self._instances.clear()

    def _required_api_key(
        self,
        spec: ModelSpec,
        api_key_override: str | None = None,
    ) -> str:
        if api_key_override is not None:
            return api_key_override or "EMPTY"
        if not spec.api_key_env:
            return "EMPTY"
        value = self.secret_resolver(spec.api_key_env)
        if not value:
            raise RuntimeError(
                f"Missing API key for model {spec.id!r}: set {spec.api_key_env}"
            )
        return value

    def _effective_cache_dir(self, spec: ModelSpec) -> str | None:
        if spec.cache_dir:
            return str(Path(spec.cache_dir).expanduser())
        if not self.catalog.cache_root:
            return None
        provider_dir = (
            "modelscope"
            if spec.source is ModelSource.MODELSCOPE
            else "huggingface"
        )
        return str(Path(self.catalog.cache_root).expanduser() / provider_dir)

    def _resolve_model_reference(self, spec: ModelSpec) -> str:
        if spec.source is ModelSource.API:
            return spec.model
        if spec.source is ModelSource.LOCAL:
            path = Path(spec.local_path or "").expanduser().resolve()
            if not path.is_dir():
                raise FileNotFoundError(
                    f"Local model directory for {spec.id!r} does not exist: {path}"
                )
            return str(path)
        if spec.source is ModelSource.HUGGINGFACE:
            return spec.model
        if spec.source is ModelSource.MODELSCOPE:
            try:
                from modelscope.hub.snapshot_download import snapshot_download
            except ImportError as exc:
                raise RuntimeError(
                    "ModelScope cached mode requires the optional dependency: "
                    "uv sync --extra modelscope"
                ) from exc
            kwargs: dict[str, Any] = {}
            if cache_dir := self._effective_cache_dir(spec):
                kwargs["cache_dir"] = cache_dir
            if spec.revision:
                kwargs["revision"] = spec.revision
            return str(snapshot_download(spec.model, **kwargs))
        raise AssertionError(f"Unhandled model source: {spec.source}")

    def _build_chat(
        self,
        spec: ModelSpec,
        *,
        api_key_override: str | None = None,
    ) -> Any:
        if spec.runtime is ModelRuntime.OPENAI_COMPATIBLE:
            from langchain_openai import ChatOpenAI

            kwargs: dict[str, Any] = {
                "model": spec.model,
                "api_key": self._required_api_key(spec, api_key_override),
                "temperature": spec.temperature,
                "timeout": spec.timeout,
                **spec.model_kwargs,
            }
            if spec.base_url:
                kwargs["base_url"] = spec.base_url
            if spec.max_tokens is not None:
                kwargs["max_tokens"] = spec.max_tokens
            return ChatOpenAI(**kwargs)

        from langchain_huggingface import ChatHuggingFace, HuggingFacePipeline

        model_reference = self._resolve_model_reference(spec)
        model_kwargs = {
            "trust_remote_code": spec.trust_remote_code,
            **spec.model_kwargs,
        }
        if spec.revision and spec.source is ModelSource.HUGGINGFACE:
            model_kwargs.setdefault("revision", spec.revision)
        if cache_dir := self._effective_cache_dir(spec):
            model_kwargs.setdefault("cache_dir", cache_dir)

        pipeline_kwargs = {
            "max_new_tokens": spec.max_tokens or 1024,
            "do_sample": spec.temperature > 0,
            **spec.generation_kwargs,
        }
        if spec.temperature > 0:
            pipeline_kwargs.setdefault("temperature", spec.temperature)

        device: int | None = -1
        device_map: str | None = None
        if spec.device == "auto":
            device = None
            device_map = "auto"
        elif spec.device.startswith("cuda"):
            device = int(spec.device.partition(":")[2] or "0")

        pipeline = HuggingFacePipeline.from_model_id(
            model_id=model_reference,
            task="text-generation",
            device=device,
            device_map=device_map,
            model_kwargs=model_kwargs,
            pipeline_kwargs=pipeline_kwargs,
            batch_size=spec.batch_size,
        )
        return ChatHuggingFace(
            llm=pipeline,
            model_id=spec.model,
            temperature=spec.temperature,
            max_tokens=spec.max_tokens,
        )

    def _build_embeddings(
        self,
        spec: ModelSpec,
        *,
        api_key_override: str | None = None,
    ) -> Any:
        if spec.runtime is ModelRuntime.OPENAI_COMPATIBLE:
            from langchain_openai import OpenAIEmbeddings

            kwargs: dict[str, Any] = {
                "model": spec.model,
                "api_key": self._required_api_key(spec, api_key_override),
                "request_timeout": spec.timeout,
                **spec.model_kwargs,
            }
            if spec.base_url:
                kwargs["base_url"] = spec.base_url
            if spec.dimensions is not None:
                kwargs["dimensions"] = spec.dimensions
            return OpenAIEmbeddings(**kwargs)

        from langchain_huggingface import HuggingFaceEmbeddings

        model_reference = self._resolve_model_reference(spec)
        model_kwargs = {
            "device": spec.device,
            "trust_remote_code": spec.trust_remote_code,
            **spec.model_kwargs,
        }
        if spec.revision and spec.source is ModelSource.HUGGINGFACE:
            model_kwargs.setdefault("revision", spec.revision)

        encode_kwargs = {
            "normalize_embeddings": spec.normalize_embeddings,
            "batch_size": spec.batch_size,
            **spec.encode_kwargs,
        }
        query_encode_kwargs = {
            "normalize_embeddings": spec.normalize_embeddings,
            **spec.query_encode_kwargs,
        }
        return HuggingFaceEmbeddings(
            model=model_reference,
            cache_folder=self._effective_cache_dir(spec),
            model_kwargs=model_kwargs,
            encode_kwargs=encode_kwargs,
            query_encode_kwargs=query_encode_kwargs,
            show_progress=spec.show_progress,
        )


__all__ = [
    "ModelCatalog",
    "ModelFactory",
    "ModelKind",
    "ModelRole",
    "ModelRuntime",
    "ModelSource",
    "ModelSpec",
    "configure_model_cache_environment",
    "embedding_contract",
    "embedding_fingerprint",
    "load_model_catalog",
]
