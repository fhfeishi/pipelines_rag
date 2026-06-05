"""
Index PDF images for RAG.

Pipeline:
1. Parse a local PDF with opendataloader-pdf.
2. Extract image elements and nearby text context from the JSON layout.
3. Send each image + context to an OpenAI-compatible vision/chat API.
4. Write both "separate caption chunks" and "inline caption chunks".

Usage:
    pip install -U opendataloader-pdf
    export DEEPSEEK_API_KEY="..."
    python index_images_for_rag_pipeline.py /path/to/file.pdf --out ./rag_image_index

Notes:
    opendataloader-pdf requires Java 11+ on PATH.
    The default model/base URL are set for DeepSeek's OpenAI-compatible API, but
    you can override them with --model and --base-url.
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import shutil
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


TEXT_TYPES = {
    "paragraph",
    "heading",
    "caption",
    "list item",
    "text",
    "text block",
}


@dataclass
class LayoutElement:
    index: int
    type: str
    page: int | None
    content: str
    source: str | None
    bbox: Any
    raw_id: int | None


@dataclass
class ImageCaption:
    image_id: str
    page: int | None
    image_path: str
    image_source: str | None
    context_before: str
    context_after: str
    caption: str
    bbox: Any


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def truncate(text: str, max_chars: int) -> str:
    text = clean_text(text)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "..."


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
            "    pip install -U opendataloader-pdf"
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
    return elements


def is_image(element: LayoutElement) -> bool:
    return element.type == "image" and bool(element.source)


def is_text(element: LayoutElement) -> bool:
    return bool(element.content) and (
        element.type in TEXT_TYPES
        or element.type.startswith("heading")
        or element.type in {"table cell"}
    )


def resolve_image_path(source: str, json_path: Path, image_dir: Path) -> Path | None:
    if source.startswith("data:"):
        return None

    source_path = Path(source)
    candidates = []
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


def nearby_context(
    elements: list[LayoutElement],
    image_index: int,
    before_chars: int,
    after_chars: int,
) -> tuple[str, str]:
    before_parts: list[str] = []
    after_parts: list[str] = []

    image_page = elements[image_index].page

    for element in reversed(elements[:image_index]):
        if image_page is not None and element.page != image_page:
            continue
        if is_text(element):
            before_parts.append(element.content)
            if len(clean_text(" ".join(reversed(before_parts)))) >= before_chars:
                break

    for element in elements[image_index + 1 :]:
        if image_page is not None and element.page != image_page:
            continue
        if is_text(element):
            after_parts.append(element.content)
            if len(clean_text(" ".join(after_parts))) >= after_chars:
                break

    return (
        truncate(" ".join(reversed(before_parts)), before_chars),
        truncate(" ".join(after_parts), after_chars),
    )


def image_to_data_url(image_path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(image_path))
    if not mime:
        mime = "image/png"
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def chat_completion(
    *,
    api_key: str,
    base_url: str,
    model: str,
    image_path: Path,
    context_before: str,
    context_after: str,
    timeout: int,
) -> str:
    endpoint = base_url.rstrip("/")
    if not endpoint.endswith("/chat/completions"):
        endpoint = endpoint + "/chat/completions"

    prompt = f"""
Describe this image for a RAG index over technical documentation.

Write a concise but information-dense caption. Prefer factual text that helps
retrieval and answer grounding. If the image contains UI labels, table values,
diagram labels, architecture components, numbers, warnings, or steps, transcribe
them. Mention uncertainty when something is unclear. Do not invent details.

Nearby text before the image:
{context_before or "(none)"}

Nearby text after the image:
{context_after or "(none)"}

