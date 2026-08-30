"""Scrapling adapter: webpage URL -> normalized JSON / Markdown / HTML.

Scrapling is useful as a lightweight HTTP fetcher and as a browser/stealth
fallback.  It does not emit a visual PDF in this adapter; use crawl4ai or
Firecrawl when a screenshot PDF is required.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from parsers.rewebpage_common import (
    as_jsonable,
    describe_planned_outputs,
    make_snapshot,
    output_dir_for_url,
    validate_urls,
    write_page_bundle,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "outputs" / "webpages"
DEFAULT_PROXY = (
    os.environ.get("HTTPS_PROXY")
    or os.environ.get("https_proxy")
    or os.environ.get("HTTP_PROXY")
    or os.environ.get("http_proxy")
)
MAIN_CONTENT_SELECTORS = ("article", "main", "[role='main']", "body")


@dataclass(frozen=True)
class ScraplingOptions:
    out_dir: Path
    fetcher: str
    selector: str | None
    proxy: str | None
    timeout_ms: int
    retries: int
    retry_delay: float
    headless: bool
    wait_ms: int
    wait_selector: str | None
    network_idle: bool
    solve_cloudflare: bool
    include_html: bool


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="用 Scrapling 抓取网页，输出统一 page.json / page.md 静态快照。",
    )
    parser.add_argument("urls", nargs="*", help="一个或多个绝对 http(s) URL。")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"输出目录；单 URL 直接写入此目录。默认 {DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument(
        "--fetcher",
        choices=["http", "dynamic", "stealthy"],
        default="http",
        help="http 最轻；dynamic 执行 JS；stealthy 面向强反爬。",
    )
    parser.add_argument(
        "--selector",
        help="只把此 CSS selector 的内容转为 Markdown；默认依次尝试 article/main/body。",
    )
    parser.add_argument(
        "--proxy",
        default=DEFAULT_PROXY,
        help="代理 URL；默认读取 HTTPS_PROXY/HTTP_PROXY。",
    )
    parser.add_argument("--no-proxy", action="store_true", help="禁用代理。")
    parser.add_argument("--timeout-ms", type=int, default=30_000, help="超时毫秒。")
    parser.add_argument("--retries", type=int, default=3, help="失败重试次数。")
    parser.add_argument("--retry-delay", type=float, default=1.0, help="重试间隔秒数。")
    parser.add_argument(
        "--headful", action="store_true", help="显示 dynamic/stealthy 浏览器。"
    )
    parser.add_argument(
        "--wait-ms", type=int, default=0, help="浏览器加载后额外等待毫秒。"
    )
    parser.add_argument("--wait-selector", help="浏览器等待出现的 CSS selector。")
    parser.add_argument(
        "--network-idle", action="store_true", help="浏览器等待 network idle。"
    )
    parser.add_argument(
        "--solve-cloudflare",
        action="store_true",
        help="仅 stealthy 模式：启用 Scrapling Cloudflare challenge 处理。",
    )
    parser.add_argument(
        "--include-html", action="store_true", help="保存完整响应为 page.html。"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="只打印计划产物，不抓取、不写文件。"
    )
    return parser.parse_args(argv)


def options_from_args(args: argparse.Namespace) -> ScraplingOptions:
    if args.timeout_ms <= 0:
        raise ValueError("--timeout-ms must be greater than zero.")
    if args.retries <= 0:
        raise ValueError("--retries must be greater than zero.")
    if args.retry_delay < 0:
        raise ValueError("--retry-delay must not be negative.")
    if args.solve_cloudflare and args.fetcher != "stealthy":
        raise ValueError("--solve-cloudflare requires --fetcher stealthy.")
    return ScraplingOptions(
        out_dir=args.out_dir.expanduser().resolve(),
        fetcher=args.fetcher,
        selector=args.selector,
        proxy=None if args.no_proxy else args.proxy,
        timeout_ms=args.timeout_ms,
        retries=args.retries,
        retry_delay=args.retry_delay,
        headless=not args.headful,
        wait_ms=max(0, args.wait_ms),
        wait_selector=args.wait_selector,
        network_idle=args.network_idle,
        solve_cloudflare=args.solve_cloudflare,
        include_html=args.include_html,
    )


def fetch_page(url: str, options: ScraplingOptions) -> Any:
    try:
        from scrapling.fetchers import DynamicFetcher, Fetcher, StealthyFetcher
    except (
        ImportError,
        ModuleNotFoundError,
    ) as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "Missing Scrapling fetchers. Install the `webpage-scrapling` extra and, "
            "for browser modes, run `scrapling install`."
        ) from exc

    if options.fetcher == "http":
        kwargs: dict[str, Any] = {
            "timeout": options.timeout_ms / 1000,
            "retries": options.retries,
            "retry_delay": options.retry_delay,
            "impersonate": "chrome",
        }
        if options.proxy:
            kwargs["proxy"] = options.proxy
        return Fetcher.get(url, **kwargs)

    kwargs = {
        "headless": options.headless,
        "timeout": options.timeout_ms,
        "wait": options.wait_ms,
        "network_idle": options.network_idle,
        "retries": options.retries,
        "retry_delay": options.retry_delay,
        "block_ads": True,
    }
    if options.proxy:
        kwargs["proxy"] = options.proxy
    if options.wait_selector:
        kwargs["wait_selector"] = options.wait_selector
    if options.fetcher == "stealthy":
        kwargs["solve_cloudflare"] = options.solve_cloudflare
        return StealthyFetcher.fetch(url, **kwargs)
    return DynamicFetcher.fetch(url, **kwargs)


def response_html(page: Any) -> str:
    body = page.body
    if isinstance(body, str):
        return body
    encoding = getattr(page, "encoding", None) or "utf-8"
    try:
        return body.decode(encoding, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def _selector_html(node: Any) -> str:
    value = getattr(node, "html_content", None)
    if callable(value):
        value = value()
    return str(value or "")


def select_content_html(page: Any, selector: str | None) -> str:
    selectors = (selector,) if selector else MAIN_CONTENT_SELECTORS
    for candidate in selectors:
        matches = page.css(candidate)
        if not matches:
            continue
        parts = []
        for node in matches:
            content = _selector_html(node)
            if content:
                parts.append(content)
        html = "\n".join(parts)
        if html.strip():
            return html
    if selector:
        raise RuntimeError(f"CSS selector matched no content: {selector}")
    return response_html(page)


def html_to_markdown(html: str) -> str:
    try:
        from markdownify import markdownify
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "Missing package: markdownify. Install the `webpage-scrapling` extra."
        ) from exc
    return markdownify(
        html,
        heading_style="ATX",
        bullets="-",
        strip=["script", "style", "noscript", "template"],
    )


def _get_all(page: Any, selector: str) -> list[str]:
    values = page.css(selector)
    if hasattr(values, "getall"):
        return [str(value) for value in values.getall()]
    return [str(value) for value in values]


def _first(page: Any, selector: str) -> str | None:
    values = page.css(selector)
    if hasattr(values, "get"):
        value = values.get()
        return str(value).strip() if value else None
    return str(values[0]).strip() if values else None


def to_snapshot(page: Any, source_url: str, options: ScraplingOptions):
    final_url = str(getattr(page, "url", None) or source_url)
    status = getattr(page, "status", None)
    if status is not None and int(status) >= 400:
        raise RuntimeError(f"HTTP {status} for {final_url}")

    markdown = html_to_markdown(select_content_html(page, options.selector))
    metadata = {
        "fetcher": options.fetcher,
        "selector": options.selector,
        "reason": getattr(page, "reason", None),
        "headers": as_jsonable(getattr(page, "headers", None)),
    }
    return make_snapshot(
        provider="scrapling",
        source_url=source_url,
        final_url=final_url,
        markdown=markdown,
        title=_first(page, "title::text") or _first(page, "h1::text"),
        description=_first(page, 'meta[name="description"]::attr(content)'),
        status_code=int(status) if status is not None else None,
        links=_get_all(page, "a::attr(href)"),
        images=_get_all(page, "img::attr(src)"),
        metadata=metadata,
    )


def save_page(
    page: Any, source_url: str, options: ScraplingOptions, *, url_count: int
) -> Path:
    snapshot = to_snapshot(page, source_url, options)
    out_dir = output_dir_for_url(options.out_dir, source_url, url_count=url_count)
    raw = {
        "status": getattr(page, "status", None),
        "reason": getattr(page, "reason", None),
        "headers": getattr(page, "headers", None),
        "request_headers": getattr(page, "request_headers", None),
        "history": getattr(page, "history", None),
        "meta": getattr(page, "meta", None),
    }
    written = write_page_bundle(
        out_dir,
        snapshot,
        raw=raw,
        html=response_html(page) if options.include_html else None,
    )
    print(f"  已写出 -> {out_dir}")
    print("    " + " / ".join(path.name for path in written))
    return out_dir


def run(urls: list[str], options: ScraplingOptions) -> int:
    failures = 0
    for index, url in enumerate(urls, 1):
        print(f"[{index}/{len(urls)}] {url}")
        try:
            page = fetch_page(url, options)
            snapshot = to_snapshot(page, url, options)
            print(
                f"  状态码={snapshot.status_code} | 标题={snapshot.title!r} | "
                f"正文={snapshot.word_count}词"
            )
            save_page(page, url, options, url_count=len(urls))
        except Exception as exc:  # network/browser failures are isolated per URL
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
        optional = ["page.html"] if options.include_html else []
        for path in describe_planned_outputs(
            options.out_dir, urls, optional_names=optional
        ):
            print(path)
        return 0
    return run(urls, options)


if __name__ == "__main__":
    raise SystemExit(main())
