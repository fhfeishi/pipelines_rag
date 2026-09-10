"""Small, dependency-light index for the public LangChain documentation."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable

import httpx

logger = logging.getLogger(__name__)

LANGCHAIN_DOCS_URLS = (
    "https://docs.langchain.com/oss/python/langchain/overview",
    "https://docs.langchain.com/oss/python/langchain/quickstart",
    "https://docs.langchain.com/oss/python/langchain/rag",
    "https://docs.langchain.com/oss/python/langchain/retrieval",
    "https://docs.langchain.com/oss/python/langchain/agents",
    "https://docs.langchain.com/oss/python/langchain/streaming",
    "https://docs.langchain.com/oss/python/langgraph/overview",
    "https://docs.langchain.com/langsmith/home",
)

_TOKEN_PATTERN = re.compile(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]")


@dataclass(slots=True)
class DocRecord:
    """Cached page content used for lexical retrieval."""

    title: str
    url: str
    text: str


@dataclass(slots=True)
class SearchResult:
    """A relevant document excerpt returned to the chat layer."""

    title: str
    url: str
    snippet: str
    score: float


class _PageParser(HTMLParser):
    """Extract readable text and the page title without a heavy HTML dependency."""

    _ignored_tags = {"script", "style", "noscript", "svg", "nav", "footer"}

    def __init__(self) -> None:
        super().__init__()
        self._ignored_depth = 0
        self._title_depth = 0
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._ignored_tags:
            self._ignored_depth += 1
        if tag == "title":
            self._title_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self._ignored_tags and self._ignored_depth:
            self._ignored_depth -= 1
        if tag == "title" and self._title_depth:
            self._title_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._title_depth:
            self.title_parts.append(data)
        if not self._ignored_depth:
            self.text_parts.append(data)


def _parse_page(html: str) -> tuple[str, str]:
    parser = _PageParser()
    parser.feed(html)
    title = " ".join("".join(parser.title_parts).split()) or "LangChain documentation"
    text = " ".join(" ".join(parser.text_parts).split())
    return title, text


def _tokens(value: str) -> list[str]:
    return [token.lower() for token in _TOKEN_PATTERN.findall(value)]


class DocsIndex:
    """A local cache plus lexical search over official docs pages."""

    def __init__(
        self,
        cache_path: Path,
        *,
        urls: Iterable[str] = LANGCHAIN_DOCS_URLS,
        timeout: float = 20.0,
        max_pages: int = 8,
    ) -> None:
        self.cache_path = Path(cache_path)
        self.urls = tuple(urls)[:max_pages]
        self.timeout = timeout
        self.records: list[DocRecord] = []
        self.last_error: str | None = None
        self.load_cache()

    @property
    def ready(self) -> bool:
        return bool(self.records)

    def load_cache(self) -> None:
        if not self.cache_path.is_file():
            return
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
            self.records = [DocRecord(**item) for item in payload]
        except (OSError, TypeError, ValueError) as exc:
            self.last_error = f"读取文档缓存失败：{exc}"
            logger.warning(self.last_error)

    def refresh(self) -> int:
        """Fetch configured pages and atomically replace the cache."""

        records: list[DocRecord] = []
        failures: list[str] = []
        headers = {"User-Agent": "static1-personal-chat-langchain/0.1"}
        with httpx.Client(timeout=self.timeout, follow_redirects=True, headers=headers) as client:
            for url in self.urls:
                try:
                    response = client.get(url)
                    response.raise_for_status()
                    title, text = _parse_page(response.text)
                    if text:
                        records.append(DocRecord(title=title, url=url, text=text))
                except httpx.HTTPError as exc:
                    failures.append(f"{url}: {exc}")

        if records:
            self.records = records
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = self.cache_path.with_suffix(".tmp")
            temporary_path.write_text(
                json.dumps([asdict(record) for record in records], ensure_ascii=False),
                encoding="utf-8",
            )
            temporary_path.replace(self.cache_path)
            self.last_error = "；".join(failures) if failures else None
        elif failures:
            self.last_error = "；".join(failures)
            raise RuntimeError("无法获取 LangChain 文档，请检查网络连接。")
        return len(self.records)

    def ensure_ready(self) -> None:
        if not self.ready:
            self.refresh()

    def search(self, query: str, limit: int = 4) -> list[SearchResult]:
        query_tokens = _tokens(query)
        if not query_tokens:
            return []

        results: list[SearchResult] = []
        for record in self.records:
            page_tokens = _tokens(record.title + " " + record.text)
            token_counts = {token: page_tokens.count(token) for token in set(query_tokens)}
            score = sum(min(token_counts[token], 5) for token in query_tokens)
            title_tokens = set(_tokens(record.title))
            score += sum(2 for token in query_tokens if token in title_tokens)
            if score <= 0:
                continue
            lowered_text = record.text.lower()
            first_match = min(
                (lowered_text.find(token) for token in query_tokens if token in lowered_text),
                default=0,
            )
            start = max(0, first_match - 180)
            snippet = record.text[start : start + 720].strip()
            results.append(
                SearchResult(
                    title=record.title,
                    url=record.url,
                    snippet=snippet,
                    score=float(score),
                )
            )
        return sorted(results, key=lambda item: item.score, reverse=True)[:limit]


def build_context(results: list[SearchResult]) -> str:
    """Format retrieved pages for the model and preserve source links."""

    if not results:
        return "没有检索到相关的 LangChain 官方文档片段。"
    sections = []
    for index, result in enumerate(results, start=1):
        sections.append(
            f"[文档 {index}] {result.title}\nURL: {result.url}\n内容：{result.snippet}"
        )
    return "\n\n".join(sections)


__all__ = [
    "LANGCHAIN_DOCS_URLS",
    "DocRecord",
    "DocsIndex",
    "SearchResult",
    "build_context",
]
