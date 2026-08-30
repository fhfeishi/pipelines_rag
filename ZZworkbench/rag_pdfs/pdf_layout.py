"""兼容 shim：layout 核心已下沉到 `parsers.document.layout`（2026-07-07）。

本模块仅保留 RAG 专属数据模型（ImageCaption / SkippedImage），
其余符号 re-export 以保持旧 import 路径可用；新代码请直接
import `parsers.document.layout`。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from parsers.document.layout import (  # noqa: F401
    DEFAULT_PAGE_HEIGHT_PT,
    FALLBACK_TITLE_MAX_CHARS,
    NOISE_KEYWORDS,
    PAGE_EDGE_MARGIN_PT,
    TEXT_TYPES,
    LayoutElement,
    bbox_area,
    bbox_aspect_ratio,
    bbox_coords,
    clean_text,
    contains_noise_keyword,
    is_caption_element,
    is_heading,
    is_image,
    is_page_edge_bbox,
    is_text,
    nearby_caption_text,
    nearby_context,
    reading_order_key,
    reorder_elements_by_bbox,
    section_title_for_element,
    sort_elements_reading_order,
    truncate,
)


class ImageCaption(BaseModel):
    image_id: str
    page: int | None
    image_path: str
    image_source: str | None
    section_title: str = ""
    context_before: str
    context_after: str
    caption: str
    bbox: Any
    element_index: int
    quality_flag: str = ""


class SkippedImage(BaseModel):
    element_index: int
    page: int | None
    image_source: str | None
    bbox: Any
    reason: str
