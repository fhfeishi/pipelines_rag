"""Firecrawl adapter: webpage URL -> normalized static page bundle."""

from __future__ import annotations

import argparse
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from parsers.rewebpage_common import (
    as_jsonable,
    decode_base64_bytes,
    describe_planned_outputs,
    image_to_a4_pdf,
    image_to_png,
    make_snapshot,
    output_dir_for_url,
    validate_urls,
    write_page_bundle,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "outputs" / "webpages"
DEFAULT_API_URL = "https://api.firecrawl.dev"
DEFAULT_HTTP_PROXY = (
    os.environ.get("HTTPS_PROXY")
    or os.environ.get("https_proxy")
    or os.environ.get("HTTP_PROXY")
    or os.environ.get("http_proxy")
)


@dataclass(frozen=True)
class FirecrawlOptions:
    out_dir: Path
    api_key: str | None
    api_url: str
    http_proxy: str | None
    timeout_ms: int
    retries: int
    only_main_content: bool
    full_page_screenshot: bool
    write_pdf: bool
    save_png: bool
    include_html: bool
    include_images: bool
    proxy_tier: str | None
    formats: list[Any]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="用 Firecrawl API 抓取网页，输出统一 page.json / page.md 静态快照。",
    )
    parser.add_argument("urls", nargs="*", help="一个或多个绝对 http(s) URL。")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"输出目录；单 URL 直接写入此目录。默认 {DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument(
        "--api-key", help="Firecrawl API key；默认从环境或 configs/.env 读取。"
    )
    parser.add_argument(
        "--api-url",
        default=os.getenv("FIRECRAWL_API_URL") or DEFAULT_API_URL,
        help=f"Firecrawl API URL，默认 {DEFAULT_API_URL}",
    )
    parser.add_argument(
        "--http-proxy",
        default=DEFAULT_HTTP_PROXY,
        help="访问 API/截图时的代理；默认读取 HTTPS_PROXY/HTTP_PROXY。",
    )
    parser.add_argument(
        "--no-http-proxy", action="store_true", help="禁用 API HTTP 代理。"
    )
    parser.add_argument(
        "--timeout-ms", type=int, default=60_000, help="Firecrawl 抓取超时毫秒。"
    )
    parser.add_argument(
        "--retries", type=int, default=2, help="每个 URL 最大尝试次数。"
    )
    parser.add_argument(
        "--all-content", action="store_true", help="关闭 only_main_content。"
    )
    parser.add_argument(
        "--no-screenshot", action="store_true", help="不请求 full-page screenshot。"
    )
    parser.add_argument(
        "--no-pdf", action="store_true", help="不把截图转换为 page.pdf。"
    )
    parser.add_argument("--no-png", action="store_true", help="不保存 page.png。")
    parser.add_argument(
        "--include-html", action="store_true", help="额外保存 page.html。"
    )
    parser.add_argument(
        "--include-images", action="store_true", help="请求页面图片 URL 列表。"
    )
    parser.add_argument(
        "--proxy",
        choices=["basic", "stealth", "enhanced", "auto"],
        help="Firecrawl 云端 proxy 档位。",
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help="只测试 Firecrawl API 网络连通性，不消耗抓取额度。",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="只打印计划产物，不抓取、不写文件。"
    )
    return parser.parse_args(argv)


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
    values = load_env_file(REPO_ROOT / "configs" / ".env")
    return values.get("FIRECRAWL_API_KEY") or values.get("firecrawl_api_key")


def options_from_args(args: argparse.Namespace) -> FirecrawlOptions:
    if args.timeout_ms <= 0:
        raise ValueError("--timeout-ms must be greater than zero.")
    if args.retries <= 0:
        raise ValueError("--retries must be greater than zero.")

    request_screenshot = not args.no_screenshot and (not args.no_pdf or not args.no_png)
    formats: list[Any] = ["markdown", "links"]
    if args.include_html:
        formats.append("html")
    if args.include_images:
        formats.append("images")
    if request_screenshot:
        # Firecrawl v2 uses camelCase inside the screenshot format object.
        formats.append({"type": "screenshot", "fullPage": True, "quality": 85})

    return FirecrawlOptions(
        out_dir=args.out_dir.expanduser().resolve(),
        api_key=resolve_api_key(args.api_key),
        api_url=args.api_url.rstrip("/"),
        http_proxy=None if args.no_http_proxy else args.http_proxy,
        timeout_ms=args.timeout_ms,
        retries=args.retries,
        only_main_content=not args.all_content,
        full_page_screenshot=request_screenshot,
        write_pdf=not args.no_pdf and request_screenshot,
        save_png=not args.no_png and request_screenshot,
        include_html=args.include_html,
        include_images=args.include_images,
        proxy_tier=args.proxy,
        formats=formats,
    )


