"""PDF layout 元素模型与阅读顺序工具（实验性 PDF ingest 专用）。"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel

TEXT_TYPES = {
    "paragraph",
    "heading",
    "caption",
    "list item",
    "text",
    "text block",
    "table cell",
}

NOISE_KEYWORDS = frozenset(
    {"logo", "icon", "avatar", "decorative", "banner", "watermark", "brand"}
)

# 常见 US Letter PDF 页高 ~792pt；用于页眉/页脚小图启发式（L1）
DEFAULT_PAGE_HEIGHT_PT = 792.0
PAGE_EDGE_MARGIN_PT = 72.0


class LayoutElement(BaseModel):
    index: int
    type: str
    page: int | None
    content: str
    source: str | None
    bbox: Any
    raw_id: int | str | None


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


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def truncate(text: str, max_chars: int) -> str:
    text = clean_text(text)
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "..."


def is_image(element: LayoutElement) -> bool:
    return element.type == "image" and bool(element.source)


def is_heading(element: LayoutElement) -> bool:
    return element.type == "heading" or element.type.startswith("heading")


def is_caption_element(element: LayoutElement) -> bool:
    return element.type == "caption" or element.type.startswith("caption")


def is_text(element: LayoutElement) -> bool:
    return bool(element.content) and (
        element.type in TEXT_TYPES or element.type.startswith("heading")
    )


def bbox_coords(bbox: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(bbox, (list, tuple)) or len(bbox) < 4:
        return None
    try:
        x1, y1, x2, y2 = [float(v) for v in bbox[:4]]
    except (TypeError, ValueError):
        return None
    return x1, y1, x2, y2


def bbox_area(bbox: Any) -> float | None:
    coords = bbox_coords(bbox)
    if coords is None:
        return None
    x1, y1, x2, y2 = coords
    return abs(x2 - x1) * abs(y2 - y1)


def bbox_aspect_ratio(bbox: Any) -> float | None:
    coords = bbox_coords(bbox)
    if coords is None:
        return None
    x1, y1, x2, y2 = coords
    width = abs(x2 - x1)
    height = abs(y2 - y1)
    if width <= 0 or height <= 0:
        return None
    return max(width, height) / min(width, height)


def is_page_edge_bbox(
    bbox: Any,
    *,
    page_height: float = DEFAULT_PAGE_HEIGHT_PT,
    margin: float = PAGE_EDGE_MARGIN_PT,
) -> bool:
    """bbox 是否落在页眉/页脚带内（用于小图 logo 启发式）。"""
    coords = bbox_coords(bbox)
    if coords is None:
        return False
    _, y1, _, y2 = coords
    return y1 >= page_height - margin or y2 <= margin


def contains_noise_keyword(text: str) -> bool:
    lowered = clean_text(text).lower()
    if not lowered:
        return False
    return any(keyword in lowered for keyword in NOISE_KEYWORDS)


def reading_order_key(element: LayoutElement) -> tuple[Any, ...]:
    page = element.page if element.page is not None else 0
    coords = bbox_coords(element.bbox)
    if coords is not None:
        x1, y1, _, _ = coords
        return (page, y1, x1, element.index)
    return (page, element.index, 0)


def sort_elements_reading_order(elements: list[LayoutElement]) -> list[LayoutElement]:
    """保持 opendataloader flatten 顺序，仅刷新 index。

    说明：技术 PDF 中大量 text 元素没有 bbox，盲目按 bbox 重排会把图片排到段落后。
    若后续验证需要 bbox 微调，请使用 ``reorder_elements_by_bbox``。
    """
    return [
        element.model_copy(update={"index": new_index})
        for new_index, element in enumerate(elements)
    ]


def reorder_elements_by_bbox(elements: list[LayoutElement]) -> list[LayoutElement]:
    """按 page → bbox.y → bbox.x 重排（仅当解析结果普遍带 bbox 时使用）。"""
    ordered = sorted(elements, key=reading_order_key)
    return [
        element.model_copy(update={"index": new_index})
        for new_index, element in enumerate(ordered)
    ]


# 无 heading 页兜底：同页首个文本超过该长度就不当伪标题（避免把正文段落当锚点）
FALLBACK_TITLE_MAX_CHARS = 80


def section_title_for_element(
    elements: list[LayoutElement],
    element_index: int,
    *,
    fallback_max_chars: int = FALLBACK_TITLE_MAX_CHARS,
) -> str:
    """向上查找最近的 heading 作为章节锚点。

    1. 优先同页 heading
    2. 同页没有则向前跨页找最近 heading
    3. 全文无 heading 时，取同页第一个「短文本」当伪标题（纯流程图 PDF 常无 heading）
    """
    target_page = elements[element_index].page
    cross_page_title = ""

    for element in reversed(elements[:element_index]):
        if not is_heading(element):
            continue
        if target_page is not None and element.page == target_page:
            return element.content
        if not cross_page_title:
            cross_page_title = element.content

    if cross_page_title:
        return cross_page_title

    if target_page is None:
        return ""
    for element in elements:
        if element.page != target_page or not is_text(element):
            continue
        text = clean_text(element.content)
        if text and len(text) <= fallback_max_chars:
            return text
        return ""
    return ""


def nearby_context(
    elements: list[LayoutElement],
    element_index: int,
    before_chars: int,
    after_chars: int,
) -> tuple[str, str]:
    before_parts: list[str] = []
    after_parts: list[str] = []
    image_page = elements[element_index].page

    for element in reversed(elements[:element_index]):
        if image_page is not None and element.page != image_page:
            continue
        if is_text(element):
            before_parts.append(element.content)
            if len(clean_text(" ".join(reversed(before_parts)))) >= before_chars:
                break

    for element in elements[element_index + 1 :]:
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


def nearby_caption_text(
    elements: list[LayoutElement],
    element_index: int,
    *,
    window: int = 3,
) -> str:
    """收集图片前后若干元素中的 caption/heading 文本（L2 过滤用）。"""
    start = max(0, element_index - window)
    end = min(len(elements), element_index + window + 1)
    parts: list[str] = []
    for element in elements[start:end]:
        if element.index == element_index:
            continue
        if is_caption_element(element) or is_heading(element):
            parts.append(element.content)
    return clean_text(" ".join(parts))
