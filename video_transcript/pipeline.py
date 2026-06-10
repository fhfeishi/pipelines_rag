from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from configs.config import settings
from video_transcript.cache import cache_key, load_cache, save_cache
from video_transcript.media import MediaError, PrepareResult, prepare_audio, probe_duration_ms, require_ffmpeg
from video_transcript.naming import OutputNames
from video_transcript.output import write_outputs, write_summary
from video_transcript.polish import PolishError, PolishResult, format_article, polish_cache_key, polish_text
from video_transcript.transcribe.base import TranscriptResult, TranscriptSegment
from video_transcript.transcribe.dashscope_asr import DashscopeAsrError, transcribe_file


@dataclass
class RunResult:
    input_path: str
    out_dir: str
    output_stem: str
    title: str
    deliverable_path: str
    deliverable_text: str
    text_chars: int
    asr_cached: bool = False
    polish_cached: bool = False
    outputs: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0
    media_kind: str = ""


def prepare_only(
    input_path: str | Path,
    *,
    out_dir: str | Path | None = None,
    max_seconds: float | None = None,
) -> PrepareResult:
    """Probe and convert media only (no API calls). Useful for format testing."""
    input_path = Path(input_path).resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"Input not found: {input_path}")
    out_dir = Path(out_dir).resolve() if out_dir else Path("tmp/outs") / input_path.stem
    names = OutputNames.from_input(input_path, out_dir)
    require_ffmpeg()
    return prepare_audio(input_path, names.work_dir(), max_seconds=max_seconds)


def _asr_from_cache(payload: dict) -> TranscriptResult:
    segments = [
        TranscriptSegment(
            start_ms=int(seg["start_ms"]),
            end_ms=int(seg["end_ms"]),
            text=str(seg["text"]),
        )
        for seg in payload.get("segments", [])
    ]
    return TranscriptResult(
        text=str(payload.get("text", "")),
        segments=segments,
        provider=str(payload.get("provider", "")),
        model=str(payload.get("model", "")),
        language=payload.get("language"),
        duration_ms=payload.get("duration_ms"),
        request_ids=list(payload.get("request_ids", [])),
        chunk_count=int(payload.get("chunk_count", 1)),
        api_calls=int(payload.get("api_calls", 0)),
    )


def _polish_from_cache(payload: dict) -> PolishResult:
    return PolishResult(
        title=str(payload.get("title", "")),
        text=str(payload.get("text", "")),
        model=str(payload.get("model", "")),
        request_id=payload.get("request_id"),
        chunk_count=int(payload.get("chunk_count", 1)),
        api_calls=int(payload.get("api_calls", 0)),
        raw_chars=int(payload.get("raw_chars", 0)),
        polished_chars=int(payload.get("polished_chars", 0)),
    )