def proxy_opener(http_proxy: str | None) -> urllib.request.OpenerDirector | None:
    if not http_proxy:
        return None
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": http_proxy, "https": http_proxy})
    )


def probe_api(api_url: str, *, http_proxy: str | None) -> int | None:
    url = api_url.rstrip("/") + "/"
    request = urllib.request.Request(url, method="GET")
    opener = proxy_opener(http_proxy)
    try:
        open_url = opener.open if opener else urllib.request.urlopen
        with open_url(request, timeout=20) as response:
            print(f"Firecrawl API reachable: HTTP {response.status} {url}")
            return response.status
    except urllib.error.HTTPError as exc:
        print(f"Firecrawl API reachable: HTTP {exc.code} {url}")
        return exc.code
    except Exception as exc:
        print(f"Firecrawl API probe failed: {type(exc).__name__}: {exc}")
        return None


def _set_proxy_environment(http_proxy: str | None) -> dict[str, str | None]:
    previous: dict[str, str | None] = {}
    if not http_proxy:
        return previous
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        previous[name] = os.environ.get(name)
        os.environ[name] = http_proxy
    return previous


def _restore_proxy_environment(previous: dict[str, str | None]) -> None:
    for name, value in previous.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


def scrape(url: str, options: FirecrawlOptions) -> Any:
    if not options.api_key and options.api_url == DEFAULT_API_URL:
        raise RuntimeError(
            "FIRECRAWL_API_KEY is not set. Add it to configs/.env or pass --api-key."
        )
    try:
        from firecrawl import Firecrawl
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "Missing package: firecrawl-py. Install the `webpage-firecrawl` extra."
        ) from exc

    previous = _set_proxy_environment(options.http_proxy)
    try:
        client = Firecrawl(api_key=options.api_key, api_url=options.api_url)
        kwargs: dict[str, Any] = {
            "formats": options.formats,
            "only_main_content": options.only_main_content,
            "timeout": options.timeout_ms,
            "block_ads": True,
            "integration": "pipelines-rag-parsers",
        }
        if options.proxy_tier:
            kwargs["proxy"] = options.proxy_tier
        return client.scrape(url, **kwargs)
    finally:
        _restore_proxy_environment(previous)


def doc_field(doc: Any, name: str, default: Any = None) -> Any:
    return (
        doc.get(name, default) if isinstance(doc, dict) else getattr(doc, name, default)
    )


def to_snapshot(doc: Any, source_url: str):
    markdown = doc_field(doc, "markdown", "") or ""
    metadata = as_jsonable(doc_field(doc, "metadata", {}) or {})
    links = doc_field(doc, "links", []) or []
    images = doc_field(doc, "images", []) or []
    final_url = metadata.get("url") or metadata.get("source_url") or source_url
    return make_snapshot(
        provider="firecrawl",
        source_url=source_url,
        final_url=final_url,
        markdown=markdown,
        title=metadata.get("title"),
        description=metadata.get("description") or metadata.get("og_description"),
        status_code=metadata.get("status_code"),
        links=links,
        images=images,
        metadata=metadata,
    )


def download_screenshot(value: str | None, *, http_proxy: str | None) -> bytes | None:
    if not value:
        return None
    if value.startswith("data:") or not value.startswith(("http://", "https://")):
        return decode_base64_bytes(value)
    request = urllib.request.Request(value, headers={"User-Agent": "pipelines-rag/0.4"})
    opener = proxy_opener(http_proxy)
    open_url = opener.open if opener else urllib.request.urlopen
    with open_url(request, timeout=60) as response:
        return response.read()


