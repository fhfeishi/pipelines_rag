from __future__ import annotations

import hashlib
import json
from pathlib import Path


def file_hash(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(chunk_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def cache_key(
    path: Path,
    *,
    provider: str,
    language: str | None,
    max_seconds: float | None = None,
) -> str:
    digest = file_hash(path)
    lang = language or "auto"
    clip = f"clip{int(max_seconds)}" if max_seconds is not None else "full"
    return f"{provider}_{lang}_{clip}_{digest[:16]}"


def load_cache(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_cache(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
