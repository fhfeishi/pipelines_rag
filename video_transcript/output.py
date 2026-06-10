from __future__ import annotations

import json
from pathlib import Path

from video_transcript.media import PrepareResult
from video_transcript.naming import OutputNames
from video_transcript.polish import PolishResult
from video_transcript.transcribe.base import TranscriptResult


def _format_srt_time(ms: int) -> str:
    hours, rem = divmod(ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def write_text_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = text.strip()
    path.write_text((content + "\n") if content else "\n", encoding="utf-8")


def write_transcript_json(
    path: Path,
    *,
    source: str,
    source_filename: str,
    output_stem: str,
    asr: TranscriptResult,
    polished_text: str | None,
    polish: PolishResult | None,
    polish_enabled: bool,
    title: str,
    deliverable: str,
) -> None:
    payload = {
        "source": source,
        "source_filename": source_filename,
        "output_stem": output_stem,
        "title": title or None,
        "deliverable": deliverable,
        "asr": {
            "provider": asr.provider,
            "model": asr.model,
            "language": asr.language,
            "duration_ms": asr.duration_ms,
            "text_chars": len(asr.text),
            "segment_count": len(asr.segments),
            "chunk_count": asr.chunk_count,
            "api_calls": asr.api_calls,
            "request_ids": asr.request_ids,
            "text": asr.text,
            "segments": [
                {"start_ms": seg.start_ms, "end_ms": seg.end_ms, "text": seg.text}
                for seg in asr.segments
            ],
        },
        "polish": {
            "enabled": polish_enabled,
            "provider": polish.provider if polish else None,
            "model": polish.model if polish else None,
            "request_id": polish.request_id if polish else None,
            "chunk_count": polish.chunk_count if polish else 0,
            "api_calls": polish.api_calls if polish else 0,
            "raw_chars": polish.raw_chars if polish else len(asr.text),
            "polished_chars": polish.polished_chars if polish else len(polished_text or ""),
            "title": title or None,
            "text": polished_text,
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_transcript_srt(path: Path, result: TranscriptResult, *, title: str = "") -> None:
    lines: list[str] = []
    if title:
        lines.extend(["1", "00:00:000,000 --> 00:00:003,000", title, ""])
    for idx, seg in enumerate(result.segments, start=2 if title else 1):
        lines.append(str(idx))
        lines.append(f"{_format_srt_time(seg.start_ms)} --> {_format_srt_time(seg.end_ms)}")
        lines.append(seg.text.strip())
        lines.append("")
    if not result.segments and result.text.strip():
        end_ms = result.duration_ms or 10_000
        lines = [
            "1",
            f"00:00:000,000 --> {_format_srt_time(end_ms)}",
            result.text.strip(),
            "",
        ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def write_summary(
    path: Path,
    *,
    source: str,
    source_filename: str,
    output_stem: str,
    source_kind: str,
    asr: TranscriptResult,
    polish: PolishResult | None,
    polish_enabled: bool,
    title: str,
    deliverable_file: str,
    elapsed_s: float,
    asr_cached: bool,
    polish_cached: bool,
    media: PrepareResult | None,
    output_files: list[str],
) -> None:
    payload = {
        "source": source,
        "source_filename": source_filename,
        "output_stem": output_stem,
        "title": title or None,
        "source_kind": source_kind,
        "elapsed_s": round(elapsed_s, 2),
        "media": {
            "probe": media.info.to_dict() if media else None,
            "extract_mode": media.extract_mode if media else None,
            "asr_mode": media.asr_mode if media else None,
            "steps": media.steps if media else [],
            "extracted_audio": str(media.extracted_path) if media else None,
            "asr_audio": str(media.asr_path) if media else None,
        },
        "asr": {
            "provider": asr.provider,
            "model": asr.model,
            "duration_ms": asr.duration_ms,
            "cached": asr_cached,
            "text_chars": len(asr.text),
            "segment_count": len(asr.segments),
            "chunk_count": asr.chunk_count,
            "api_calls": asr.api_calls,
            "request_ids": asr.request_ids,
        },
        "polish": {
            "enabled": polish_enabled,
            "cached": polish_cached,
            "provider": polish.provider if polish else None,
            "model": polish.model if polish else None,
            "request_id": polish.request_id if polish else None,
            "chunk_count": polish.chunk_count if polish else 0,
            "api_calls": polish.api_calls if polish else 0,
            "raw_chars": polish.raw_chars if polish else len(asr.text),
            "polished_chars": polish.polished_chars if polish else len(asr.text),
            "title": title or None,
        },
        "deliverable_file": deliverable_file,
        "outputs": output_files,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_outputs(
    names: OutputNames,
    *,
    source: str,
    source_filename: str,
    asr: TranscriptResult,
    polished_text: str | None,
    polish: PolishResult | None,
    polish_enabled: bool,
    title: str,
    deliverable: str,
    formats: list[str],
) -> list[str]:
    written: list[str] = []

    if "txt" in formats:
        if polish_enabled and asr.text.strip():
            write_text_file(names.raw_transcript_txt(), asr.text)
            written.append(str(names.raw_transcript_txt()))
        write_text_file(names.transcript_txt(), deliverable)
        written.append(str(names.transcript_txt()))
        if title:
            write_text_file(names.title_txt(), title)
            written.append(str(names.title_txt()))

    if "json" in formats:
        path = names.transcript_json()
        write_transcript_json(
            path,
            source=source,
            source_filename=source_filename,
            output_stem=names.stem,
            asr=asr,
            polished_text=polished_text,
            polish=polish,
            polish_enabled=polish_enabled,
            title=title,
            deliverable=deliverable,
        )
        written.append(str(path))

    if "srt" in formats:
        path = names.transcript_srt()
        write_transcript_srt(path, asr, title=title)
        written.append(str(path))

    return written
