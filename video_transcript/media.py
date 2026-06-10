from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# Common extensions — ffmpeg/ffprobe handles the real decode.
AUDIO_EXTENSIONS = {
    ".mp3", ".m4a", ".wav", ".flac", ".aac", ".ogg", ".oga", ".opus",
    ".wma", ".amr", ".3gp", ".m4b", ".aiff", ".aif", ".caf",
}
VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".flv", ".ts",
    ".mpeg", ".mpg", ".wmv", ".3gp", ".3g2",
}

AUDIO_CODEC_EXT = {
    "aac": ".m4a",
    "mp3": ".mp3",
    "flac": ".flac",
    "opus": ".opus",
    "vorbis": ".ogg",
    "pcm_s16le": ".wav",
    "pcm_s24le": ".wav",
    "pcm_s32le": ".wav",
    "alac": ".m4a",
}

ASR_SAMPLE_RATE = 16_000
ASR_CHANNELS = 1


class MediaError(RuntimeError):
    pass


@dataclass
class MediaInfo:
    path: Path
    kind: str  # audio | video
    format_name: str
    duration_ms: int
    has_video: bool
    has_audio: bool
    audio_codec: str | None = None
    audio_sample_rate: int | None = None
    audio_channels: int | None = None
    video_codec: str | None = None

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "kind": self.kind,
            "format_name": self.format_name,
            "duration_ms": self.duration_ms,
            "has_video": self.has_video,
            "has_audio": self.has_audio,
            "audio_codec": self.audio_codec,
            "audio_sample_rate": self.audio_sample_rate,
            "audio_channels": self.audio_channels,
            "video_codec": self.video_codec,
        }


@dataclass
class PrepareResult:
    """Result of media normalization before ASR."""

    info: MediaInfo
    extracted_path: Path  # lossless stream copy when possible
    asr_path: Path  # mono 16kHz WAV for DashScope
    extract_mode: str  # copy | transcode_lossless | transcode
    asr_mode: str  # copy | resample | transcode
    steps: list[str] = field(default_factory=list)


def require_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise MediaError("ffmpeg/ffprobe not found. Install ffmpeg and ensure it is on PATH.")


def _run(cmd: list[str], *, action: str) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise MediaError(f"{action} failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc


def probe_media(path: Path) -> MediaInfo:
    require_ffmpeg()
    path = path.resolve()
    if not path.exists():
        raise MediaError(f"File not found: {path}")

    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=format_name,duration",
        "-show_entries",
        "stream=codec_type,codec_name,sample_rate,channels",
        "-of",
        "json",
        str(path),
    ]
    proc = _run(cmd, action=f"ffprobe {path.name}")
    payload = json.loads(proc.stdout)

    streams = payload.get("streams", [])
    fmt = payload.get("format", {})
    duration = float(fmt.get("duration", 0))
    if duration <= 0:
        raise MediaError(f"Could not read duration from {path}")

    has_video = any(s.get("codec_type") == "video" for s in streams)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    if not has_audio:
        raise MediaError(f"No audio stream found in {path}")

    audio_stream = next(s for s in streams if s.get("codec_type") == "audio")
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)

    suffix = path.suffix.lower()
    if suffix in VIDEO_EXTENSIONS or (has_video and suffix not in AUDIO_EXTENSIONS):
        kind = "video"
    elif suffix in AUDIO_EXTENSIONS or (has_audio and not has_video):
        kind = "audio"
    else:
        kind = "video" if has_video else "audio"

    return MediaInfo(
        path=path,
        kind=kind,
        format_name=str(fmt.get("format_name", "")),
        duration_ms=int(duration * 1000),
        has_video=has_video,
        has_audio=has_audio,
        audio_codec=audio_stream.get("codec_name"),
        audio_sample_rate=int(audio_stream["sample_rate"])
        if audio_stream.get("sample_rate")
        else None,
        audio_channels=int(audio_stream["channels"]) if audio_stream.get("channels") else None,
        video_codec=video_stream.get("codec_name") if video_stream else None,
    )


def probe_duration_ms(path: Path) -> int:
    return probe_media(path).duration_ms


def _lossless_ext(codec: str | None) -> str:
    return AUDIO_CODEC_EXT.get(codec or "", ".mka")


