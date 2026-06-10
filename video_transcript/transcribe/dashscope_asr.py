from __future__ import annotations

import json
from http import HTTPStatus
from pathlib import Path
from typing import Any

import dashscope
from dashscope import MultiModalConversation
from loguru import logger

from configs.config import settings
from video_transcript.media import probe_duration_ms, split_audio
from video_transcript.transcribe.base import TranscriptResult, TranscriptSegment

# qwen3-asr-flash supports local files up to ~5 minutes per request.
MAX_CHUNK_SECONDS = 280
PROVIDER = "dashscope"
MODEL = "qwen3-asr-flash"


class DashscopeAsrError(RuntimeError):
    pass


def _api_key(explicit: str | None = None) -> str:
    key = explicit or settings.dashscope_api_key
    if not key:
        raise DashscopeAsrError(
            "DashScope API key missing. Set DASHSCOPE_API_KEY or configs/.env dashscope_api_key."
        )
    return key


def _file_uri(path: Path) -> str:
    return path.resolve().as_uri()


def _response_request_id(response: Any) -> str | None:
    for attr in ("request_id", "requestId"):
        value = getattr(response, attr, None)
        if value:
            return str(value)
    if hasattr(response, "output") and isinstance(response.output, dict):
        value = response.output.get("request_id")
        if value:
            return str(value)
    return None


def _raise_for_status(response: Any) -> None:
    status = getattr(response, "status_code", None)
    if status is None or status == HTTPStatus.OK:
        return
    code = getattr(response, "code", None)
    message = getattr(response, "message", None) or response
    request_id = _response_request_id(response)
    detail = f"code={code}, message={message}"
    if request_id:
        detail = f"{detail}, request_id={request_id}"
    raise DashscopeAsrError(f"DashScope ASR failed (HTTP {status}): {detail}")


def _extract_text(response: Any) -> str:
    output = getattr(response, "output", None)
    if output is None:
        raise DashscopeAsrError(f"Unexpected DashScope response: {response}")

    choices = output.get("choices") if isinstance(output, dict) else getattr(output, "choices", None)
    if choices:
        message = choices[0].get("message", {})
        content = message.get("content", "")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("text"):
                        parts.append(str(item["text"]))
                    if item.get("transcript"):
                        parts.append(str(item["transcript"]))
            return "\n".join(parts).strip()

    for key in ("text", "transcription", "result"):
        if isinstance(output, dict) and output.get(key):
            return str(output[key]).strip()

    # Empty audio or silence may return an empty assistant message.
    if isinstance(output, dict) and output.get("choices"):
        return ""

    raise DashscopeAsrError(
        f"Could not parse transcription from response: {json.dumps(output, ensure_ascii=False)[:500]}"
    )


def _call_once(
    audio_path: Path,
    *,
    api_key: str,
    language: str | None,
    enable_itn: bool,
) -> tuple[str, str | None]:
    messages = [
        {
            "role": "user",
            "content": [{"audio": _file_uri(audio_path)}],
        }
    ]
    asr_options: dict[str, Any] = {"enable_itn": enable_itn}
    if language and language != "auto":
        asr_options["language"] = language

    response = MultiModalConversation.call(
        api_key=api_key,
        model=MODEL,
        messages=messages,
        result_format="message",
        asr_options=asr_options,
    )
    _raise_for_status(response)
    return _extract_text(response), _response_request_id(response)


def transcribe_file(
    audio_path: Path,
    *,
    language: str | None = None,
    api_key: str | None = None,
    enable_itn: bool = True,
    work_dir: Path | None = None,
) -> TranscriptResult:
    audio_path = audio_path.resolve()
    if not audio_path.exists():
        raise DashscopeAsrError(f"Audio file not found: {audio_path}")

    key = _api_key(api_key)
    dashscope.api_key = key

    duration_ms = probe_duration_ms(audio_path)
    request_ids: list[str] = []
    api_calls = 0

    if duration_ms <= MAX_CHUNK_SECONDS * 1000:
        text, request_id = _call_once(audio_path, api_key=key, language=language, enable_itn=enable_itn)
        api_calls = 1
        if request_id:
            request_ids.append(request_id)
        return TranscriptResult(
            text=text,
            segments=[TranscriptSegment(0, duration_ms, text)] if text else [],
            provider=PROVIDER,
            model=MODEL,
            language=language,
            duration_ms=duration_ms,
            request_ids=request_ids,
            chunk_count=1,
            api_calls=api_calls,
        )

    chunk_dir = (work_dir or audio_path.parent) / "chunks"
    chunks = split_audio(audio_path, chunk_dir, chunk_seconds=MAX_CHUNK_SECONDS)
    logger.info("Long audio ({} ms): split into {} chunks", duration_ms, len(chunks))

    texts: list[str] = []
    segments: list[TranscriptSegment] = []
    offset_ms = 0

    for idx, chunk in enumerate(chunks, start=1):
        chunk_ms = probe_duration_ms(chunk)
        logger.info("Transcribing chunk {}/{} ({} ms)", idx, len(chunks), chunk_ms)
        text, request_id = _call_once(chunk, api_key=key, language=language, enable_itn=enable_itn)
        api_calls += 1
        if request_id:
            request_ids.append(request_id)
        if text:
            texts.append(text)
            segments.append(TranscriptSegment(offset_ms, offset_ms + chunk_ms, text))
        offset_ms += chunk_ms

    merged = "\n\n".join(texts).strip()
    return TranscriptResult(
        text=merged,
        segments=segments,
        provider=PROVIDER,
        model=MODEL,
        language=language,
        duration_ms=duration_ms,
        request_ids=request_ids,
        chunk_count=len(chunks),
        api_calls=api_calls,
    )
