"""Shared models and artifact writers for ``rewebpage_*`` backends.

The webpage tools are Layer 0 collectors.  They deliberately share a small,
human-inspectable bundle while leaving provider-specific raw fields in
``page.json``.  A single-URL run writes directly to ``--out-dir``; a multi-URL
run adds one URL slug per page to avoid collisions.
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse, urlunparse

from pydantic import BaseModel, Field, HttpUrl


class WebpageSnapshot(BaseModel):
    """Provider-neutral metadata stored in every ``page.json``."""

    provider: str
    url: HttpUrl = Field(description="Final URL after redirects")
    source_url: str = Field(description="Original URL supplied by the user")
    title: str | None = None
    description: str | None = None
    status_code: int | None = None
    word_count: int = 0
    headings: list[str] = Field(default_factory=list)
    markdown: str = ""
    links_internal: list[str] = Field(default_factory=list)
    links_external: list[str] = Field(default_factory=list)
    images: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    fetched_at: str = Field(description="UTC ISO-8601 timestamp")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_urls(urls: Iterable[str]) -> list[str]:
    """Return normalized HTTP(S) URLs or raise one actionable error."""

    normalized: list[str] = []
    invalid: list[str] = []
    for value in urls:
        candidate = value.strip()
        parsed = urlparse(candidate)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            invalid.append(value)
        else:
            normalized.append(candidate)
    if invalid:
        raise ValueError(
            "Only absolute http(s) URLs are supported: " + ", ".join(invalid)
        )
    if not normalized:
        raise ValueError("At least one URL is required.")
    return normalized


def url_slug(url: str, *, max_length: int = 100) -> str:
    parsed = urlparse(url)
    raw = (parsed.netloc + parsed.path).strip("/")
    if parsed.query:
        raw += "_" + parsed.query
    slug = "".join(char if char.isalnum() or char in "-_." else "_" for char in raw)
    return (slug.strip("._-") or "page")[:max_length]


def output_dir_for_url(root: Path, url: str, *, url_count: int) -> Path:
    """Use a flat output for one URL and slug subdirectories for batches."""

    return root if url_count == 1 else root / url_slug(url)


def normalize_markdown(markdown: str | None) -> str:
    text = (markdown or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    return text + "\n" if text else ""


def clean_heading(text: str) -> str:
    return re.sub(r"\s*\[[^\]]*\]\([^)]*\)", "", text).strip()


def headings_from_markdown(markdown: str, *, max_level: int = 3) -> list[str]:
    pattern = rf"^(#{{1,{max_level}}})\s+(.+)$"
    return dedupe(
        clean_heading(match.group(2)) for match in re.finditer(pattern, markdown, re.M)
    )


def rough_word_count(text: str) -> int:
    """Count Latin-like tokens and individual CJK characters for a useful estimate."""

    return len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]|[\w]+", text, re.UNICODE))


def dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        item = str(value).strip()
        if item and item not in seen:
            seen.add(item)
            output.append(item)
    return output


def _normalize_link(value: str, base_url: str) -> str | None:
    absolute = urljoin(base_url, value.strip())
    parsed = urlparse(absolute)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return urlunparse(parsed._replace(fragment=""))


def classify_links(values: Iterable[str], base_url: str) -> tuple[list[str], list[str]]:
    base_host = (urlparse(base_url).hostname or "").lower()
    internal: list[str] = []
    external: list[str] = []
    for value in values:
        link = _normalize_link(str(value), base_url)
        if not link:
            continue
        host = (urlparse(link).hostname or "").lower()
        target = internal if host == base_host else external
        target.append(link)
    return dedupe(internal), dedupe(external)


def normalize_images(values: Iterable[str], base_url: str) -> list[str]:
    return dedupe(
        link
        for value in values
        if (link := _normalize_link(str(value), base_url)) is not None
    )


def make_snapshot(
    *,
    provider: str,
    source_url: str,
    final_url: str | None,
    markdown: str | None,
    title: str | None = None,
    description: str | None = None,
    status_code: int | None = None,
    links: Iterable[str] = (),
    internal_links: Iterable[str] | None = None,
    external_links: Iterable[str] | None = None,
    images: Iterable[str] = (),
    metadata: dict[str, Any] | None = None,
) -> WebpageSnapshot:
    final = final_url or source_url
    content = normalize_markdown(markdown)
    if internal_links is None or external_links is None:
        classified_internal, classified_external = classify_links(links, final)
        internal_links = (
            classified_internal if internal_links is None else internal_links
        )
        external_links = (
            classified_external if external_links is None else external_links
        )

    headings = headings_from_markdown(content)
    return WebpageSnapshot(
        provider=provider,
        url=final,
        source_url=source_url,
        title=(title or (headings[0] if headings else None)),
        description=description,
        status_code=status_code,
        word_count=rough_word_count(content),
        headings=headings,
        markdown=content,
        links_internal=dedupe(internal_links),
        links_external=dedupe(external_links),
        images=normalize_images(images, final),
        metadata=metadata or {},
        fetched_at=utc_now(),
    )


def as_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return {"type": "bytes", "length": len(value)}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): as_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [as_jsonable(item) for item in value]
    if hasattr(value, "model_dump"):
        return as_jsonable(value.model_dump(exclude_none=True))
    if hasattr(value, "dict"):
        return as_jsonable(value.dict(exclude_none=True))
    return str(value)


def decode_base64_bytes(value: str | bytes | None) -> bytes | None:
    if not value:
        return None
    if isinstance(value, bytes):
        return value
    payload = value.partition(",")[2] if value.startswith("data:") else value
    if not payload:
        return None
    try:
        return base64.b64decode(payload, validate=False)
    except (binascii.Error, ValueError, TypeError):
        return None


def image_to_png(image_bytes: bytes) -> bytes:
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "Missing package: pillow. Install the webpage-firecrawl extra."
        ) from exc

    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def image_to_a4_pdf(image_bytes: bytes) -> tuple[bytes, int]:
    """Split a long screenshot into A4-ratio pages and return PDF bytes."""

    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "Missing package: pillow. Install the webpage-firecrawl extra."
        ) from exc

    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    width, height = image.size
    page_height = max(1, int(width * 1.414))
    pages = []
    for y in range(0, height, page_height):
        crop = image.crop((0, y, width, min(y + page_height, height)))
        if crop.height < page_height:
            background = Image.new("RGB", (width, page_height), "white")
            background.paste(crop, (0, 0))
            crop = background
        pages.append(crop)

    output = io.BytesIO()
    if pages:
        pages[0].save(
            output,
            "PDF",
            save_all=True,
            append_images=pages[1:],
            resolution=96.0,
        )
    return output.getvalue(), len(pages)


def write_page_bundle(
    out_dir: Path,
    snapshot: WebpageSnapshot,
    *,
    raw: Any = None,
    html: str | None = None,
    png_bytes: bytes | None = None,
    pdf_bytes: bytes | None = None,
    mhtml: str | None = None,
) -> list[Path]:
    """Write one normalized page bundle and return the written paths."""

    out_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {"snapshot": snapshot.model_dump(mode="json")}
    if raw is not None:
        payload["raw"] = as_jsonable(raw)

    written: list[Path] = []
    json_path = out_dir / "page.json"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    written.append(json_path)

    markdown_path = out_dir / "page.md"
    markdown_path.write_text(snapshot.markdown, encoding="utf-8")
    written.append(markdown_path)

    optional_files = (
        ("page.html", html, "text"),
        ("page.png", png_bytes, "bytes"),
        ("page.pdf", pdf_bytes, "bytes"),
        ("page.mhtml", mhtml, "text"),
    )
    for filename, content, mode in optional_files:
        if content is None:
            continue
        path = out_dir / filename
        if mode == "bytes":
            path.write_bytes(content)  # type: ignore[arg-type]
        else:
            path.write_text(str(content), encoding="utf-8")
        written.append(path)
    return written


def describe_planned_outputs(
    root: Path,
    urls: list[str],
    *,
    optional_names: Iterable[str] = (),
) -> list[str]:
    names = ["page.json", "page.md", *optional_names]
    planned: list[str] = []
    for url in urls:
        out_dir = output_dir_for_url(root, url, url_count=len(urls))
        planned.extend(str(out_dir / name) for name in names)
    return planned