def _extract_lossless(input_path: Path, output_path: Path, *, info: MediaInfo) -> tuple[Path, str]:
    """Extract/copy audio without re-encoding when possible."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if info.kind == "video":
        cmd = [
            "ffmpeg", "-y", "-i", str(input_path),
            "-vn", "-map", "0:a:0", "-c:a", "copy", str(output_path),
        ]
    else:
        cmd = [
            "ffmpeg", "-y", "-i", str(input_path),
            "-map", "0:a:0", "-c:a", "copy", str(output_path),
        ]

    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
        return output_path, "copy"

    # Fallback: transcode to FLAC (lossless archive before ASR downsample).
    flac_path = output_path.with_suffix(".flac")
    if info.kind == "video":
        cmd = [
            "ffmpeg", "-y", "-i", str(input_path),
            "-vn", "-map", "0:a:0", "-c:a", "flac", str(flac_path),
        ]
    else:
        cmd = [
            "ffmpeg", "-y", "-i", str(input_path),
            "-map", "0:a:0", "-c:a", "flac", str(flac_path),
        ]

    _run(cmd, action=f"lossless flac extract {input_path.name}")
    output_path.unlink(missing_ok=True)
    return flac_path, "transcode_lossless"


def _transcode_for_asr(input_path: Path, output_path: Path, *, info: MediaInfo) -> str:
    """Produce mono 16kHz PCM WAV for ASR upload."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if (
        info.audio_codec in {"pcm_s16le", "pcm_s24le", "pcm_s32le"}
        and info.audio_sample_rate == ASR_SAMPLE_RATE
        and info.audio_channels == ASR_CHANNELS
        and input_path.suffix.lower() == ".wav"
    ):
        shutil.copy2(input_path, output_path)
        return "copy"

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-ac",
        str(ASR_CHANNELS),
        "-ar",
        str(ASR_SAMPLE_RATE),
        "-c:a",
        "pcm_s16le",
        str(output_path),
    ]
    _run(cmd, action=f"ASR transcode {input_path.name}")
    return "resample" if info.audio_codec in {"pcm_s16le", "pcm_s24le", "pcm_s32le"} else "transcode"


def trim_input(input_path: Path, output_path: Path, *, max_seconds: float) -> Path:
    """Trim media to the first N seconds (for smoke tests / saving API tokens)."""
    require_ffmpeg()
    if max_seconds <= 0:
        raise MediaError("--max-seconds must be positive")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            "ffmpeg", "-y", "-t", str(max_seconds),
            "-i", str(input_path), "-c", "copy", str(output_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or not output_path.exists() or output_path.stat().st_size == 0:
        _run(
            [
                "ffmpeg", "-y", "-t", str(max_seconds),
                "-i", str(input_path), "-c:a", "aac", str(output_path.with_suffix(".m4a")),
            ],
            action=f"trim {input_path.name}",
        )
        return output_path.with_suffix(".m4a")
    return output_path


def prepare_audio(input_path: Path, work_dir: Path, *, max_seconds: float | None = None) -> PrepareResult:
    """Normalize input media for ASR.

    Two-step strategy:
    1. Lossless extract/copy audio stream (no re-encode when container allows).
    2. Transcode to mono 16kHz WAV only for ASR (required by upstream).
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    source_path = input_path
    steps: list[str] = []

    if max_seconds is not None:
        clipped = trim_input(
            input_path,
            work_dir / f"clip_{int(max_seconds)}s{input_path.suffix}",
            max_seconds=max_seconds,
        )
        source_path = clipped
        steps.append(f"trim:{max_seconds}s -> {clipped.name}")

    info = probe_media(source_path)

    ext = _lossless_ext(info.audio_codec)
    extracted_path = work_dir / f"extracted_audio{ext}"
    asr_path = work_dir / "audio_16k_mono.wav"

    extracted_path, extract_mode = _extract_lossless(source_path, extracted_path, info=info)
    steps.append(f"extract:{extract_mode} -> {extracted_path.name}")

    source_for_asr = extracted_path
    asr_info = probe_media(source_for_asr)
    asr_mode = _transcode_for_asr(source_for_asr, asr_path, info=asr_info)
    steps.append(f"asr:{asr_mode} -> {asr_path.name}")

    return PrepareResult(
        info=info,
        extracted_path=source_for_asr,
        asr_path=asr_path,
        extract_mode=extract_mode,
        asr_mode=asr_mode,
        steps=steps,
    )


def split_audio(
    input_path: Path,
    output_dir: Path,
    *,
    chunk_seconds: int,
) -> list[Path]:
    """Split long audio into fixed-length ASR chunks."""
    require_ffmpeg()
    output_dir.mkdir(parents=True, exist_ok=True)
    pattern = output_dir / "chunk_%03d.wav"
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-f",
        "segment",
        "-segment_time",
        str(chunk_seconds),
        "-ac",
        str(ASR_CHANNELS),
        "-ar",
        str(ASR_SAMPLE_RATE),
        "-c:a",
        "pcm_s16le",
        str(pattern),
    ]
    _run(cmd, action=f"split {input_path.name}")
    chunks = sorted(output_dir.glob("chunk_*.wav"))
    if not chunks:
        raise MediaError(f"No chunks produced from {input_path}")
    return chunks


def supported_suffixes() -> list[str]:
    return sorted(AUDIO_EXTENSIONS | VIDEO_EXTENSIONS)
