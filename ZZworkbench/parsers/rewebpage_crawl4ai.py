"""crawl4ai adapter: webpage URL -> normalized static page bundle.

Single-URL runs write ``page.json`` and ``page.md`` directly under
``--out-dir``.  PDF, PNG, HTML and MHTML artifacts are optional.  Imports of
the browser SDK are lazy so ``--help`` and ``--dry-run`` work without the
optional dependency installed.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from parsers.rewebpage_common import (
    as_jsonable,
    decode_base64_bytes,
    describe_planned_outputs,
    make_snapshot,
    output_dir_for_url,
    validate_urls,
    write_page_bundle,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "outputs" / "webpages"
DEFAULT_PROXY_SERVER = (
    os.environ.get("HTTPS_PROXY")
    or os.environ.get("https_proxy")
    or os.environ.get("HTTP_PROXY")
    or os.environ.get("http_proxy")
)


@dataclass(frozen=True)
class CrawlOptions:
    out_dir: Path
    proxy_server: str | None
    proxy_bypass: list[str]
    host_resolver_rules: list[str]
    headless: bool
    timeout_ms: int
    delay_seconds: float
    retries: int
    prune_threshold: float
    raw_markdown: bool
    write_pdf: bool
    keep_png: bool
    include_html: bool
    include_mhtml: bool
    wait_for: str | None
    respect_robots: bool


def _split_csv(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="用 crawl4ai 抓取网页，输出统一 page.json / page.md 静态快照。",
    )
    parser.add_argument("urls", nargs="*", help="一个或多个绝对 http(s) URL。")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"输出目录；单 URL 直接写入此目录。默认 {DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument(
        "--proxy-server",
        default=DEFAULT_PROXY_SERVER,
        help="Chromium 代理；默认读取 HTTPS_PROXY/HTTP_PROXY，不再假定本机固定端口。",
    )
    parser.add_argument(
        "--no-browser-proxy", action="store_true", help="禁用 Chromium 代理。"
    )
    parser.add_argument(
        "--proxy-bypass",
        default="",
        help="逗号分隔的 Chromium proxy bypass 域名。",
    )
    parser.add_argument(
        "--host-resolver-rule",
        action="append",
        default=[],
        help="Chromium --host-resolver-rules 条目，可重复传入。",
    )
    parser.add_argument("--headful", action="store_true", help="显示浏览器窗口。")
    parser.add_argument("--timeout-ms", type=int, default=60_000, help="页面超时毫秒。")
    parser.add_argument(
        "--delay", type=float, default=1.0, help="截图/返回前额外等待秒数。"
    )
    parser.add_argument(
        "--retries", type=int, default=3, help="每个 URL 最大尝试次数。"
    )
    parser.add_argument(
        "--prune-threshold",
        type=float,
        default=0.45,
        help="PruningContentFilter threshold。",
    )
    parser.add_argument(
        "--raw-markdown", action="store_true", help="使用 raw_markdown。"
    )
    parser.add_argument("--no-pdf", action="store_true", help="不生成浏览器打印 PDF。")
    parser.add_argument(
        "--keep-png", action="store_true", help="保存整页截图 page.png。"
    )
    parser.add_argument(
        "--include-html", action="store_true", help="保存清洗后的 page.html。"
    )
    parser.add_argument(
        "--include-mhtml", action="store_true", help="保存 page.mhtml。"
    )
    parser.add_argument(
        "--wait-for",
        help="crawl4ai wait_for 表达式，例如 css:.article-loaded。",
    )
    parser.add_argument(
        "--respect-robots",
        action="store_true",
        help="启用 crawl4ai check_robots_txt。",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="只打印计划产物，不抓取、不写文件。"
    )
    return parser.parse_args(argv)


def options_from_args(args: argparse.Namespace) -> CrawlOptions:
    if args.timeout_ms <= 0:
        raise ValueError("--timeout-ms must be greater than zero.")
    if args.retries <= 0:
        raise ValueError("--retries must be greater than zero.")
    if not 0.0 <= args.prune_threshold <= 1.0:
        raise ValueError("--prune-threshold must be between 0 and 1.")
    return CrawlOptions(
        out_dir=args.out_dir.expanduser().resolve(),
        proxy_server=None if args.no_browser_proxy else args.proxy_server,
        proxy_bypass=_split_csv(args.proxy_bypass),
        host_resolver_rules=list(args.host_resolver_rule),
        headless=not args.headful,
        timeout_ms=args.timeout_ms,
        delay_seconds=max(0.0, args.delay),
        retries=args.retries,
        prune_threshold=args.prune_threshold,
        raw_markdown=args.raw_markdown,
        write_pdf=not args.no_pdf,
        keep_png=args.keep_png,
        include_html=args.include_html,
        include_mhtml=args.include_mhtml,
        wait_for=args.wait_for,
        respect_robots=args.respect_robots,
    )


def _build_browser_config(options: CrawlOptions) -> Any:
    try:
        from crawl4ai import BrowserConfig
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "Missing package: crawl4ai. Install the `webpage-crawl4ai` extra and run "
            "`crawl4ai-setup`."
        ) from exc

    extra_args: list[str] = []
    if options.proxy_server:
        extra_args.append(f"--proxy-server={options.proxy_server}")
        if options.proxy_bypass:
            extra_args.append("--proxy-bypass-list=" + ";".join(options.proxy_bypass))
    if options.host_resolver_rules:
        extra_args.append(
            "--host-resolver-rules=" + ",".join(options.host_resolver_rules)
        )
    return BrowserConfig(
        headless=options.headless,
        user_agent_mode="random",
        viewport_width=1280,
        viewport_height=900,
        extra_args=extra_args,
    )


def _build_run_config(options: CrawlOptions) -> Any:
    try:
        from crawl4ai import CacheMode, CrawlerRunConfig
        from crawl4ai.content_filter_strategy import PruningContentFilter
        from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "Missing package: crawl4ai. Install the `webpage-crawl4ai` extra."
        ) from exc

    return CrawlerRunConfig(
        markdown_generator=DefaultMarkdownGenerator(
            content_filter=PruningContentFilter(
                threshold=options.prune_threshold,
                threshold_type="dynamic",
            )
        ),
        cache_mode=CacheMode.BYPASS,
        wait_until="networkidle",
        wait_for=options.wait_for,
        page_timeout=options.timeout_ms,
        delay_before_return_html=options.delay_seconds,
        magic=True,
        simulate_user=True,
        override_navigator=True,
        scan_full_page=True,
        wait_for_images=True,
        check_robots_txt=options.respect_robots,
        screenshot=options.keep_png,
        screenshot_wait_for=options.delay_seconds,
        pdf=options.write_pdf,
        capture_mhtml=options.include_mhtml,
    )


async def _fetch(crawler: Any, url: str, run_config: Any, retries: int) -> Any:
    last_error = "unknown error"
    for attempt in range(1, retries + 1):
        result = await crawler.arun(url=url, config=run_config)
        if result.success:
            return result
        last_error = (result.error_message or last_error).splitlines()[0]
        if attempt < retries:
            print(f"  第 {attempt}/{retries} 次失败：{last_error}")
            await asyncio.sleep(min(2 * attempt, 5))
    raise RuntimeError(f"crawl4ai failed after {retries} attempt(s): {last_error}")


def _markdown_text(result: Any, *, raw_markdown: bool) -> str:
    value = result.markdown
    if isinstance(value, str):
        return value
    raw = getattr(value, "raw_markdown", "") or ""
    fit = getattr(value, "fit_markdown", "") or ""
    return raw if raw_markdown else (fit or raw)


def to_snapshot(result: Any, source_url: str, *, raw_markdown: bool):
    metadata = as_jsonable(result.metadata or {})
    links = result.links or {}
    internal = [item.get("href", "") for item in links.get("internal", [])]
    external = [item.get("href", "") for item in links.get("external", [])]
    media = result.media or {}
    images = [item.get("src", "") for item in media.get("images", [])]
    final_url = getattr(result, "redirected_url", None) or result.url or source_url
    return make_snapshot(
        provider="crawl4ai",
        source_url=source_url,
        final_url=final_url,
        markdown=_markdown_text(result, raw_markdown=raw_markdown),
        title=metadata.get("title"),
        description=metadata.get("description"),
        status_code=getattr(result, "status_code", None),
        internal_links=internal,
        external_links=external,
        images=images,
        metadata=metadata,
    )


def save_result(
    result: Any, source_url: str, options: CrawlOptions, *, url_count: int
) -> Path:
    snapshot = to_snapshot(result, source_url, raw_markdown=options.raw_markdown)
    out_dir = output_dir_for_url(options.out_dir, source_url, url_count=url_count)
    png_bytes = decode_base64_bytes(result.screenshot) if options.keep_png else None
    raw = {
        "links": result.links or {},
        "media": result.media or {},
        "tables": getattr(result, "tables", None),
        "metadata": result.metadata or {},
    }
    written = write_page_bundle(
        out_dir,
        snapshot,
        raw=raw,
        html=(getattr(result, "cleaned_html", None) or result.html)
        if options.include_html
        else None,
        png_bytes=png_bytes,
        pdf_bytes=result.pdf if options.write_pdf else None,
        mhtml=result.mhtml if options.include_mhtml else None,
    )
    print(f"  已写出 -> {out_dir}")
    print("    " + " / ".join(path.name for path in written))
    return out_dir


async def run(urls: list[str], options: CrawlOptions) -> int:
    try:
        from crawl4ai import AsyncWebCrawler
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "Missing package: crawl4ai. Install the `webpage-crawl4ai` extra and run "
            "`crawl4ai-setup`."
        ) from exc

    browser_config = _build_browser_config(options)
    run_config = _build_run_config(options)
    failures = 0
    async with AsyncWebCrawler(config=browser_config) as crawler:
        for index, url in enumerate(urls, 1):
            print(f"[{index}/{len(urls)}] {url}")
            try:
                result = await _fetch(crawler, url, run_config, options.retries)
                snapshot = to_snapshot(result, url, raw_markdown=options.raw_markdown)
                print(
                    f"  状态码={snapshot.status_code} | 标题={snapshot.title!r} | "
                    f"正文={snapshot.word_count}词"
                )
                save_result(result, url, options, url_count=len(urls))
            except Exception as exc:  # remote/browser failures need per-URL isolation
                failures += 1
                print(f"  抓取失败：{type(exc).__name__}: {exc}")
    print(f"完成：{len(urls) - failures}/{len(urls)} 成功。")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        urls = validate_urls(args.urls)
        options = options_from_args(args)
    except ValueError as exc:
        print(f"Error: {exc}")
        return 2

    if args.dry_run:
        optional = []
        if options.write_pdf:
            optional.append("page.pdf")
        if options.keep_png:
            optional.append("page.png")
        if options.include_html:
            optional.append("page.html")
        if options.include_mhtml:
            optional.append("page.mhtml")
        for path in describe_planned_outputs(
            options.out_dir, urls, optional_names=optional
        ):
            print(path)
        return 0

    try:
        return asyncio.run(run(urls, options))
    except RuntimeError as exc:
        print(f"Error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