Return only the caption text.
""".strip()

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "You create image captions for retrieval-augmented generation indexes.",
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": image_to_data_url(image_path)},
                    },
                ],
            },
        ],
        "temperature": 0,
        "max_tokens": 800,
    }

    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"API request failed: HTTP {exc.code}\n{body}") from exc

    return clean_text(data["choices"][0]["message"]["content"])


def build_captions(
    *,
    elements: list[LayoutElement],
    json_path: Path,
    image_dir: Path,
    api_key: str | None,
    base_url: str,
    model: str,
    before_chars: int,
    after_chars: int,
    timeout: int,
    sleep_seconds: float,
    dry_run: bool,
) -> list[ImageCaption]:
    captions: list[ImageCaption] = []
    image_elements = [element for element in elements if is_image(element)]

    for image_number, element in enumerate(image_elements, 1):
        image_path = resolve_image_path(str(element.source), json_path, image_dir)
        before, after = nearby_context(elements, element.index, before_chars, after_chars)
        image_id = f"image-{image_number:04d}"

        if dry_run:
            caption = (
                "DRY RUN CAPTION. Replace this by running without --dry-run. "
                f"Context before: {before} Context after: {after}"
            )
        else:
            if not api_key:
                raise RuntimeError("Missing DEEPSEEK_API_KEY or --api-key.")
            if image_path is None or not image_path.exists():
                raise RuntimeError(
                    f"Could not resolve extracted image path for source: {element.source}"
                )
            caption = chat_completion(
                api_key=api_key,
                base_url=base_url,
                model=model,
                image_path=image_path,
                context_before=before,
                context_after=after,
                timeout=timeout,
            )
            if sleep_seconds:
                time.sleep(sleep_seconds)

        captions.append(
            ImageCaption(
                image_id=image_id,
                page=element.page,
                image_path=str(image_path) if image_path else "",
                image_source=str(element.source),
                context_before=before,
                context_after=after,
                caption=caption,
                bbox=element.bbox,
            )
        )
        print(f"[captioned] {image_id} page={element.page} {image_path}")

    return captions


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_outputs(
    out_dir: Path,
    pdf_path: Path,
    elements: list[LayoutElement],
    captions: list[ImageCaption],
) -> None:
    caption_by_source = {caption.image_source: caption for caption in captions}

    separate_rows = []
    for caption in captions:
        separate_rows.append(
            {
                "id": caption.image_id,
                "chunk_type": "image_caption",
                "source_pdf": str(pdf_path),
                "page": caption.page,
                "text": caption.caption,
                "image_path": caption.image_path,
                "image_source": caption.image_source,
                "metadata": {
                    "bbox": caption.bbox,
                    "context_before": caption.context_before,
                    "context_after": caption.context_after,
                },
            }
        )

    inline_rows = []
    current_parts: list[str] = []
    chunk_id = 1

    def flush() -> None:
        nonlocal current_parts, chunk_id
        text = clean_text("\n\n".join(current_parts))
        if not text:
            return
        inline_rows.append(
            {
                "id": f"inline-{chunk_id:04d}",
                "chunk_type": "text_with_inline_image_captions",
                "source_pdf": str(pdf_path),
                "text": text,
            }
        )
        chunk_id += 1
        current_parts = []

    for element in elements:
        if is_text(element):
            current_parts.append(element.content)
        elif is_image(element):
            caption = caption_by_source.get(str(element.source))
            if caption:
                current_parts.append(
                    f"[Image caption for {caption.image_id}: {caption.caption}]"
                )

        if sum(len(part) for part in current_parts) >= 1800:
            flush()

    flush()

    write_jsonl(out_dir / "image_captions.jsonl", [asdict(c) for c in captions])
    write_jsonl(out_dir / "separate_caption_chunks.jsonl", separate_rows)
    write_jsonl(out_dir / "inline_caption_chunks.jsonl", inline_rows)

    summary = {
        "source_pdf": str(pdf_path),
        "image_count": len(captions),
        "separate_caption_chunks": len(separate_rows),
        "inline_caption_chunks": len(inline_rows),
        "outputs": {
            "image_captions": str(out_dir / "image_captions.jsonl"),
            "separate_caption_chunks": str(out_dir / "separate_caption_chunks.jsonl"),
            "inline_caption_chunks": str(out_dir / "inline_caption_chunks.jsonl"),
        },
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path, help="Local PDF path")
    parser.add_argument("--out", type=Path, default=Path("rag_image_index"))
    parser.add_argument("--api-key", default=os.getenv("DEEPSEEK_API_KEY"))
    parser.add_argument("--base-url", default=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"))
    parser.add_argument("--model", default=os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"))
    parser.add_argument("--before-chars", type=int, default=1200)
    parser.add_argument("--after-chars", type=int, default=1200)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--sleep", type=float, default=0.0, help="Delay between image API calls")
    parser.add_argument("--dry-run", action="store_true", help="Do not call the API")
    parser.add_argument(
        "--skip-parse",
        action="store_true",
        help="Reuse existing OpenDataLoader JSON/images in --out",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    pdf_path = args.pdf.expanduser().resolve()
    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)

    out_dir = args.out.expanduser().resolve()
    image_dir = out_dir / "images"

    if args.skip_parse:
        json_files = sorted(out_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
        if not json_files:
            raise RuntimeError(f"--skip-parse set, but no JSON found in {out_dir}")
        json_path = json_files[-1]
    else:
        require_java()
        json_path = run_opendataloader(pdf_path, out_dir, image_dir)

    print(f"[json] {json_path}")
    elements = load_elements(json_path)
    print(f"[layout elements] {len(elements)}")
    print(f"[images] {sum(1 for element in elements if is_image(element))}")

    captions = build_captions(
        elements=elements,
        json_path=json_path,
        image_dir=image_dir,
        api_key=args.api_key,
        base_url=args.base_url,
        model=args.model,
        before_chars=args.before_chars,
        after_chars=args.after_chars,
        timeout=args.timeout,
        sleep_seconds=args.sleep,
        dry_run=args.dry_run,
    )
    write_outputs(out_dir, pdf_path, elements, captions)
    print(f"[done] wrote outputs to {out_dir}")


if __name__ == "__main__":
    main()