def run(
    input_path: str | Path,
    *,
    out_dir: str | Path | None = None,
    language: str | None = None,
    formats: list[str] | None = None,
    polish: bool = True,
    title: bool = True,
    keep_audio: bool = False,
    resume: bool = True,
    force: bool = False,
    provider: str = "dashscope",
    polish_model: str | None = None,
    max_seconds: float | None = None,
) -> RunResult:
    """Run the full transcript pipeline on one media file."""
    input_path = Path(input_path).resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"Input not found: {input_path}")

    formats = formats or ["txt", "json"]
    out_dir = Path(out_dir).resolve() if out_dir else Path("tmp/outs") / input_path.stem
    out_dir.mkdir(parents=True, exist_ok=True)

    names = OutputNames.from_input(input_path, out_dir)
    require_ffmpeg()
    work_dir = names.work_dir()
    prep = prepare_audio(input_path, work_dir, max_seconds=max_seconds)
    audio_path = prep.asr_path
    source_kind = prep.info.kind

    asr_key = cache_key(
        input_path, provider=provider, language=language, max_seconds=max_seconds
    )
    asr_cache_file = names.cache_file(f"asr_{asr_key}")
    asr_cached = False
    polish_cached = False
    started = time.perf_counter()
    polish_model = polish_model or settings.polish_model

    if resume and not force and (payload := load_cache(asr_cache_file)):
        logger.info("ASR cache hit: {}", input_path.name)
        asr_result = _asr_from_cache(payload)
        asr_cached = True
    else:
        if provider != "dashscope":
            raise ValueError(f"Unsupported provider: {provider}")
        logger.info("ASR: {}", input_path.name)
        asr_result = transcribe_file(audio_path, language=language, work_dir=work_dir)
        save_cache(
            asr_cache_file,
            {
                "stage": "asr",
                "source": str(input_path),
                "source_filename": input_path.name,
                "output_stem": names.stem,
                "text": asr_result.text,
                "segments": [
                    {"start_ms": s.start_ms, "end_ms": s.end_ms, "text": s.text}
                    for s in asr_result.segments
                ],
                "provider": asr_result.provider,
                "model": asr_result.model,
                "language": asr_result.language,
                "duration_ms": asr_result.duration_ms,
                "request_ids": asr_result.request_ids,
                "chunk_count": asr_result.chunk_count,
                "api_calls": asr_result.api_calls,
            },
        )

    polish_result: PolishResult | None = None
    polished_text: str | None = None
    article_title = ""

    if polish and asr_result.text.strip():
        polish_key = polish_cache_key(
            asr_result.text,
            model=polish_model,
            language=language,
            with_title=title,
        )
        polish_cache_file = names.cache_file(polish_key)
        if resume and not force and (payload := load_cache(polish_cache_file)):
            logger.info("Polish cache hit: {}", input_path.name)
            polish_result = _polish_from_cache(payload)
            polished_text = polish_result.text
            article_title = polish_result.title
            polish_cached = True
        else:
            polish_result = polish_text(
                asr_result.text,
                language=language,
                model=polish_model,
                with_title=title,
            )
            polished_text = polish_result.text
            article_title = polish_result.title
            save_cache(
                polish_cache_file,
                {
                    "stage": "polish",
                    "title": polish_result.title,
                    "text": polish_result.text,
                    "model": polish_result.model,
                    "request_id": polish_result.request_id,
                    "chunk_count": polish_result.chunk_count,
                    "api_calls": polish_result.api_calls,
                    "raw_chars": polish_result.raw_chars,
                    "polished_chars": polish_result.polished_chars,
                },
            )
    elif polish:
        polished_text = ""
        article_title = ""

    if keep_audio:
        extracted_out = names.extracted_audio(prep.extracted_path.suffix)
        shutil.copy2(prep.extracted_path, extracted_out)
        shutil.copy2(prep.asr_path, names.audio_wav())

    body = polished_text if polish and polished_text is not None else asr_result.text
    deliverable = format_article(article_title, body) if polish and title else (body or "")

    outputs = write_outputs(
        names,
        source=str(input_path),
        source_filename=input_path.name,
        asr=asr_result,
        polished_text=polished_text,
        polish=polish_result,
        polish_enabled=polish,
        title=article_title if polish and title else "",
        deliverable=deliverable,
        formats=formats,
    )

    elapsed = time.perf_counter() - started
    if asr_result.duration_ms is None:
        try:
            asr_result.duration_ms = probe_duration_ms(audio_path)
        except MediaError:
            pass

    summary_path = names.summary_json()
    write_summary(
        summary_path,
        source=str(input_path),
        source_filename=input_path.name,
        output_stem=names.stem,
        source_kind=source_kind,
        asr=asr_result,
        polish=polish_result,
        polish_enabled=polish,
        title=article_title if polish and title else "",
        deliverable_file=str(names.transcript_txt().name),
        elapsed_s=elapsed,
        asr_cached=asr_cached,
        polish_cached=polish_cached,
        media=prep,
        output_files=outputs + [str(summary_path)],
    )

    deliverable_path = str(names.transcript_txt())
    return RunResult(
        input_path=str(input_path),
        out_dir=str(out_dir),
        output_stem=names.stem,
        title=article_title,
        deliverable_path=deliverable_path,
        deliverable_text=deliverable,
        text_chars=len(deliverable),
        asr_cached=asr_cached,
        polish_cached=polish_cached,
        outputs=outputs + [str(summary_path)],
        elapsed_s=elapsed,
        media_kind=source_kind,
    )