def save_document(
    doc: Any, source_url: str, options: FirecrawlOptions, *, url_count: int
) -> Path:
    snapshot = to_snapshot(doc, source_url)
    out_dir = output_dir_for_url(options.out_dir, source_url, url_count=url_count)
    screenshot = doc_field(doc, "screenshot")
    image_bytes = None
    if options.full_page_screenshot and screenshot:
        image_bytes = download_screenshot(screenshot, http_proxy=options.http_proxy)

    png_bytes = image_to_png(image_bytes) if image_bytes and options.save_png else None
    pdf_bytes = None
    page_count = 0
    if image_bytes and options.write_pdf:
        pdf_bytes, page_count = image_to_a4_pdf(image_bytes)

    raw = as_jsonable(doc)
    if isinstance(raw, dict):
        omitted: dict[str, Any] = {}
        for field in ("markdown", "html", "screenshot"):
            value = raw.pop(field, None)
            if value is not None:
                stored_separately = (
                    field == "markdown"
                    or (field == "html" and options.include_html)
                    or (field == "screenshot" and bool(png_bytes or pdf_bytes))
                )
                omitted[field] = {
                    "stored_separately": stored_separately,
                    "length": len(value) if isinstance(value, str) else None,
                }
        if omitted:
            raw["_omitted_artifacts"] = omitted

    written = write_page_bundle(
        out_dir,
        snapshot,
        raw=raw,
        html=doc_field(doc, "html") if options.include_html else None,
        png_bytes=png_bytes,
        pdf_bytes=pdf_bytes,
    )
    print(f"  已写出 -> {out_dir}")
    message = "    " + " / ".join(path.name for path in written)
    if page_count:
        message += f"（PDF {page_count} 页）"
    elif options.write_pdf:
        message += "（无可用截图，未生成 PDF）"
    print(message)
    return out_dir


def scrape_with_retries(url: str, options: FirecrawlOptions) -> Any:
    last_error: Exception | None = None
    for attempt in range(1, options.retries + 1):
        try:
            return scrape(url, options)
        except Exception as exc:
            last_error = exc
            if attempt < options.retries:
                print(
                    f"  第 {attempt}/{options.retries} 次失败：{type(exc).__name__}: {exc}"
                )
                time.sleep(min(2 * attempt, 5))
    assert last_error is not None
    raise last_error


def run(urls: list[str], options: FirecrawlOptions) -> int:
    failures = 0
    for index, url in enumerate(urls, 1):
        print(f"[{index}/{len(urls)}] {url}")
        try:
            doc = scrape_with_retries(url, options)
            snapshot = to_snapshot(doc, url)
            print(
                f"  状态码={snapshot.status_code} | 标题={snapshot.title!r} | "
                f"正文={snapshot.word_count}词"
            )
            save_document(doc, url, options, url_count=len(urls))
        except Exception as exc:  # API/network failures are isolated per URL
            failures += 1
            print(f"  抓取失败：{type(exc).__name__}: {exc}")
    print(f"完成：{len(urls) - failures}/{len(urls)} 成功。")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        options = options_from_args(args)
    except ValueError as exc:
        print(f"Error: {exc}")
        return 2

    if args.probe:
        status = probe_api(options.api_url, http_proxy=options.http_proxy)
        return 0 if status is not None else 1

    try:
        urls = validate_urls(args.urls)
    except ValueError as exc:
        print(f"Error: {exc}")
        return 2

    if args.dry_run:
        optional = []
        if options.save_png:
            optional.append("page.png")
        if options.write_pdf:
            optional.append("page.pdf")
        if options.include_html:
            optional.append("page.html")
        for path in describe_planned_outputs(
            options.out_dir, urls, optional_names=optional
        ):
            print(path)
        return 0
    if not options.api_key and options.api_url == DEFAULT_API_URL:
        print(
            "Error: FIRECRAWL_API_KEY is not set. Add it to configs/.env or pass --api-key."
        )
        return 2
    return run(urls, options)


if __name__ == "__main__":
    raise SystemExit(main())
