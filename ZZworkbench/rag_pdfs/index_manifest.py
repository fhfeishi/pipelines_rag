"""Versioned metadata contract for persisted vector indexes."""

from __future__ import annotations

import json
import warnings
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from rag_pdfs.model_runtime import (
    ModelSpec,
    embedding_contract,
    embedding_fingerprint,
)

INDEX_MANIFEST_NAME = "index_manifest.json"


class EmbeddingIndexContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model_id: str
    fingerprint: str
    contract: dict[str, Any]


class IndexManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    created_at: str
    embedding: EmbeddingIndexContract
    collections: dict[str, int]


def manifest_path(index_dir: str | Path) -> Path:
    return Path(index_dir) / INDEX_MANIFEST_NAME


def write_index_manifest(
    index_dir: str | Path,
    embedding_spec: ModelSpec,
    collection_counts: dict[str, int],
) -> IndexManifest:
    """Atomically persist the embedding contract after all collections succeed."""

    root = Path(index_dir)
    root.mkdir(parents=True, exist_ok=True)
    manifest = IndexManifest(
        created_at=datetime.now(UTC).isoformat(),
        embedding=EmbeddingIndexContract(
            model_id=embedding_spec.id,
            fingerprint=embedding_fingerprint(embedding_spec),
            contract=embedding_contract(embedding_spec),
        ),
        collections=collection_counts,
    )
    path = manifest_path(root)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(manifest.model_dump(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return manifest


def load_index_manifest(index_dir: str | Path) -> IndexManifest | None:
    path = manifest_path(index_dir)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return IndexManifest.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(f"Invalid index manifest: {path}: {exc}") from exc


def validate_index_embedding(
    index_dir: str | Path,
    embedding_spec: ModelSpec,
    *,
    collection_name: str | None = None,
    strict_missing: bool = False,
) -> IndexManifest | None:
    """Reject incompatible embeddings; optionally reject legacy indexes."""

    path = manifest_path(index_dir)
    manifest = load_index_manifest(index_dir)
    if manifest is None:
        message = (
            f"Index manifest not found: {path}. This is a legacy index, so the "
            "embedding contract cannot be verified. Rebuild it with --build-chroma."
        )
        if strict_missing:
            raise RuntimeError(message)
        warnings.warn(message, stacklevel=2)
        return None

    expected = embedding_fingerprint(embedding_spec)
    actual = manifest.embedding.fingerprint
    if actual != expected:
        raise RuntimeError(
            "Embedding/index mismatch: "
            f"index={actual} ({manifest.embedding.model_id}), "
            f"query={expected} ({embedding_spec.id}). "
            "Use the same model catalog/path used for ingest or rebuild the index."
        )
    if collection_name and collection_name not in manifest.collections:
        raise RuntimeError(
            f"Collection {collection_name!r} is not declared in {path}"
        )
    return manifest


__all__ = [
    "INDEX_MANIFEST_NAME",
    "EmbeddingIndexContract",
    "IndexManifest",
    "load_index_manifest",
    "manifest_path",
    "validate_index_embedding",
    "write_index_manifest",
]
