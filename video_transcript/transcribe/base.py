from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class TranscriptSegment:
    start_ms: int
    end_ms: int
    text: str


@dataclass
class TranscriptResult:
    text: str
    segments: list[TranscriptSegment] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    language: str | None = None
    duration_ms: int | None = None
    request_ids: list[str] = field(default_factory=list)
    chunk_count: int = 1
    api_calls: int = 0


class Transcriber(Protocol):
    def transcribe(
        self,
        audio_path: str,
        *,
        language: str | None = None,
    ) -> TranscriptResult: ...
