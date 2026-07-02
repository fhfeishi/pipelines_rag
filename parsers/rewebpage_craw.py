"""crawl4ai 网页抓取脚本：网页 -> JSON / Markdown / PDF。

默认抓取 Qwen AgentWorld 博客，并把结果写到：

    outputs/webpages/<page-slug>/
      page.json
      page.md
      page.pdf

`page.pdf` 可直接作为 `parsers.script_oppdf` 的输入。

用法：
    python -m parsers.script_craw
    python -m parsers.script_craw https://qwen.ai/blog?id=qwen-agentworld
    python -m parsers.script_craw --out-dir outputs/webpages --keep-png
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field, HttpUrl


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_URLS = ["https://qwen.ai/blog?id=qwen-agentworld"]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "outputs" / "webpages"

# WSL2 + Windows Clash/TUN 环境下 qwen.ai 的已验证绕行配置，详见 craw_notes.md。
DEFAULT_PROXY_SERVER = (
    os.environ.get("https_proxy")
    or os.environ.get("HTTPS_PROXY")
    or os.environ.get("http_proxy")
    or os.environ.get("HTTP_PROXY")
    or "http://127.0.0.1:7897"
)
DEFAULT_PROXY_BYPASS = ["qwen.ai", "*.qwen.ai"]
DEFAULT_HOST_RESOLVER_RULES = [
    "MAP qwen.ai 139.95.10.252",
    "MAP *.qwen.ai 139.95.10.252",
]

UNLOCK_SCROLL_JS = """
(() => {
  document.querySelectorAll('*').forEach(el => {
    if (el === document.documentElement || el === document.body) return;
    const s = getComputedStyle(el);
    if (['auto','scroll','hidden'].includes(s.overflowY) ||
        ['auto','scroll','hidden'].includes(s.overflow)) {
      el.style.overflow = 'visible';
      el.style.maxHeight = 'none';
      if (s.height && s.height !== 'auto' && parseInt(s.height) > 400) el.style.height = 'auto';
    }
  });
  document.documentElement.style.overflow = 'visible';
  document.body.style.overflow = 'visible';
})();
"""


class PageSnapshot(BaseModel):
    """单个网页的结构化快照。"""

    url: HttpUrl = Field(description="实际抓取到的页面地址（含重定向后地址）")
    source_url: str = Field(description="用户传入的原始 URL")
    title: str | None = Field(default=None, description="文章标题，优先正文首个 H1")
    description: str | None = Field(default=None, description="meta description")
    status_code: int | None = Field(default=None, description="HTTP 状态码")
    word_count: int = Field(default=0, description="正文词数（粗略）")
    headings: list[str] = Field(default_factory=list, description="正文 H1~H3 大纲")
    markdown: str = Field(default="", description="清洗后的正文 markdown")
    links_internal: list[str] = Field(default_factory=list, description="站内链接")
    links_external: list[str] = Field(default_factory=list, description="站外链接")
    fetched_at: str = Field(description="抓取时间（UTC ISO8601）")


@dataclass(frozen=True)
class CrawlOptions:
    out_dir: Path
    proxy_server: str | None
    proxy_bypass: list[str]
    host_resolver_rules: list[str]
    headless: bool
    timeout_ms: int
    delay_before_return_html: float
    retries: int
    prune_threshold: float
    write_pdf: bool
    keep_png: bool
    raw_markdown: bool


_SCREENSHOTS: dict[str, bytes] = {}


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="用 crawl4ai 抓取网页，输出 page.json / page.md / page.pdf。",
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
    parser.add_argument(
        "--proxy-server",
        default=DEFAULT_PROXY_SERVER,
        help="Chromium 代理服务器；默认取环境变量，兜底 http://127.0.0.1:7897。",
    )
    parser.add_argument(
        "--no-browser-proxy",
        action="store_true",
        help="不向 Chromium 传 --proxy-server。",
    )
    parser.add_argument(
        "--proxy-bypass",
        default=",".join(DEFAULT_PROXY_BYPASS),
        help="逗号分隔的 Chromium proxy bypass 域名列表。",
    )
    parser.add_argument(
        "--host-resolver-rule",
        action="append",
        default=None,
        help="追加 Chromium --host-resolver-rules 条目；可重复传。",
    )
    parser.add_argument(
        "--no-host-resolver-rules",
        action="store_true",
        help="禁用默认 qwen.ai host resolver 映射。",
    )
    parser.add_argument(
        "--headful",
        action="store_true",
        help="显示浏览器窗口，便于观察反爬或渲染问题。",
    )
    parser.add_argument("--timeout-ms", type=int, default=60_000, help="页面超时毫秒。")
    parser.add_argument("--delay", type=float, default=2.0, help="渲染后额外等待秒数。")
    parser.add_argument("--retries", type=int, default=3, help="抓取失败重试次数。")
    parser.add_argument(
        "--prune-threshold",
        type=float,
        default=0.45,
        help="crawl4ai PruningContentFilter threshold。",
    )
    parser.add_argument("--no-pdf", action="store_true", help="只输出 JSON/Markdown，不生成 PDF。")
    parser.add_argument("--keep-png", action="store_true", help="额外保存整页长截图 page.png。")
    parser.add_argument(
        "--raw-markdown",
        action="store_true",
        help="page.md 使用 raw_markdown，而不是默认 fit_markdown。",
    )
    return parser.parse_args()


def options_from_args(args: argparse.Namespace) -> CrawlOptions:
    host_rules = [] if args.no_host_resolver_rules else list(DEFAULT_HOST_RESOLVER_RULES)
    if args.host_resolver_rule:
        host_rules.extend(args.host_resolver_rule)

    return CrawlOptions(
        out_dir=args.out_dir.expanduser().resolve(),
        proxy_server=None if args.no_browser_proxy else args.proxy_server,
        proxy_bypass=_split_csv(args.proxy_bypass),
        host_resolver_rules=host_rules,
        headless=not args.headful,
        timeout_ms=args.timeout_ms,
        delay_before_return_html=args.delay,
        retries=max(1, args.retries),
        prune_threshold=args.prune_threshold,
        write_pdf=not args.no_pdf,
        keep_png=args.keep_png,
        raw_markdown=args.raw_markdown,
    )


def _build_browser_config(options: CrawlOptions):
    try:
        from crawl4ai import BrowserConfig
    except ImportError as exc:
        raise RuntimeError(
            "Missing package: crawl4ai. Install project/parser dependencies first."
        ) from exc

    extra_args: list[str] = []
    if options.proxy_server:
        extra_args.append(f"--proxy-server={options.proxy_server}")
        if options.proxy_bypass:
            extra_args.append("--proxy-bypass-list=" + ";".join(options.proxy_bypass))
    if options.host_resolver_rules:
        extra_args.append("--host-resolver-rules=" + ",".join(options.host_resolver_rules))

    return BrowserConfig(
        headless=options.headless,
        user_agent_mode="random",
        viewport_width=1280,
        viewport_height=900,
        extra_args=extra_args,
    )


def _build_run_config(options: CrawlOptions):
    try:
        from crawl4ai import CacheMode, CrawlerRunConfig
        from crawl4ai.content_filter_strategy import PruningContentFilter
        from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
    except ImportError as exc:
        raise RuntimeError(
            "Missing package: crawl4ai. Install project/parser dependencies first."
        ) from exc

    return CrawlerRunConfig(
        markdown_generator=DefaultMarkdownGenerator(
            content_filter=PruningContentFilter(
                threshold=options.prune_threshold,
                threshold_type="dynamic",
            ),
        ),
        cache_mode=CacheMode.BYPASS,
        wait_until="networkidle",
        page_timeout=options.timeout_ms,
        delay_before_return_html=options.delay_before_return_html,
        magic=True,
        simulate_user=True,
        override_navigator=True,
        scan_full_page=True,
        wait_for_images=True,
        pdf=False,
    )


async def _screenshot_hook(page, context=None, config=None, **kwargs):
    """before_retrieve_html 钩子：解开滚动容器，截整页 PNG 供转 PDF。"""
    try:
        await page.evaluate(UNLOCK_SCROLL_JS)
        await page.wait_for_timeout(600)
        await page.emulate_media(media="screen")
        _SCREENSHOTS[page.url] = await page.screenshot(full_page=True)
    except Exception as exc:  # pragma: no cover - depends on remote page/browser
        print(f"  整页截图失败，PDF 将跳过：{str(exc).splitlines()[0]}")
    return page


async def _fetch(crawler: Any, url: str, run_cfg: Any, retries: int):
    _SCREENSHOTS.clear()
    result = None
    for attempt in range(1, retries + 1):
        result = await crawler.arun(url=url, config=run_cfg)
        if result.success:
            return result
        first_line = (result.error_message or "未知错误").splitlines()[0]
        print(f"  第 {attempt}/{retries} 次抓取失败：{first_line}")
        if attempt < retries:
            await asyncio.sleep(2 * attempt)

    msg = result.error_message if result else "无结果"
    raise RuntimeError(
        f"抓取失败（已重试 {retries} 次）：{msg}\n"
        "  排查建议：1) 确认 URL 在浏览器能打开；2) 强反爬可改 --headful 观察；"
        "3) 若域名需绕过代理，调整 --proxy-bypass / --host-resolver-rule。"
    )


def _clean_heading(text: str) -> str:
    return re.sub(r"\s*\[[^\]]*\]\([^)]*\)", "", text).strip()


def _dedupe(seq: list[str]) -> list[str]:
    seen, out = set(), []
    for item in seq:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _markdown_text(result: Any, *, raw_markdown: bool) -> tuple[str, str]:
    md_obj = result.markdown
    fit_md = getattr(md_obj, "fit_markdown", "") or ""
    raw_md = getattr(md_obj, "raw_markdown", None) or (
        md_obj if isinstance(md_obj, str) else ""
    )
    content_md = raw_md if raw_markdown else (fit_md or raw_md)
    return content_md, raw_md


def to_snapshot(result: Any, source_url: str, *, raw_markdown: bool) -> PageSnapshot:
    content_md, raw_md = _markdown_text(result, raw_markdown=raw_markdown)
    meta = result.metadata or {}

    h1s = [_clean_heading(m.group(1)) for m in re.finditer(r"^#\s+(.+)$", raw_md, re.M)]
    title = (h1s[0] if h1s else None) or meta.get("title")
    headings = [_clean_heading(m.group(2)) for m in re.finditer(r"^(#{1,3})\s+(.+)$", raw_md, re.M)]

    links = result.links or {}
    internal = _dedupe([i["href"] for i in links.get("internal", []) if i.get("href")])
    external = _dedupe([i["href"] for i in links.get("external", []) if i.get("href")])
    final_url = getattr(result, "redirected_url", None) or getattr(result, "url", None) or source_url

    return PageSnapshot(
        url=final_url,
        source_url=source_url,
        title=title,
        description=meta.get("description"),
        status_code=getattr(result, "status_code", None),
        word_count=len(content_md.split()),
        headings=headings,
        markdown=content_md,
        links_internal=internal,
        links_external=external,
        fetched_at=datetime.now(timezone.utc).isoformat(),
    )


def _slug(url: str) -> str:
    parsed = urlparse(url)
    raw = (parsed.netloc + parsed.path).strip("/")
    if parsed.query:
        raw += "_" + parsed.query
    slug = "".join(c if c.isalnum() or c in "-_." else "_" for c in raw)
    return (slug or "page")[:100]


def _screenshot_to_pdf(png_bytes: bytes, out_path: Path) -> int:
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError(
            "Missing package: pillow. It is required to convert full-page PNG screenshots to PDF."
        ) from exc

    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    width, height = img.size
    page_h = max(1, int(width * 1.414))  # A4 portrait ratio
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


def _decode_result_pdf(result: Any) -> bytes | None:
    pdf = getattr(result, "pdf", None)
    if not pdf:
        return None
    if isinstance(pdf, bytes):
        return pdf
    if isinstance(pdf, str):
        try:
            return base64.b64decode(pdf)
        except Exception:
            return None
    return None


def save(
    snapshot: PageSnapshot,
    *,
    result: Any,
    png_bytes: bytes | None,
    options: CrawlOptions,
) -> Path:
    out_dir = options.out_dir / _slug(snapshot.source_url)
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "page.json").write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")
    (out_dir / "page.md").write_text(snapshot.markdown, encoding="utf-8")

    pages = 0
    if options.keep_png and png_bytes:
        (out_dir / "page.png").write_bytes(png_bytes)

    if options.write_pdf:
        if png_bytes:
            pages = _screenshot_to_pdf(png_bytes, out_dir / "page.pdf")
        else:
            pdf_bytes = _decode_result_pdf(result)
            if pdf_bytes:
                (out_dir / "page.pdf").write_bytes(pdf_bytes)
                pages = -1

    print(f"  已写出 -> {out_dir}")
    pdf_msg = " / page.pdf" if pages else ""
    if pages > 0:
        pdf_msg += f"（{pages} 页 A4）"
    print(f"    page.json / page.md{pdf_msg}")
    return out_dir


async def main(urls: list[str], options: CrawlOptions) -> None:
    try:
        from crawl4ai import AsyncWebCrawler
    except ImportError as exc:
        raise RuntimeError(
            "Missing package: crawl4ai. Install project/parser dependencies first."
        ) from exc

    print(f"输出目录 : {options.out_dir}")
    print(f"待抓取   : {len(urls)} 个 URL")
    if options.proxy_server:
        print(f"浏览器代理: {options.proxy_server}")
    if options.proxy_bypass:
        print(f"代理绕过 : {', '.join(options.proxy_bypass)}")
    print()

    browser_cfg = _build_browser_config(options)
    run_cfg = _build_run_config(options)

    ok = 0
    async with AsyncWebCrawler(config=browser_cfg) as crawler:
        if options.write_pdf:
            crawler.crawler_strategy.set_hook("before_retrieve_html", _screenshot_hook)

        for index, url in enumerate(urls, 1):
            print(f"[{index}/{len(urls)}] {url}")
            try:
                result = await _fetch(crawler, url, run_cfg, options.retries)
            except RuntimeError as exc:
                print(f"  {exc}\n")
                continue

            snapshot = to_snapshot(result, url, raw_markdown=options.raw_markdown)
            png = (
                _SCREENSHOTS.get(getattr(result, "url", ""))
                or _SCREENSHOTS.get(getattr(result, "redirected_url", ""))
                or next(iter(_SCREENSHOTS.values()), None)
            )

            print(
                f"  状态码={snapshot.status_code} | 标题={snapshot.title!r} | "
                f"正文={snapshot.word_count}词 | 链接={len(snapshot.links_internal)}内/"
                f"{len(snapshot.links_external)}外"
            )
            save(snapshot, result=result, png_bytes=png, options=options)
            ok += 1
            print()

    print(f"完成：{ok}/{len(urls)} 成功。")


if __name__ == "__main__":
    args = parse_args()
    target_urls = args.urls or DEFAULT_URLS
    asyncio.run(main(target_urls, options_from_args(args)))
