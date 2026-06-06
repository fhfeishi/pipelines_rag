"""opendataloader-pdf 解析与 layout flatten（实验性 PDF ingest 专用）。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from rag_langchain.pdf_layout import LayoutElement, clean_text, sort_elements_reading_order


def require_java() -> None:
    if shutil.which("java") is None:
        raise RuntimeError(
            "Java was not found on PATH. opendataloader-pdf requires Java 11+."
        )


def run_opendataloader(pdf_path: Path, out_dir: Path, image_dir: Path) -> Path:
    try:
        import opendataloader_pdf
    except ImportError as exc:
        raise RuntimeError(
            "Missing package: opendataloader-pdf. Install it with:\n"
            "    uv add opendataloader-pdf"
        ) from exc

    out_dir.mkdir(parents=True, exist_ok=True)
    image_dir.mkdir(parents=True, exist_ok=True)

    opendataloader_pdf.convert(
        input_path=[str(pdf_path)],
        output_dir=str(out_dir),
        format="json,markdown",
        image_output="external",
        image_format="png",
        image_dir=str(image_dir),
        quiet=False,
    )

    json_files = sorted(out_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
    if not json_files:
        json_files = sorted(out_dir.rglob("*.json"), key=lambda p: p.stat().st_mtime)
    if not json_files:
        raise RuntimeError(f"No JSON output found in {out_dir}")
    return json_files[-1]


def flatten_layout(node: Any, elements: list[LayoutElement]) -> None:
    if isinstance(node, list):
        for item in node:
            flatten_layout(item, elements)
        return

    if not isinstance(node, dict):
        return

    node_type = str(node.get("type") or "").lower()
    content = clean_text(str(node.get("content") or ""))
    source = node.get("source") or node.get("data")

    if node_type or content or source:
        elements.append(
            LayoutElement(
                index=len(elements),
                type=node_type,
                page=node.get("page number") or node.get("page"),
                content=content,
                source=source,
                bbox=node.get("bounding box") or node.get("bbox"),
                raw_id=node.get("id"),
            )
        )

    for key in ("kids", "list items", "rows", "cells"):
        child = node.get(key)
        if child is not None:
            flatten_layout(child, elements)


def load_elements(json_path: Path) -> list[LayoutElement]:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    elements: list[LayoutElement] = []
    flatten_layout(data, elements)
    return sort_elements_reading_order(elements)


def resolve_image_path(source: str, json_path: Path, image_dir: Path) -> Path | None:
    if source.startswith("data:"):
        return None

    source_path = Path(source)
    candidates: list[Path] = []
    if source_path.is_absolute():
        candidates.append(source_path)
    else:
        candidates.extend(
            [
                json_path.parent / source_path,
                image_dir / source_path.name,
                image_dir / source_path,
                Path.cwd() / source_path,
            ]
        )

    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return candidates[0].resolve() if candidates else None


def find_existing_layout_json(out_dir: Path) -> Path:
    json_files = sorted(
        [
            path
            for path in out_dir.glob("*.json")
            if path.name not in {"summary.json"}
        ],
        key=lambda p: p.stat().st_mtime,
    )
    if not json_files:
        raise RuntimeError(f"--skip-parse set, but no layout JSON found in {out_dir}")
    return json_files[-1]


def path_for_storage(path: Path | None, out_dir: Path) -> str:
    """metadata 中优先存相对 out_dir 的路径，便于搬迁索引目录。"""
    if path is None:
        return ""
    try:
        return str(path.resolve().relative_to(out_dir.resolve()))
    except ValueError:
        return str(path.resolve())
