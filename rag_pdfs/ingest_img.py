"""Index PDF images for RAG with LangChain building blocks.

Pipeline:
    PDF parse → image filter → VLM caption → inline / separate chunks → Chroma

PDF 解析/过滤逻辑在 ``pdf_*`` 模块（实验性）；本文件为 CLI 入口与 caption/索引编排。

Outputs:
    image_captions.jsonl
    skipped_images.jsonl
    text_only_chunks.jsonl
    inline_caption_chunks.jsonl
    separate_caption_chunks.jsonl
    separate_mixed_chunks.jsonl

Usage:
    uv run -m rag_langchain.ingest_img tmp/raws/foo.pdf --out tmp/outs/foo --dry-run
    uv run -m rag_langchain.ingest_img tmp/raws/foo.pdf --out tmp/outs/foo --build-chroma
    uv run -m rag_langchain.ingest_img --batch --build-chroma
    uv run -m rag_langchain.ingest_img tmp/raws/foo.pdf --out tmp/outs/foo --skip-parse --resume
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import shutil
import time
from pathlib import Path
from typing import Any, Iterable

from langchain_core.documents import Document
from langchain_core.messages import HumanMessage, SystemMessage

from rag_langchain.caption_chunks import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    build_index_documents,
)
from rag_langchain.pdf_filter import (
    DEFAULT_MAX_ASPECT_RATIO,
    classify_image_skip,
)
from rag_langchain.pdf_layout import (
    ImageCaption,
    LayoutElement,
    SkippedImage,
    clean_text,
    is_image,
    is_text,
    nearby_caption_text,
    nearby_context,
    section_title_for_element,
)
from rag_langchain.pdf_parser import (
    find_existing_layout_json,
    load_elements,
    path_for_storage,
    require_java,
    resolve_image_path,
    run_opendataloader,
)

VISION_PRESETS: dict[str, dict[str, str]] = {
    "dashscope": {
        "api_key_setting": "dashscope_api_key",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-vl-max",
    },
    "deepseek": {
        "api_key_setting": "deepseek_api_key",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-v4-flash",
    },
}


def image_to_data_url(image_path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(image_path))
    if not mime:
        mime = "image/png"
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def caption_prompt(
    *,
    section_title: str,
    context_before: str,
    context_after: str,
) -> str:
    return f"""
Describe this image for a RAG index over technical documentation.

Use the section title and surrounding text to ground the caption in the specific
product, workflow, or step. Write a concise but information-dense caption.

If the image contains UI labels, table values, diagram labels, architecture
components, numbers, warnings, configuration paths, menu paths, commands, or
steps, transcribe them. For tables or matrices, preserve row/column/value
relationships. For diagrams, name nodes, edges, directions, and constraints.

Mention uncertainty when something is unclear. Do not invent details.

Section title:
{section_title or "(none)"}

Nearby text before the image:
{context_before or "(none)"}

Nearby text after the image:
{context_after or "(none)"}

