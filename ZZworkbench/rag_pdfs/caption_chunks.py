"""Inline / Separate caption chunk 构建（LangChain Document + TextSplitter）。

两种策略对应 notes.md / plan.md 实验组 B 与 C：

Inline（方案 A/B）
-----------------
把 caption 当作「插在原文阅读顺序里的 alt text」，再与普通正文一起切 chunk。

    正文段 1
    [Image id=imgcap-0001 page=3 path=...] Settings 页面截图，Cloud Sync 开关位于...
    正文段 2

    → page_documents（按页合并）
    → RecursiveCharacterTextSplitter
    → inline_caption_chunks

特点：caption 与邻近正文绑定；chunk 被召回时 caption 被动进入上下文。

Separate（方案 B/C）
-------------------
每张图单独一个 ``image_caption`` Document，**不再 split**；与 text chunk 并列写入索引。

    text_only_chunks          separate_caption_chunks
    ┌─────────────┐          ┌──────────────────────┐
    │ 纯文本 chunk │          │ 每图 1 个 caption chunk │
    └─────────────┘          └──────────────────────┘
              └──── separate_mixed_chunks ────┘

特点：caption 只有被检索命中时才进入上下文；适合 load-bearing 图片。
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from rag_pdfs.pdf_layout import (
    ImageCaption,
    LayoutElement,
    clean_text,
    is_image,
    is_text,
    truncate,
)

DEFAULT_CHUNK_SIZE = 700
DEFAULT_CHUNK_OVERLAP = 120

CHUNK_TYPE_TEXT = "text"
CHUNK_TYPE_INLINE = "text_with_inline_image_captions"
CHUNK_TYPE_IMAGE_CAPTION = "image_caption"


def format_inline_caption_marker(caption: ImageCaption) -> str:
    """生成 inline 插入标记，便于 answer 引用 image_id / page / path。"""
    page = caption.page if caption.page is not None else "unknown"
    path = caption.image_path or "unknown"
    section = (
        f' section="{caption.section_title}"' if caption.section_title else ""
    )
    return (
        f"[Image id={caption.image_id} page={page} path={path}{section}] "
        f"{caption.caption}"
    )


def build_separate_caption_content(
    caption: ImageCaption,
    *,
    context_chars: int = 200,
) -> str:
    """构建 separate chunk 的 page_content（面向检索优化，metadata 仍保留完整字段）。"""
    page = caption.page if caption.page is not None else "unknown"
    header = f"[Image caption | page {page}"
    if caption.section_title:
        header += f' | section "{caption.section_title}"'
    header += "]"

    lines = [header, caption.caption]
    if caption.context_before or caption.context_after:
        before = truncate(caption.context_before, context_chars)
        after = truncate(caption.context_after, context_chars)
        snippets: list[str] = []
        if before:
            snippets.append(f"before: {before}")
        if after:
            snippets.append(f"after: {after}")
        lines.append("Nearby text: " + " | ".join(snippets))
    return "\n".join(lines)


def make_splitter(
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", "。", ". ", " ", ""],
    )


def page_documents_from_units(
    *,
    units: Iterable[tuple[int | None, str]],
    pdf_path: Path,
    chunk_type: str,
    extra_metadata: dict[str, Any] | None = None,
) -> list[Document]:
    """把 (page, text) 序列按页合并为 LangChain Document（切分前的 source doc）。"""
    pages: dict[int | str, list[str]] = defaultdict(list)
    for page, text in units:
        text = clean_text(text)
        if text:
            pages[page if page is not None else "unknown"].append(text)

    docs: list[Document] = []
    for page, parts in sorted(pages.items(), key=lambda item: str(item[0])):
        metadata: dict[str, Any] = {
            "source_pdf": str(pdf_path),
            "page": page,
            "chunk_type": chunk_type,
        }
        if extra_metadata:
            metadata.update(extra_metadata)
        docs.append(Document(page_content="\n\n".join(parts), metadata=metadata))
    return docs


def build_reading_order_units(
    elements: list[LayoutElement],
    captions_by_element: dict[int, ImageCaption] | None = None,
    *,
    include_images: bool = False,
) -> list[tuple[int | None, str]]:
    """按阅读顺序遍历元素，产出 (page, text) 序列。

    ``include_images=False`` → 仅文本（text-only baseline）
    ``include_images=True``  → 在图片原位插入 inline caption 标记
    """
    units: list[tuple[int | None, str]] = []
    captions_by_element = captions_by_element or {}

    for element in elements:
        if is_text(element):
            units.append((element.page, element.content))
        elif include_images and is_image(element) and element.index in captions_by_element:
            caption = captions_by_element[element.index]
            units.append((caption.page, format_inline_caption_marker(caption)))
    return units


def build_text_only_source_documents(
    elements: list[LayoutElement],
    pdf_path: Path,
) -> list[Document]:
    return page_documents_from_units(
        units=build_reading_order_units(elements, include_images=False),
        pdf_path=pdf_path,
        chunk_type=CHUNK_TYPE_TEXT,
    )


def build_inline_source_documents(
    elements: list[LayoutElement],
    captions: list[ImageCaption],
    pdf_path: Path,
) -> list[Document]:
    captions_by_element = {caption.element_index: caption for caption in captions}
    return page_documents_from_units(
        units=build_reading_order_units(
            elements,
            captions_by_element,
            include_images=True,
        ),
        pdf_path=pdf_path,
        chunk_type=CHUNK_TYPE_INLINE,
    )


def build_separate_caption_documents(
    captions: list[ImageCaption],
    pdf_path: Path,
    *,
    context_chars: int = 200,
) -> list[Document]:
    """每张图一个 Document；page_content 增强检索，metadata 保留完整溯源信息。"""
    docs: list[Document] = []
    for caption in captions:
        docs.append(
            Document(
                page_content=build_separate_caption_content(
                    caption,
                    context_chars=context_chars,
                ),
                metadata={
                    "source_pdf": str(pdf_path),
                    "page": caption.page,
                    "chunk_type": CHUNK_TYPE_IMAGE_CAPTION,
                    "chunk_id": caption.image_id,
                    "image_id": caption.image_id,
                    "image_path": caption.image_path,
                    "image_source": caption.image_source,
                    "section_title": caption.section_title,
                    "bbox": caption.bbox,
                    "context_before": caption.context_before,
                    "context_after": caption.context_after,
                    "element_index": caption.element_index,
                },
            )
        )
    return docs


def split_and_tag_documents(
    docs: list[Document],
    *,
    splitter: RecursiveCharacterTextSplitter,
    id_prefix: str,
    chunk_type: str,
) -> list[Document]:
    split_docs = splitter.split_documents(docs)
    for index, doc in enumerate(split_docs, 1):
        doc.metadata["chunk_id"] = f"{id_prefix}-{index:04d}"
        doc.metadata["chunk_type"] = chunk_type
    return split_docs


def build_index_documents(
    *,
    elements: list[LayoutElement],
    captions: list[ImageCaption],
    pdf_path: Path,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    separate_context_chars: int = 200,
) -> dict[str, list[Document]]:
    """构建实验所需的全部 chunk 产物。

    Returns:
        text_only_chunks:        实验组 A
        inline_caption_chunks:   实验组 B（caption 插入正文后切分）
        separate_caption_chunks: 实验组 C 的图片证据（每图 1 chunk，不 split）
        separate_mixed_chunks:   实验组 C 的混合索引（text + image_caption）
    """
    splitter = make_splitter(chunk_size, chunk_overlap)

    text_chunks = split_and_tag_documents(
        build_text_only_source_documents(elements, pdf_path),
        splitter=splitter,
        id_prefix="text",
        chunk_type=CHUNK_TYPE_TEXT,
    )
    inline_chunks = split_and_tag_documents(
        build_inline_source_documents(elements, captions, pdf_path),
        splitter=splitter,
        id_prefix="inline",
        chunk_type=CHUNK_TYPE_INLINE,
    )
    separate_caption_chunks = build_separate_caption_documents(
        captions,
        pdf_path,
        context_chars=separate_context_chars,
    )

    return {
        "text_only_chunks": text_chunks,
        "inline_caption_chunks": inline_chunks,
        "separate_caption_chunks": separate_caption_chunks,
        "separate_mixed_chunks": [*text_chunks, *separate_caption_chunks],
    }
