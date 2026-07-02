"""Firecrawl 网页抓取脚本：网页 -> JSON / Markdown / 可选 PDF。

默认抓取 Qwen AgentWorld 博客，并把结果写到：

    outputs/firecrawl/<page-slug>/
      page.json
      page.md
      page.png   # Firecrawl full-page screenshot 可用时
      page.pdf   # 由 page.png 转成 A4 分页 PDF

Firecrawl 需要 API key。脚本按顺序读取：

1. 命令行 `--api-key`
2. 环境变量 `FIRECRAWL_API_KEY`
3. `configs/.env` 里的 `FIRECRAWL_API_KEY` / `firecrawl_api_key`

用法：
    python -m parsers.script_firecrawl --probe
    python -m parsers.script_firecrawl
    python -m parsers.script_firecrawl https://qwen.ai/blog?id=qwen-agentworld
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field, HttpUrl


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_URLS = ["https://qwen.ai/blog?id=qwen-agentworld"]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "outputs" / "firecrawl"
DEFAULT_API_URL = "https://api.firecrawl.dev"
DEFAULT_HTTP_PROXY = (
    os.environ.get("https_proxy")
    or os.environ.get("HTTPS_PROXY")
    or os.environ.get("http_proxy")
    or os.environ.get("HTTP_PROXY")
    or "http://127.0.0.1:7897"
)


class FirecrawlSnapshot(BaseModel):
    """单个网页的 Firecrawl 结构化快照。"""

    url: HttpUrl = Field(description="最终页面 URL，优先取 Firecrawl metadata.url")
    source_url: str = Field(description="用户传入的原始 URL")
    provider: str = "firecrawl"
    title: str | None = None
    description: str | None = None
    status_code: int | None = None
    word_count: int = 0
    headings: list[str] = Field(default_factory=list)
    markdown: str = ""
    links: list[str] = Field(default_factory=list)
    images: list[str] = Field(default_factory=list)
    screenshot: str | None = Field(default=None, description="Firecrawl 返回的截图 URL 或 data URI")
    metadata: dict[str, Any] = Field(default_factory=dict)
    fetched_at: str = Field(description="抓取时间（UTC ISO8601）")


@dataclass(frozen=True)
class FirecrawlOptions:
    out_dir: Path
    api_key: str | None
    api_url: str
    http_proxy: str | None
    timeout_ms: int
    only_main_content: bool
    full_page_screenshot: bool
    write_pdf: bool
    save_png: bool
    formats: list[Any]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="用 Firecrawl 抓取网页，输出 page.json / page.md / 可选 page.pdf。",
    )
    parser.add_argument(
        "urls",
        nargs="*",
        help="待抓取 URL；省略时抓取 Qwen AgentWorld 博客。",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"输出根目录，默认 {DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument("--api-key", help="Firecrawl API key；默认从环境或 configs/.env 读取。")
    parser.add_argument(
        "--api-url",
        default=os.getenv("FIRECRAWL_API_URL") or DEFAULT_API_URL,
        help=f"Firecrawl API URL，默认 {DEFAULT_API_URL}",
    )
    parser.add_argument(
        "--http-proxy",
        default=DEFAULT_HTTP_PROXY,
        help=(
            "访问 Firecrawl API 时使用的本地 HTTP 代理；默认取环境变量，"
            "兜底 http://127.0.0.1:7897。"
        ),
    )
    parser.add_argument(
        "--no-http-proxy",
        action="store_true",
        help="不为 Firecrawl SDK / probe / 截图下载设置 HTTP(S)_PROXY。",
    )
    parser.add_argument("--timeout-ms", type=int, default=60_000, help="Firecrawl 请求超时毫秒。")
    parser.add_argument(
        "--all-content",
        action="store_true",
        help="关闭 only_main_content，尽量保留导航等页面内容。",
    )
    parser.add_argument(
        "--no-screenshot",
        action="store_true",
        help="不请求 Firecrawl 截图，因此也不会生成 page.png/page.pdf。",
    )
    parser.add_argument("--no-pdf", action="store_true", help="不把截图转成 page.pdf。")
    parser.add_argument(
        "--no-png",
        action="store_true",
        help="不保存 page.png；若要生成 PDF，仍会临时使用截图 bytes。",
    )
    parser.add_argument(
        "--include-html",
        action="store_true",
        help="额外请求 html，并写入 page.html。",
    )
    parser.add_argument(
        "--include-images",
        action="store_true",
        help="额外请求 images 列表。会消耗同一次 scrape 的更多字段。",
    )
    parser.add_argument(
        "--proxy",
        choices=["basic", "stealth", "enhanced", "auto"],
        help="透传 Firecrawl proxy 档位；默认由 Firecrawl 决定。",
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help="只测试到 Firecrawl API 的网络连通性，不消耗抓取额度。",
    )
    return parser.parse_args()


def load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def resolve_api_key(cli_value: str | None) -> str | None:
    if cli_value:
        return cli_value
    if os.getenv("FIRECRAWL_API_KEY"):
        return os.getenv("FIRECRAWL_API_KEY")

    env_values = load_env_file(REPO_ROOT / "configs" / ".env")
    return (
        env_values.get("FIRECRAWL_API_KEY")
        or env_values.get("firecrawl_api_key")
        or env_values.get("firecrawl_api")
    )


def options_from_args(args: argparse.Namespace) -> FirecrawlOptions:
    formats: list[Any] = ["markdown", "links"]
    if args.include_html:
        formats.append("html")
    if args.include_images:
        formats.append("images")
    if not args.no_screenshot:
        formats.append({"type": "screenshot", "full_page": True, "quality": 85})

    return FirecrawlOptions(
        out_dir=args.out_dir.expanduser().resolve(),
        api_key=resolve_api_key(args.api_key),
        api_url=args.api_url,
        http_proxy=None if args.no_http_proxy else args.http_proxy,
        timeout_ms=args.timeout_ms,
        only_main_content=not args.all_content,
        full_page_screenshot=not args.no_screenshot,
        write_pdf=not args.no_pdf and not args.no_screenshot,
        save_png=not args.no_png and not args.no_screenshot,
        formats=formats,
    )


def apply_http_proxy(http_proxy: str | None) -> None:
    if not http_proxy:
        return
    os.environ.setdefault("HTTP_PROXY", http_proxy)
    os.environ.setdefault("HTTPS_PROXY", http_proxy)
    os.environ.setdefault("http_proxy", http_proxy)
    os.environ.setdefault("https_proxy", http_proxy)


def proxy_opener(http_proxy: str | None) -> urllib.request.OpenerDirector | None:
    if not http_proxy:
        return None
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": http_proxy, "https": http_proxy})
    )


def probe_api(api_url: str, *, http_proxy: str | None) -> int | None:
    """轻量网络探测：只验证 API 域名可达，不调用 scrape。"""
    url = api_url.rstrip("/") + "/"
    req = urllib.request.Request(url, method="GET")
    opener = proxy_opener(http_proxy)
    try:
        open_url = opener.open if opener else urllib.request.urlopen
        with open_url(req, timeout=20) as response:
            print(f"Firecrawl API reachable: HTTP {response.status} {url}")
            return response.status
    except urllib.error.HTTPError as exc:
        print(f"Firecrawl API reachable: HTTP {exc.code} {url}")
        return exc.code
    except Exception as exc:
        print(f"Firecrawl API probe failed: {type(exc).__name__}: {exc}")
        return None


def _slug(url: str) -> str:
    parsed = urlparse(url)
    raw = (parsed.netloc + parsed.path).strip("/")
    if parsed.query:
        raw += "_" + parsed.query
    slug = "".join(c if c.isalnum() or c in "-_." else "_" for c in raw)
    return (slug or "page")[:100]


def _clean_heading(text: str) -> str:
    return re.sub(r"\s*\[[^\]]*\]\([^)]*\)", "", text).strip()


def _dedupe(seq: list[str]) -> list[str]:
    seen, out = set(), []
    for item in seq:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    if hasattr(value, "dict"):
        return value.dict(exclude_none=True)
    return {"value": str(value)}


def doc_field(doc: Any, name: str, default: Any = None) -> Any:
    if isinstance(doc, dict):
        return doc.get(name, default)
    return getattr(doc, name, default)


def scrape(url: str, options: FirecrawlOptions, *, proxy: str | None = None) -> Any:
    if not options.api_key and "api.firecrawl.dev" in options.api_url:
        raise RuntimeError(
            "FIRECRAWL_API_KEY is not set. Add it to configs/.env or pass --api-key."
        )

    try:
        from firecrawl import Firecrawl
    except ImportError as exc:
        raise RuntimeError(
            "Missing package: firecrawl. Install it with `pip install firecrawl` in this venv."
        ) from exc

    apply_http_proxy(options.http_proxy)
    client = Firecrawl(api_key=options.api_key, api_url=options.api_url)
    kwargs: dict[str, Any] = {
        "formats": options.formats,
        "only_main_content": options.only_main_content,
        "timeout": options.timeout_ms,
        "block_ads": True,
        "integration": "pipelines-rag-parsers",
    }
    if proxy:
        kwargs["proxy"] = proxy
    return client.scrape(url, **kwargs)


def to_snapshot(doc: Any, source_url: str) -> FirecrawlSnapshot:
    markdown = doc_field(doc, "markdown", "") or ""
    metadata = as_dict(doc_field(doc, "metadata", {}))
    links = _dedupe(doc_field(doc, "links", []) or [])
    images = _dedupe(doc_field(doc, "images", []) or [])
    headings = [_clean_heading(m.group(2)) for m in re.finditer(r"^(#{1,3})\s+(.+)$", markdown, re.M)]
    final_url = metadata.get("url") or metadata.get("source_url") or source_url

    return FirecrawlSnapshot(
        url=final_url,
        source_url=source_url,
        title=metadata.get("title") or (headings[0] if headings else None),
        description=metadata.get("description") or metadata.get("og_description"),
        status_code=metadata.get("status_code"),
        word_count=len(markdown.split()),
        headings=headings,
        markdown=markdown,
        links=links,
        images=images,
        screenshot=doc_field(doc, "screenshot"),
        metadata=metadata,
        fetched_at=datetime.now(timezone.utc).isoformat(),
    )


def screenshot_to_bytes(screenshot: str | None, *, http_proxy: str | None) -> bytes | None:
    if not screenshot:
        return None

    if screenshot.startswith("data:"):
        _, _, payload = screenshot.partition(",")
        if payload:
            return base64.b64decode(payload)
        return None

    if screenshot.startswith(("http://", "https://")):
        req = urllib.request.Request(screenshot, headers={"User-Agent": "pipelines-rag/0.4"})
        opener = proxy_opener(http_proxy)
        open_url = opener.open if opener else urllib.request.urlopen
        with open_url(req, timeout=60) as response:
            return response.read()

    try:
        return base64.b64decode(screenshot)
    except Exception:
        return None


def screenshot_to_pdf(png_bytes: bytes, out_path: Path) -> int:
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError(
            "Missing package: pillow. It is required to convert Firecrawl screenshots to PDF."
        ) from exc

    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    width, height = img.size
    page_h = max(1, int(width * 1.414))
    pages = []
    y = 0
    while y < height:
        crop = img.crop((0, y, width, min(y + page_h, height)))
        if crop.height < page_h:
            bg = Image.new("RGB", (width, page_h), "white")
            bg.paste(crop, (0, 0))
            crop = bg
        pages.append(crop)
        y += page_h
    if pages:
        pages[0].save(out_path, "PDF", save_all=True, append_images=pages[1:], resolution=96.0)
    return len(pages)


def save(snapshot: FirecrawlSnapshot, doc: Any, options: FirecrawlOptions) -> Path:
    out_dir = options.out_dir / _slug(snapshot.source_url)
    out_dir.mkdir(parents=True, exist_ok=True)

    raw_doc = as_dict(doc)
    (out_dir / "page.json").write_text(
        json.dumps(
            {
                "snapshot": snapshot.model_dump(mode="json"),
                "raw": raw_doc,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (out_dir / "page.md").write_text(snapshot.markdown, encoding="utf-8")

    html = doc_field(doc, "html")
    if html:
        (out_dir / "page.html").write_text(html, encoding="utf-8")

    png_bytes = None
    pages = 0
    if options.full_page_screenshot and snapshot.screenshot:
        try:
            png_bytes = screenshot_to_bytes(snapshot.screenshot, http_proxy=options.http_proxy)
        except Exception as exc:
            print(f"  截图下载失败：{type(exc).__name__}: {exc}")

    if png_bytes and options.save_png:
        (out_dir / "page.png").write_bytes(png_bytes)
    if png_bytes and options.write_pdf:
        pages = screenshot_to_pdf(png_bytes, out_dir / "page.pdf")

    print(f"  已写出 -> {out_dir}")
    msg = "    page.json / page.md"
    if html:
        msg += " / page.html"
    if png_bytes and options.save_png:
        msg += " / page.png"
    if pages:
        msg += f" / page.pdf（{pages} 页 A4）"
    elif options.write_pdf:
        msg += "（无可用截图，未生成 PDF）"
    print(msg)
    return out_dir


def main() -> None:
    args = parse_args()
    options = options_from_args(args)

    if args.probe:
        probe_api(options.api_url, http_proxy=options.http_proxy)
        return

    urls = args.urls or DEFAULT_URLS
    print(f"输出目录 : {options.out_dir}")
    print(f"待抓取   : {len(urls)} 个 URL")
    print(f"API URL  : {options.api_url}")
    print(f"HTTP代理 : {options.http_proxy or '(none)'}")
    print(f"Formats  : {options.formats}")
    print()

    ok = 0
    for index, url in enumerate(urls, 1):
        print(f"[{index}/{len(urls)}] {url}")
        try:
            doc = scrape(url, options, proxy=args.proxy)
            snapshot = to_snapshot(doc, url)
            print(
                f"  状态码={snapshot.status_code} | 标题={snapshot.title!r} | "
                f"正文={snapshot.word_count}词 | 链接={len(snapshot.links)}"
            )
            save(snapshot, doc, options)
            ok += 1
        except Exception as exc:
            print(f"  Firecrawl 抓取失败：{type(exc).__name__}: {exc}")
        print()

    print(f"完成：{ok}/{len(urls)} 成功。")


if __name__ == "__main__":
    main()