Return only the caption text.
""".strip()


def get_setting(name: str, default: str = "") -> str:
    value = os.getenv(name.upper())
    if value:
        return value
    try:
        from configs.config import settings

        return str(getattr(settings, name.lower(), default) or default)
    except Exception:
        return default


def resolve_vision_config(
    provider: str,
    *,
    api_key: str | None,
    base_url: str | None,
    model: str | None,
) -> tuple[str, str, str]:
    preset = VISION_PRESETS.get(provider)
    if preset is None:
        raise ValueError(
            f"Unknown vision provider: {provider!r}. "
            f"Choose from: {', '.join(VISION_PRESETS)}"
        )

    resolved_key = api_key or get_setting(preset["api_key_setting"])
    resolved_base = base_url or preset["base_url"]
    resolved_model = model or preset["model"]
    return resolved_key, resolved_base, resolved_model


def build_vision_llm(
    *,
    api_key: str,
    base_url: str,
    model: str,
    timeout: int,
) -> Any:
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        api_key=api_key,
        base_url=base_url,
        model=model,
        temperature=0,
        max_tokens=1200,
        timeout=timeout,
    )


def caption_image_with_langchain(
    *,
    llm: Any,
    image_path: Path,
    section_title: str,
    context_before: str,
    context_after: str,
) -> str:
    response = llm.invoke(
        [
            SystemMessage(
                content="You create image captions for retrieval-augmented generation indexes."
            ),
            HumanMessage(
                content=[
                    {
                        "type": "text",
                        "text": caption_prompt(
                            section_title=section_title,
                            context_before=context_before,
                            context_after=context_after,
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": image_to_data_url(image_path)},
                    },
                ]
            ),
        ]
    )
    return clean_text(str(response.content))


def is_placeholder_caption(caption: ImageCaption) -> bool:
    return caption.caption.startswith("DRY RUN CAPTION")


def load_caption_cache(path: Path) -> dict[int, ImageCaption]:
    if not path.exists():
        return {}
    cache: dict[int, ImageCaption] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        cache[int(row["element_index"])] = ImageCaption.model_validate(row)
    return cache


def build_captions(
    *,
    elements: list[LayoutElement],
    json_path: Path,
    image_dir: Path,
    out_dir: Path,
    api_key: str | None,
    base_url: str,
    model: str,
    before_chars: int,
    after_chars: int,
    timeout: int,
    sleep_seconds: float,
    dry_run: bool,
    no_caption_context: bool,
    skip_small_images: bool,
    min_bbox_area: float,
    max_aspect_ratio: float,
    resume: bool,
    caption_cache_path: Path,
) -> tuple[list[ImageCaption], list[SkippedImage], dict[str, Any]]:
    captions: list[ImageCaption] = []
    skipped: list[SkippedImage] = []
    llm = None
    cache = load_caption_cache(caption_cache_path) if (resume or dry_run) else {}
    caption_seconds = 0.0
    api_calls = 0

    image_elements = [element for element in elements if is_image(element)]

    for element in image_elements:
        filter_before, filter_after = nearby_context(
            elements, element.index, before_chars=200, after_chars=200
        )
        image_path = resolve_image_path(str(element.source), json_path, image_dir)
        skip_reason = classify_image_skip(
            element,
            skip_small_images=skip_small_images,
            min_bbox_area=min_bbox_area,
            max_aspect_ratio=max_aspect_ratio,
            context_before=filter_before,
            context_after=filter_after,
            nearby_captions=nearby_caption_text(elements, element.index),
            image_path=image_path,
        )
        if skip_reason:
            skipped.append(
                SkippedImage(
                    element_index=element.index,
                    page=element.page,
                    image_source=str(element.source),
                    bbox=element.bbox,
                    reason=skip_reason,
                )
            )
            print(f"[skipped] page={element.page} reason={skip_reason}")
            continue

        if element.index in cache and not is_placeholder_caption(cache[element.index]):
            captions.append(cache[element.index])
            print(f"[cached] {cache[element.index].image_id} page={element.page}")
            continue

        before, after = nearby_context(elements, element.index, before_chars, after_chars)
        section_title = section_title_for_element(elements, element.index)
        caption_before = "" if no_caption_context else before
        caption_after = "" if no_caption_context else after
        section_for_prompt = "" if no_caption_context else section_title
        image_id = f"imgcap-{element.index:04d}"

        if dry_run:
            caption = (
                "DRY RUN CAPTION. Replace by running without --dry-run. "
                f"Section: {section_title}. Before: {before}. After: {after}."
            )
        else:
            if not llm:
                if not api_key:
                    raise RuntimeError(
                        "Missing vision API key. Pass --api-key or set the provider key "
                        "in configs/.env (e.g. dashscope_api_key)."
                    )
                llm = build_vision_llm(
                    api_key=api_key,
                    base_url=base_url,
                    model=model,
                    timeout=timeout,
                )
            if image_path is None or not image_path.exists():
                raise RuntimeError(
                    f"Could not resolve extracted image path for source: {element.source}"
                )
            started = time.perf_counter()
            caption = caption_image_with_langchain(
                llm=llm,
                image_path=image_path,
                section_title=section_for_prompt,
                context_before=caption_before,
                context_after=caption_after,
            )
            caption_seconds += time.perf_counter() - started
            api_calls += 1
            if sleep_seconds:
                time.sleep(sleep_seconds)

        stored_path = path_for_storage(image_path, out_dir)
        captions.append(
            ImageCaption(
                image_id=image_id,
                page=element.page,
                image_path=stored_path,
                image_source=str(element.source),
                section_title=section_title,
                context_before=before,
                context_after=after,
                caption=caption,
                bbox=element.bbox,
                element_index=element.index,
            )
        )
        print(f"[captioned] {image_id} page={element.page} {stored_path or image_path}")

    stats = {
        "caption_api_calls": api_calls,
        "caption_seconds": round(caption_seconds, 2),
        "dry_run": dry_run,
        "vision_model": model,
    }
    return captions, skipped, stats


def sanitize_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in metadata.items():
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            clean[key] = value
        else:
            clean[key] = json.dumps(value, ensure_ascii=False)
    return clean


def document_to_row(doc: Document) -> dict[str, Any]:
    return {
        "id": doc.metadata.get("chunk_id"),
        "chunk_type": doc.metadata.get("chunk_type"),
        "source_pdf": doc.metadata.get("source_pdf"),
        "page": doc.metadata.get("page"),
        "text": doc.page_content,
        "metadata": doc.metadata,
    }


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def captions_for_persist(
    captions: list[ImageCaption],
    *,
    dry_run: bool,
    caption_cache_path: Path,
) -> list[ImageCaption]:
    """dry-run 重建索引时，用磁盘上的真实 caption 覆盖占位，避免写坏 jsonl。"""
    if not dry_run:
        return captions
    if not any(is_placeholder_caption(caption) for caption in captions):
        return captions

    disk = load_caption_cache(caption_cache_path)
    merged: dict[int, ImageCaption] = {}
    for caption in captions:
        if is_placeholder_caption(caption) and caption.element_index in disk:
            cached = disk[caption.element_index]
            if not is_placeholder_caption(cached):
                merged[caption.element_index] = cached
                continue
        merged[caption.element_index] = caption
    return [merged[key] for key in sorted(merged)]


def write_outputs(
    out_dir: Path,
    pdf_path: Path,
    captions: list[ImageCaption],
    skipped: list[SkippedImage],
    index_docs: dict[str, list[Document]],
    *,
    chunk_size: int,
    chunk_overlap: int,
    caption_stats: dict[str, Any] | None = None,
    vision_provider: str | None = None,
    dry_run: bool = False,
    caption_cache_path: Path | None = None,
) -> dict[str, Path]:
    paths = {
        "image_captions": out_dir / "image_captions.jsonl",
        "skipped_images": out_dir / "skipped_images.jsonl",
        "text_only_chunks": out_dir / "text_only_chunks.jsonl",
        "inline_caption_chunks": out_dir / "inline_caption_chunks.jsonl",
        "separate_caption_chunks": out_dir / "separate_caption_chunks.jsonl",
        "separate_mixed_chunks": out_dir / "separate_mixed_chunks.jsonl",
    }

    cache_path = caption_cache_path or (out_dir / "image_captions.jsonl")
    persisted = captions_for_persist(
        captions,
        dry_run=dry_run,
        caption_cache_path=cache_path,
    )

    write_jsonl(
        paths["image_captions"],
        [caption.model_dump() for caption in persisted],
    )
    write_jsonl(paths["skipped_images"], [row.model_dump() for row in skipped])
    for name, docs in index_docs.items():
        write_jsonl(paths[name], [document_to_row(doc) for doc in docs])

    summary: dict[str, Any] = {
        "source_pdf": str(pdf_path),
        "image_count": len(persisted),
        "skipped_image_count": len(skipped),
        "chunk_size": chunk_size,
        "chunk_overlap": chunk_overlap,
        "outputs": {name: str(path) for name, path in paths.items()},
        "counts": {name: len(docs) for name, docs in index_docs.items()},
    }
    if vision_provider:
        summary["vision_provider"] = vision_provider
    if caption_stats:
        if (
            dry_run
            and caption_stats.get("caption_api_calls", 0) == 0
        ):
            prior = out_dir / "summary.json"
            if prior.exists():
                try:
                    old = json.loads(prior.read_text(encoding="utf-8"))
                    old_stats = old.get("caption_stats")
                    if old_stats and old_stats.get("caption_api_calls", 0) > 0:
                        summary["caption_stats"] = old_stats
                    else:
                        summary["caption_stats"] = caption_stats
                except (json.JSONDecodeError, OSError):
                    summary["caption_stats"] = caption_stats
            else:
                summary["caption_stats"] = caption_stats
        else:
            summary["caption_stats"] = caption_stats
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return paths


def build_embeddings(model_path: str | None) -> Any:
    from langchain_huggingface import HuggingFaceEmbeddings

    model_path = model_path or get_setting("qwen3_embedding_06b_path")
    if not model_path:
        raise RuntimeError(
            "Missing embedding model path. Pass --embedding-model-path or set "
            "QWEN3_EMBEDDING_06B_PATH / configs/.env qwen3_embedding_06b_path."
        )
    return HuggingFaceEmbeddings(
        model_name=model_path,
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )


def build_chroma_indexes(
    *,
    out_dir: Path,
    index_docs: dict[str, list[Document]],
    embedding_model_path: str | None,
) -> None:
    from langchain_chroma import Chroma

    embeddings = build_embeddings(embedding_model_path)
    chroma_root = out_dir / "chroma"
    chroma_root.mkdir(parents=True, exist_ok=True)

    collections = {
        "text_only_chunks": "text_only",
        "inline_caption_chunks": "inline_caption",
        "separate_caption_chunks": "separate_caption",
        "separate_mixed_chunks": "separate_mixed",
    }
    for doc_key, collection_name in collections.items():
        persist_dir = chroma_root / collection_name
        if persist_dir.exists():
            shutil.rmtree(persist_dir)

        docs = [
            Document(
                page_content=doc.page_content,
                metadata=sanitize_metadata(doc.metadata),
            )
            for doc in index_docs[doc_key]
        ]
        vectorstore = Chroma.from_documents(
            documents=docs,
            embedding=embeddings,
            persist_directory=str(persist_dir),
            collection_name=collection_name,
        )
        print(
            f"[chroma] {collection_name}: {vectorstore._collection.count()} docs -> {persist_dir}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", nargs="?", type=Path, help="Local PDF path")
    parser.add_argument("--out", type=Path, default=None, help="Output directory")
    parser.add_argument(
        "--batch",
        action="store_true",
        help="Ingest all PDFs under --raws-dir into --outs-dir/{pdf_stem}/",
    )
    parser.add_argument(
        "--raws-dir",
        type=Path,
        default=Path("tmp/raws"),
        help="Input PDF folder for --batch (default: tmp/raws)",
    )
    parser.add_argument(
        "--outs-dir",
        type=Path,
        default=Path("tmp/outs"),
        help="Output root for --batch (default: tmp/outs)",
    )
    parser.add_argument(
        "--vision-provider",
        choices=sorted(VISION_PRESETS),
        default=os.getenv("VISION_PROVIDER", "dashscope"),
        help="VLM backend for image captioning (default: dashscope/qwen-vl-max)",
    )
    parser.add_argument("--api-key", default=None, help="Override vision API key")
    parser.add_argument("--base-url", default=None, help="Override vision API base URL")
    parser.add_argument("--model", default=None, help="Override vision model name")
    parser.add_argument("--before-chars", type=int, default=1200)
    parser.add_argument("--after-chars", type=int, default=1200)
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    parser.add_argument("--chunk-overlap", type=int, default=DEFAULT_CHUNK_OVERLAP)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--sleep", type=float, default=0.0, help="Delay between image calls")
    parser.add_argument("--dry-run", action="store_true", help="Do not call the vision API")
    parser.add_argument(
        "--no-caption-context",
        action="store_true",
        help="Generate captions without section/before/after text (ablation D)",
    )
    parser.add_argument(
        "--skip-small-images",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Skip images whose parser bbox area is below --min-bbox-area",
    )
    parser.add_argument("--min-bbox-area", type=float, default=4_000)
    parser.add_argument("--max-aspect-ratio", type=float, default=DEFAULT_MAX_ASPECT_RATIO)
    parser.add_argument(
        "--skip-parse",
        action="store_true",
        help="Reuse existing OpenDataLoader JSON/images in --out",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse existing image_captions.jsonl and skip already captioned images",
    )
    parser.add_argument(
        "--build-chroma",
        action="store_true",
        help="Build Chroma indexes for all experiment collections",
    )
    parser.add_argument("--embedding-model-path", default=None)
    return parser.parse_args()


def ingest_one_pdf(pdf_path: Path, out_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    pdf_path = pdf_path.expanduser().resolve()
    out_dir = out_dir.expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    image_dir = out_dir / "images"

    print(f"\n{'=' * 60}\n[ingest] {pdf_path.name} -> {out_dir}\n{'=' * 60}")

    if args.skip_parse:
        json_path = find_existing_layout_json(out_dir)
    else:
        require_java()
        json_path = run_opendataloader(pdf_path, out_dir, image_dir)

    print(f"[json] {json_path}")
    elements = load_elements(json_path)
    print(f"[layout elements] {len(elements)}")
    print(f"[text elements] {sum(1 for element in elements if is_text(element))}")
    print(f"[image elements] {sum(1 for element in elements if is_image(element))}")

    vision_api_key, vision_base_url, vision_model = resolve_vision_config(
        args.vision_provider,
        api_key=args.api_key,
        base_url=args.base_url,
        model=args.model,
    )
    print(
        f"[vision] provider={args.vision_provider} model={vision_model} "
        f"base_url={vision_base_url}"
    )

    captions, skipped, caption_stats = build_captions(
        elements=elements,
        json_path=json_path,
        image_dir=image_dir,
        out_dir=out_dir,
        api_key=vision_api_key,
        base_url=vision_base_url,
        model=vision_model,
        before_chars=args.before_chars,
        after_chars=args.after_chars,
        timeout=args.timeout,
        sleep_seconds=args.sleep,
        dry_run=args.dry_run,
        no_caption_context=args.no_caption_context,
        skip_small_images=args.skip_small_images,
        min_bbox_area=args.min_bbox_area,
        max_aspect_ratio=args.max_aspect_ratio,
        resume=args.resume,
        caption_cache_path=out_dir / "image_captions.jsonl",
    )
    index_docs = build_index_documents(
        elements=elements,
        captions=captions,
        pdf_path=pdf_path,
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
    )
    write_outputs(
        out_dir,
        pdf_path,
        captions,
        skipped,
        index_docs,
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
        caption_stats=caption_stats,
        vision_provider=args.vision_provider,
        dry_run=args.dry_run,
        caption_cache_path=out_dir / "image_captions.jsonl",
    )

    if args.build_chroma:
        build_chroma_indexes(
            out_dir=out_dir,
            index_docs=index_docs,
            embedding_model_path=args.embedding_model_path,
        )

    print(f"[done] wrote outputs to {out_dir}")
    print(
        "[chunks] "
        f"text={len(index_docs['text_only_chunks'])} "
        f"inline={len(index_docs['inline_caption_chunks'])} "
        f"separate_caption={len(index_docs['separate_caption_chunks'])} "
        f"separate_mixed={len(index_docs['separate_mixed_chunks'])}"
    )
    return {
        "pdf": str(pdf_path),
        "out_dir": str(out_dir),
        "counts": {name: len(docs) for name, docs in index_docs.items()},
        "caption_stats": caption_stats,
        "skipped": len(skipped),
        "captioned": len(captions),
    }


def main() -> None:
    args = parse_args()

    if args.batch:
        raws_dir = args.raws_dir.expanduser().resolve()
        outs_root = args.outs_dir.expanduser().resolve()
        pdfs = sorted(raws_dir.glob("*.pdf"))
        if not pdfs:
            raise FileNotFoundError(f"No PDF files found in {raws_dir}")
        results = []
        for pdf_path in pdfs:
            out_dir = outs_root / pdf_path.stem
            results.append(ingest_one_pdf(pdf_path, out_dir, args))
        print(f"\n[batch done] ingested {len(results)} PDFs into {outs_root}")
        return

    if args.pdf is None:
        raise SystemExit("Provide a PDF path, or use --batch with --raws-dir.")

    pdf_path = args.pdf.expanduser().resolve()
    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)

    out_dir = args.out or Path("rag_image_index")
    if args.out is None and pdf_path.parent.name == "raws":
        out_dir = Path("tmp/outs") / pdf_path.stem

    ingest_one_pdf(pdf_path, out_dir, args)


if __name__ == "__main__":
    main()