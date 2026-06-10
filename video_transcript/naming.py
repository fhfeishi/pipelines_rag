from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OutputNames:
    """Output paths derived from the input media stem."""

    stem: str
    out_dir: Path

    @classmethod
    def from_input(cls, input_path: Path, out_dir: Path) -> OutputNames:
        return cls(stem=input_path.stem, out_dir=out_dir)

    def transcript_txt(self) -> Path:
        """Final deliverable: title + polished body."""
        return self.out_dir / f"{self.stem}.transcript.txt"

    def title_txt(self) -> Path:
        return self.out_dir / f"{self.stem}.title.txt"

    def raw_transcript_txt(self) -> Path:
        """Verbatim ASR output before polish."""
        return self.out_dir / f"{self.stem}.raw.transcript.txt"

    def transcript_json(self) -> Path:
        return self.out_dir / f"{self.stem}.transcript.json"

    def transcript_srt(self) -> Path:
        return self.out_dir / f"{self.stem}.transcript.srt"

    def summary_json(self) -> Path:
        return self.out_dir / f"{self.stem}.summary.json"

    def audio_wav(self) -> Path:
        return self.out_dir / f"{self.stem}.audio_16k.wav"

    def extracted_audio(self, ext: str) -> Path:
        return self.out_dir / f"{self.stem}.extracted{ext}"

    def cache_file(self, cache_key: str) -> Path:
        cache_dir = self.out_dir / ".cache"
        return cache_dir / f"{self.stem}.{cache_key}.cache.json"

    def work_dir(self) -> Path:
        return self.out_dir / ".work" / self.stem
