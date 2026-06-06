"""PDF 图片 L1/L2 规则过滤（VLM caption 之前，实验性）。

原则：宁可漏滤（多 caption 装饰图），不可误滤（丢掉 load-bearing 图）。
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from rag_langchain.pdf_layout import (
    LayoutElement,
    SkippedImage,
    bbox_area,
    bbox_aspect_ratio,
    clean_text,
    contains_noise_keyword,
    is_image,
    is_page_edge_bbox,
    nearby_caption_text,
    nearby_context,
)

DEFAULT_MAX_ASPECT_RATIO = 8.0
DEFAULT_MIN_BBOX_AREA = 4_000
DEFAULT_SPARSE_CONTEXT_THRESHOLD = 40
# 仅对「明显小图」应用长宽比/页边启发式，避免误伤宽幅架构图
SMALL_IMAGE_AREA_MULTIPLIER = 3.0
TINY_IMAGE_AREA_MULTIPLIER = 1.5


def classify_image_skip(
    element: LayoutElement,
    *,
    skip_small_images: bool,
    min_bbox_area: float,
    max_aspect_ratio: float,
    context_before: str = "",
    context_after: str = "",
    nearby_captions: str = "",
    image_path: Path | None = None,
    sparse_context_threshold: int = DEFAULT_SPARSE_CONTEXT_THRESHOLD,
) -> str | None:
    """L1/L2 规则过滤。返回 skip reason；None 表示保留。"""
    if not is_image(element):
        return "not_image"

    area = bbox_area(element.bbox)

    if image_path is not None and not image_path.exists():
        return "image_file_missing"

    if image_path is not None:
        mime, _ = mimetypes.guess_type(str(image_path))
        if mime and not mime.startswith("image/"):
            return f"unsupported_mime:{mime}"

    # L1：极小 bbox — 高置信装饰/logo
    if skip_small_images and area is not None and area < min_bbox_area:
        return f"bbox_area<{min_bbox_area}"

    small_area_cap = min_bbox_area * SMALL_IMAGE_AREA_MULTIPLIER
    tiny_area_cap = min_bbox_area * TINY_IMAGE_AREA_MULTIPLIER

    # L1：小图 + 极端长宽比 → 横幅/分隔线
    ratio = bbox_aspect_ratio(element.bbox)
    if (
        ratio is not None
        and ratio > max_aspect_ratio
        and area is not None
        and area < small_area_cap
    ):
        return f"aspect_ratio>{max_aspect_ratio}_small_area"

    # L1：页眉/页脚小图（常见 logo 位）
    if (
        area is not None
        and area < tiny_area_cap
        and is_page_edge_bbox(element.bbox)
    ):
        return "page_edge_small_image"

    # L1/L2：噪声关键词 — 仅在与小图组合时跳过（避免误伤含 icon 的正文图）
    noise_text = " ".join(
        part for part in (element.content, nearby_captions) if part
    )
    if noise_text and contains_noise_keyword(noise_text):
        if area is None or area < small_area_cap:
            return "noise_keyword_small_image"

    # L2：小图 + 上下文极稀疏 → 倾向装饰
    if area is not None and area < small_area_cap:
        context_len = len(clean_text(context_before)) + len(clean_text(context_after))
        if context_len < sparse_context_threshold:
            return f"sparse_context<{sparse_context_threshold}_small_area"

    return None


def filter_image_elements(
    elements: list[LayoutElement],
    *,
    json_path: Path,
    image_dir: Path,
    skip_small_images: bool,
    min_bbox_area: float,
    max_aspect_ratio: float,
    context_chars: int = 200,
    sparse_context_threshold: int = DEFAULT_SPARSE_CONTEXT_THRESHOLD,
) -> tuple[list[LayoutElement], list[SkippedImage]]:
    """对 layout 中所有 image 元素做过滤，返回 (保留, 跳过)。"""
    from rag_langchain.pdf_parser import resolve_image_path

    kept: list[LayoutElement] = []
    skipped: list[SkippedImage] = []

    for element in elements:
        if not is_image(element):
            continue

        before, after = nearby_context(
            elements, element.index, before_chars=context_chars, after_chars=context_chars
        )
        image_path = resolve_image_path(str(element.source), json_path, image_dir)
        skip_reason = classify_image_skip(
            element,
            skip_small_images=skip_small_images,
            min_bbox_area=min_bbox_area,
            max_aspect_ratio=max_aspect_ratio,
            context_before=before,
            context_after=after,
            nearby_captions=nearby_caption_text(elements, element.index),
            image_path=image_path,
            sparse_context_threshold=sparse_context_threshold,
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
        else:
            kept.append(element)

    return kept, skipped
