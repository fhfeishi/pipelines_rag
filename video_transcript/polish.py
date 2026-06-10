from __future__ import annotations

import hashlib
from dataclasses import dataclass

from loguru import logger

from configs.config import settings
from video_transcript.llm import LlmError, chat_json

PROVIDER = "deepseek"
CACHE_VERSION = "v2"
MAX_CHUNK_CHARS = 3_500


class PolishError(LlmError):
    pass


@dataclass
class PolishResult:
    title: str
    text: str
    provider: str = PROVIDER
    model: str = ""
    request_id: str | None = None
    chunk_count: int = 1
    api_calls: int = 0
    raw_chars: int = 0
    polished_chars: int = 0


def polish_cache_key(raw_text: str, *, model: str, language: str | None, with_title: bool) -> str:
    digest = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()[:16]
    lang = language or "auto"
    title_flag = "title" if with_title else "notitle"
    return f"polish_{CACHE_VERSION}_{model}_{lang}_{title_flag}_{digest}"


def _lang_hint(language: str | None) -> str:
    if language == "zh":
        return "标题与正文均使用简体中文。"
    if language == "en":
        return "Title and body in English."
    return "标题语言与正文保持一致。"


def _polish_prompt(*, language: str | None) -> str:
    return f"""
你是语音转写后的文字编辑。请把 ASR 原始转写整理成可直接发布的文章。

要求：
1. 修正同音错字、术语误识别、明显口误；不确定处保留原意，不要臆造事实。
2. 补全标点，按语义分段（正文段落之间用空行分隔）。
3. 去掉无意义的口头禅和重复（如「嗯」「啊」「那个」），保留有信息量的表达。
4. 不要添加原文没有的新观点、新例子或总结性发挥。
5. 保持技术名词、产品名、命令、URL、英文缩写准确。
6. 生成简洁标题（8-28字），准确概括主题，拒绝夸张党标题。
7. 严格输出 JSON，格式：{{"title": "...", "body": "..."}}
   - body 为整理后正文，不要包含标题行，不要使用 markdown 标题符号。

{_lang_hint(language)}
""".strip()


def _title_prompt(*, language: str | None) -> str:
    return f"""
根据下面正文生成一个简洁标题（8-28字），准确概括主题，拒绝夸张党标题。
严格输出 JSON：{{"title": "..."}}

{_lang_hint(language)}
""".strip()


def _split_chunks(text: str, *, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    text = text.strip()
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            for sep in ("\n\n", "。", "！", "？", ". ", "! ", "? "):
                pos = text.rfind(sep, start, end)
                if pos > start + max_chars // 2:
                    end = pos + len(sep)
                    break
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end
    return chunks


def _polish_chunk(
    raw_text: str,
    *,
    model: str,
    language: str | None,
) -> tuple[str, str, str | None]:
    payload, request_id = chat_json(
        system=_polish_prompt(language=language),
        user=raw_text,
        model=model,
        base_url=settings.polish_base_url,
    )
    title = str(payload.get("title", "")).strip()
    body = str(payload.get("body", "")).strip()
    if not body:
        raise PolishError(f"Polish JSON missing body: {payload}")
    return title, body, request_id


def _generate_title(body: str, *, model: str, language: str | None) -> tuple[str, str | None]:
    payload, request_id = chat_json(
        system=_title_prompt(language=language),
        user=body[:4000],
        model=model,
        base_url=settings.polish_base_url,
        temperature=0.1,
    )
    title = str(payload.get("title", "")).strip()
    if not title:
        raise PolishError(f"Title JSON missing title: {payload}")
    return title, request_id


def format_article(title: str, body: str) -> str:
    title = title.strip()
    body = body.strip()
    if title and body:
        return f"{title}\n\n{body}\n"
    if body:
        return f"{body}\n"
    return f"{title}\n" if title else "\n"


def polish_text(
    raw_text: str,
    *,
    language: str | None = None,
    model: str | None = None,
    with_title: bool = True,
) -> PolishResult:
    raw_text = raw_text.strip()
    model = model or settings.polish_model
    if not raw_text:
        return PolishResult(title="", text="", model=model, raw_chars=0, polished_chars=0)

    chunks = _split_chunks(raw_text)
    logger.info("Polishing {} chars in {} chunk(s) via {}", len(raw_text), len(chunks), model)

    polished_parts: list[str] = []
    request_ids: list[str] = []
    api_calls = 0
    title = ""

    for idx, chunk in enumerate(chunks, start=1):
        logger.info("Polish chunk {}/{}", idx, len(chunks))
        chunk_title, body, request_id = _polish_chunk(chunk, model=model, language=language)
        api_calls += 1
        if request_id:
            request_ids.append(request_id)
        if idx == 1 and chunk_title:
            title = chunk_title
        polished_parts.append(body)

    merged_body = "\n\n".join(polished_parts).strip()

    if with_title and not title and merged_body:
        logger.info("Generating title from polished body")
        title, request_id = _generate_title(merged_body, model=model, language=language)
        api_calls += 1
        if request_id:
            request_ids.append(request_id)
    elif not with_title:
        title = ""

    return PolishResult(
        title=title,
        text=merged_body,
        model=model,
        request_id=request_ids[-1] if request_ids else None,
        chunk_count=len(chunks),
        api_calls=api_calls,
        raw_chars=len(raw_text),
        polished_chars=len(merged_body),
    )
