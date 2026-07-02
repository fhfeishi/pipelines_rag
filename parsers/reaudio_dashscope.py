# parsers/reaudio.py 

# audio files, video files (get audio), url video (get audio)  --to--> llm --to--> chinese markdown

# ffmpeg is needed to convert video to audio

from __future__ import annotations 

import os 
import re 
import json 
import shutil 
import subprocess 
import argparse
import tempfile 
import hashlib  
import sys 
from dataclasses import dataclass, asdict 
from datetime import datetime, timezone  
from pathlib import Path  
from typing import Any  
from urllib.parse import urlparse  


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HTTP_PROXY = (
    os.getenv("https_proxy")
    or os.getenv("HTTPS_PROXY")
    or os.getenv("http_proxy")
    or os.getenv("HTTP_PROXY")
    or "http://127.0.0.1:7897"
)


@dataclass 
class Segment:
    start: float 
    end: float 
    text: str 


@dataclass
class ResolvedInput:
    original: str
    kind: str
    media_path: Path
    stem: str
    title: str
    cache_identity: str
    source_note: str
    metadata: dict[str, Any]
    
class ReaudioDashScopeError(Exception):
    pass  


def main(argv: list[str] | None = None) -> None:
    try:
        args = parse_args(argv)
        run(args)
    except ReaudioDashScopeError as exc:
        raise SystemExit(f"Error: {exc}") from exc 


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="reaudio_dashscope.py",
        description=(
            "Transcribe a local audio/video file or a video URL with DashScope, "
            "then write polished markdown text."
        )
    )
    parser.add_argument(
        "input",
        help=(
            "Audio/video file path, or a video/audio URL supported by yt-dlp "
            "(YouTube, Bilibili, generic media URLs, etc.)."
        ),
    )
    parser.add_argument("--output-dir", default="outputs", help="Directory for generated files.")
    parser.add_argument("--title", default=None, help="Markdown title. Defaults to the input file stem.")
    parser.add_argument("--formats", default="md,json", help="Comma-separated: md,txt,srt,json.")
    parser.add_argument("--language-hints", default="zh,en", help="Comma-separated DashScope language hints.")
    parser.add_argument("--no-polish", action="store_true", help="Skip DashScope LLM polishing.")
    parser.add_argument("--asr-model", default="paraformer-realtime-v2")
    parser.add_argument("--llm-model", default="qwen-plus")
    parser.add_argument("--api-key-env", default="DASHSCOPE_API_KEY")
    parser.add_argument("--max-segment-chars", type=int, default=90, help="Split long segments. Set 0 to disable.")
    parser.add_argument("--max-seconds", type=float, default=None, help="Only process the first N seconds.")
    parser.add_argument("--no-cache", action="store_true", help="Disable the ASR cache.")
    parser.add_argument("--force", action="store_true", help="Ignore the ASR cache and transcribe again.")
    parser.add_argument("--keep-wav", action="store_true", help="Keep normalized 16 kHz WAV beside outputs.")
    parser.add_argument("--keep-source-media", action="store_true", help="Keep URL-downloaded source audio/video beside outputs.")
    parser.add_argument("--download-dir", default=None, help="Directory for kept URL downloads. Defaults to <output-dir>/media.")
    parser.add_argument("--yt-dlp-binary", default="yt-dlp", help="yt-dlp executable for URL inputs.")
    parser.add_argument("--yt-dlp-format", default="bestaudio/best", help="Format selector passed to yt-dlp -f.")
    parser.add_argument("--cookies", type=Path, default=None, help="Cookies file for yt-dlp, useful for Bilibili/YouTube gated videos.")
    parser.add_argument("--cookies-from-browser", default=None, help="Browser cookies source for yt-dlp, e.g. chrome, firefox.")
    parser.add_argument("--http-proxy", default=DEFAULT_HTTP_PROXY, help="Proxy passed to yt-dlp for URL inputs.")
    parser.add_argument("--referer", default=None, help="Referer passed to yt-dlp. Defaults to Bilibili home for bilibili.com URLs.")
    parser.add_argument("--user-agent", default=None, help="User-Agent passed to yt-dlp.")
    parser.add_argument("--url-download-timeout", type=int, default=60, help="yt-dlp socket timeout in seconds.")
    return parser.parse_args(argv)
    

def run(args: argparse.Namespace) -> None:
    if args.max_seconds is not None and args.max_seconds <= 0:
        raise ReaudioDashScopeError("--max-seconds must be greater than 0.")

    check_binary("ffmpeg")

    out_dir = Path(args.output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    formats = parse_formats(args.formats)
    language_hints = [item.strip() for item in args.language_hints.split(",") if item.strip()]

    with tempfile.TemporaryDirectory(prefix="reaudio_dashscope_") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        resolved = resolve_input(args, out_dir=out_dir, temp_dir=temp_dir)
        stem = sanitize_stem(resolved.stem)
        title = args.title or resolved.title or stem

        cache_path = None
        if not args.no_cache:
            cache_path = out_dir / ".cache" / f"{stem}.{cache_key(resolved, args, language_hints)}.json"

        segments: list[Segment] | None = None
        if cache_path and cache_path.exists() and not args.force:
            segments = load_cache(cache_path)

        if segments is None:
            audio_wav = Path(temp_dir) / f"{stem}.16k.wav"
            extract_audio(resolved.media_path, audio_wav, max_seconds=args.max_seconds)
            segments = transcribe_dashscope(
                audio_wav,
                api_key_env=args.api_key_env,
                asr_model=args.asr_model,
                language_hints=language_hints,
            )
            if args.keep_wav:
                shutil.copy2(audio_wav, out_dir / f"{stem}.16k.wav")
            if cache_path:
                save_cache(cache_path, segments, model=args.asr_model)

        source = f"asr:dashscope:{args.asr_model}"
        if not args.no_polish:
            segments = polish_segments_dashscope(
                segments,
                api_key_env=args.api_key_env,
                llm_model=args.llm_model,
            )
            source += f"+polish:{args.llm_model}"

        segments = split_long_segments(segments, args.max_segment_chars)
        write_outputs(
            segments,
            out_dir=out_dir,
            stem=stem,
            title=title,
            source=source,
            input_path=resolved.media_path,
            original_input=resolved.original,
            source_note=resolved.source_note,
            input_metadata=resolved.metadata,
            formats=formats,
            max_seconds=args.max_seconds,
        )


def resolve_input(args: argparse.Namespace, *, out_dir: Path, temp_dir: Path) -> ResolvedInput:
    raw_input = str(args.input)
    if is_url(raw_input):
        return download_url_input(raw_input, args, out_dir=out_dir, temp_dir=temp_dir)

    input_path = Path(raw_input).expanduser().resolve()
    if not input_path.exists():
        raise ReaudioDashScopeError(f"Input file does not exist: {input_path}")
    return ResolvedInput(
        original=raw_input,
        kind="file",
        media_path=input_path,
        stem=input_path.stem,
        title=input_path.stem,
        cache_identity=file_hash(input_path),
        source_note=f"local file: {input_path}",
        metadata={"path": str(input_path), "size_bytes": input_path.stat().st_size},
    )


def is_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def download_url_input(
    url: str,
    args: argparse.Namespace,
    *,
    out_dir: Path,
    temp_dir: Path,
) -> ResolvedInput:
    yt_dlp_command = resolve_yt_dlp_command(args.yt_dlp_binary)

    download_dir = (
        Path(args.download_dir).expanduser().resolve()
        if args.download_dir
        else out_dir / "media"
    )
    target_dir = download_dir if args.keep_source_media else temp_dir / "download"
    target_dir.mkdir(parents=True, exist_ok=True)

    output_template = str(target_dir / "%(title).180B.%(id)s.%(ext)s")
    command = [
        *yt_dlp_command,
        "--no-playlist",
        "--newline",
        "--socket-timeout",
        str(args.url_download_timeout),
        "-f",
        args.yt_dlp_format,
        "-x",
        "--audio-format",
        "m4a",
        "--print",
        "after_move:filepath",
        "--print",
        "%(title)s",
        "--print",
        "%(id)s",
        "-o",
        output_template,
    ]
    if args.http_proxy:
        command += ["--proxy", args.http_proxy]
    referer = args.referer or ("https://www.bilibili.com/" if "bilibili.com" in urlparse(url).netloc else None)
    if referer:
        command += ["--referer", referer]
    if args.user_agent:
        command += ["--user-agent", args.user_agent]
    if args.cookies:
        command += ["--cookies", str(args.cookies.expanduser().resolve())]
    if args.cookies_from_browser:
        command += ["--cookies-from-browser", args.cookies_from_browser]
    command.append(url)

    completed = run_command(command)
    media_path, title, video_id = parse_yt_dlp_download_output(completed.stdout, target_dir)
    if not media_path.exists():
        raise ReaudioDashScopeError(f"yt-dlp finished but downloaded media was not found: {media_path}")

    title = clean_text(title) or media_path.stem
    stem = sanitize_stem(title or media_path.stem)
    metadata = {
        "url": url,
        "downloaded_path": str(media_path),
        "title": title,
        "video_id": video_id,
        "yt_dlp_format": args.yt_dlp_format,
        "kept_source_media": bool(args.keep_source_media),
    }
    return ResolvedInput(
        original=url,
        kind="url",
        media_path=media_path,
        stem=stem,
        title=title,
        cache_identity=url,
        source_note=f"url via yt-dlp: {url}",
        metadata=metadata,
    )


def resolve_yt_dlp_command(binary: str) -> list[str]:
    if shutil.which(binary):
        return [binary]
    if binary == "yt-dlp":
        try:
            import yt_dlp  # noqa: F401
        except ImportError as exc:
            raise ReaudioDashScopeError(
                "URL input requires yt-dlp. Install it with `python -m pip install yt-dlp` "
                "or put the `yt-dlp` executable on PATH."
            ) from exc
        return [sys.executable, "-m", "yt_dlp"]
    raise ReaudioDashScopeError(f"yt-dlp executable not found: {binary}")


def parse_yt_dlp_download_output(stdout: str, target_dir: Path) -> tuple[Path, str, str]:
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    existing_paths = [Path(line).expanduser().resolve() for line in lines if Path(line).expanduser().exists()]
    if existing_paths:
        media_path = existing_paths[-1]
        path_index = lines.index(str(media_path)) if str(media_path) in lines else -1
        remaining = [line for idx, line in enumerate(lines) if idx != path_index]
        title = remaining[0] if remaining else media_path.stem
        video_id = remaining[1] if len(remaining) > 1 else ""
        return media_path, title, video_id

    candidates = sorted(
        [path for path in target_dir.rglob("*") if path.is_file()],
        key=lambda path: path.stat().st_mtime,
    )
    if not candidates:
        raise ReaudioDashScopeError("yt-dlp did not report or create a downloadable media file.")
    media_path = candidates[-1].resolve()
    title = lines[-2] if len(lines) >= 2 else media_path.stem
    video_id = lines[-1] if lines else ""
    return media_path, title, video_id


def sanitize_stem(value: str) -> str:
    value = clean_text(value) or "audio"
    stem = re.sub(r"[^\w.\-一-龥]+", "_", value, flags=re.UNICODE).strip("._-")
    return (stem or "audio")[:120]


def extract_audio(input_path: Path, output_wav: Path, *, max_seconds: float | None = None) -> None:
    command = ["ffmpeg", "-y", "-v", "error", "-i", str(input_path)]
    if max_seconds:
        command += ["-t", f"{max_seconds:g}"]
    command += ["-vn", "-ac", "1", "-ar", "16000", "-f", "wav", str(output_wav)]
    run_command(command)


def transcribe_dashscope(
    audio_wav: Path,
    *,
    api_key_env: str,
    asr_model: str,
    language_hints: list[str],
) -> list[Segment]:
    api_key = read_api_key(api_key_env)
    try:
        import dashscope
        from dashscope.audio.asr import Recognition
    except Exception as exc:
        raise ReaudioDashScopeError(
            "DashScope SDK is not installed. Run `uv sync --extra dashscope --extra dev`."
        ) from exc

    dashscope.api_key = api_key
    recognition = Recognition(
        model=asr_model,
        format="wav",
        sample_rate=16000,
        language_hints=language_hints or ["zh", "en"],
        callback=None,
    )
    result = recognition.call(str(audio_wav))
    if getattr(result, "status_code", 200) >= 300:
        raise ReaudioDashScopeError(f"DashScope ASR failed: {getattr(result, 'message', result)}")
    return normalize_dashscope_result(result)


def polish_segments_dashscope(
    segments: list[Segment],
    *,
    api_key_env: str,
    llm_model: str,
) -> list[Segment]:
    api_key = read_api_key(api_key_env)
    try:
        import dashscope
        from dashscope import Generation
    except Exception as exc:
        raise ReaudioDashScopeError(
            "DashScope SDK is not installed. Run `uv sync --extra dashscope --extra dev`."
        ) from exc

    dashscope.api_key = api_key
    output: list[Segment] = []
    batch_size = 20
    for offset in range(0, len(segments), batch_size):
        batch = segments[offset : offset + batch_size]
        payload = "\n".join(f"{index + 1}. {seg.text}" for index, seg in enumerate(batch))
        prompt = (
            "请把下面的音视频转写文本润色为中文为主的 Markdown 笔记素材。要求：\n"
            "1. 修正明显的语音识别错字和标点问题。\n"
            "2. 保留不适合翻译的英文专有名词、产品名、代码名。\n"
            "3. 不要添加原文没有的信息，不要总结，不要扩写。\n"
            "4. 逐条输出，数量和编号必须与输入一致。\n\n"
            f"{payload}"
        )
        response = Generation.call(model=llm_model, prompt=prompt)
        if getattr(response, "status_code", 200) >= 300:
            raise ReaudioDashScopeError(f"DashScope polish failed: {getattr(response, 'message', response)}")
        polished = parse_numbered_lines(extract_dashscope_text(response))
        for index, seg in enumerate(batch):
            text = polished[index] if index < len(polished) and polished[index] else seg.text
            output.append(Segment(start=seg.start, end=seg.end, text=clean_text(text)))
    return output


def normalize_dashscope_result(result: Any) -> list[Segment]:
    data = None
    if hasattr(result, "get_sentence"):
        data = result.get_sentence()
    if data is None and hasattr(result, "output"):
        data = result.output
    if data is None:
        data = result

    segments: list[Segment] = []
    for item in flatten_possible_segments(data):
        text = clean_text(str(item.get("text") or item.get("sentence") or item.get("result") or ""))
        if not text:
            continue
        start = milliseconds_to_seconds(item.get("begin_time", item.get("start_time", item.get("start", 0))))
        end = milliseconds_to_seconds(item.get("end_time", item.get("end", item.get("finish", 0))))
        segments.append(Segment(start=start, end=end, text=text))

    if not segments:
        text = clean_text(safe_string(data))
        if text:
            segments.append(Segment(start=0.0, end=0.1, text=text))
    return fix_segment_times(segments)


def flatten_possible_segments(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        for key in ("sentences", "sentence", "results", "transcripts"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return [data]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def fix_segment_times(segments: list[Segment]) -> list[Segment]:
    fixed: list[Segment] = []
    for index, seg in enumerate(segments):
        start = max(0.0, float(seg.start or 0.0))
        end = float(seg.end or 0.0)
        if end <= start:
            next_start = segments[index + 1].start if index + 1 < len(segments) else 0.0
            end = next_start if next_start > start else start + 0.1
        fixed.append(Segment(start=start, end=end, text=clean_text(seg.text)))
    return fixed


def write_outputs(
    segments: list[Segment],
    *,
    out_dir: Path,
    stem: str,
    title: str,
    source: str,
    input_path: Path,
    original_input: str,
    source_note: str,
    input_metadata: dict[str, Any],
    formats: set[str],
    max_seconds: float | None,
) -> None:
    if "md" in formats:
        (out_dir / f"{stem}.md").write_text(
            render_markdown(
                segments,
                title=title,
                source=source,
                input_path=input_path,
                original_input=original_input,
                source_note=source_note,
                input_metadata=input_metadata,
                max_seconds=max_seconds,
            ),
            encoding="utf-8",
        )
    if "txt" in formats:
        (out_dir / f"{stem}.txt").write_text(render_txt(segments), encoding="utf-8")
    if "srt" in formats:
        (out_dir / f"{stem}.srt").write_text(render_srt(segments), encoding="utf-8")
    if "json" in formats:
        payload = {
            "source": source,
            "input": {
                "original": original_input,
                "resolved_path": str(input_path),
                "source_note": source_note,
                "metadata": input_metadata,
            },
            "segments": [asdict(item) for item in segments],
        }
        (out_dir / f"{stem}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {', '.join(sorted(formats))} to {out_dir}")


def render_markdown(
    segments: list[Segment],
    *,
    title: str,
    source: str,
    input_path: Path,
    original_input: str,
    source_note: str,
    input_metadata: dict[str, Any],
    max_seconds: float | None,
) -> str:
    generated = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    lines = [
        f"# {title}",
        "",
        f"- Original input: `{original_input}`",
        f"- Resolved media: `{input_path}`",
        f"- Input source: `{source_note}`",
        f"- Text source: `{source}`",
        f"- Generated: `{generated}`",
    ]
    if input_metadata.get("video_id"):
        lines.append(f"- Video id: `{input_metadata['video_id']}`")
    if max_seconds:
        lines.append(f"- Clip: first `{max_seconds:g}` seconds")
    lines += ["", "## Transcript", ""]
    for seg in segments:
        lines.append(f"### {format_clock(seg.start)} - {format_clock(seg.end)}")
        lines.append("")
        lines.append(seg.text)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_txt(segments: list[Segment]) -> str:
    return "\n".join(f"[{format_clock(seg.start)} - {format_clock(seg.end)}] {seg.text}" for seg in segments) + "\n"


def render_srt(segments: list[Segment]) -> str:
    blocks = []
    for index, seg in enumerate(segments, start=1):
        blocks.append(f"{index}\n{format_srt_time(seg.start)} --> {format_srt_time(seg.end)}\n{seg.text}")
    return "\n\n".join(blocks) + "\n"


def split_long_segments(segments: list[Segment], max_chars: int) -> list[Segment]:
    if max_chars <= 0:
        return segments
    output: list[Segment] = []
    for seg in segments:
        chunks = split_text(seg.text, max_chars)
        if len(chunks) == 1:
            output.append(seg)
            continue
        total_chars = sum(max(1, len(chunk)) for chunk in chunks)
        cursor = seg.start
        duration = max(0.1, seg.end - seg.start)
        for index, chunk in enumerate(chunks):
            end = seg.end if index == len(chunks) - 1 else cursor + duration * (max(1, len(chunk)) / total_chars)
            output.append(Segment(start=cursor, end=end, text=chunk))
            cursor = end
    return output


def split_text(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    pieces = re.findall(r"[^。！？!?；;，,]+[。！？!?；;，,]?", text)
    chunks: list[str] = []
    current = ""
    for piece in pieces or [text]:
        piece = piece.strip()
        if not piece:
            continue
        if len(piece) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(piece[index : index + max_chars] for index in range(0, len(piece), max_chars))
            continue
        if current and len(current) + len(piece) > max_chars:
            chunks.append(current)
            current = piece
        else:
            current += piece
    if current:
        chunks.append(current)
    return chunks


def parse_formats(value: str) -> set[str]:
    formats = {item.strip().lower() for item in value.split(",") if item.strip()}
    allowed = {"md", "txt", "srt", "json"}
    unknown = formats - allowed
    if unknown:
        raise ReaudioDashScopeError(f"Unsupported formats: {', '.join(sorted(unknown))}")
    return formats or {"md"}


def cache_key(resolved: ResolvedInput, args: argparse.Namespace, language_hints: list[str]) -> str:
    model_tag = hashlib.sha256(args.asr_model.encode("utf-8")).hexdigest()[:8]
    lang_tag = "-".join(language_hints) or "auto"
    clip_tag = f"clip{args.max_seconds:g}" if args.max_seconds else "full"
    identity = resolved.cache_identity or str(resolved.media_path)
    identity_tag = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    return f"dashscope_{model_tag}_{lang_tag}_{clip_tag}_{identity_tag}"


def load_cache(path: Path) -> list[Segment] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return normalize_cached_segments(payload.get("segments") or [])
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def save_cache(path: Path, segments: list[Segment], *, model: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"backend": "dashscope", "model": model, "segments": [asdict(item) for item in segments]}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_cached_segments(raw: list[dict[str, Any]]) -> list[Segment]:
    segments: list[Segment] = []
    for item in raw:
        text = clean_text(str(item.get("text") or ""))
        if not text:
            continue
        start = as_float(item.get("start"))
        end = as_float(item.get("end"))
        if end <= start:
            end = start + 0.1
        segments.append(Segment(start=start, end=end, text=text))
    return segments


def file_hash(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(chunk_size):
            digest.update(block)
    return digest.hexdigest()


def read_api_key(api_key_env: str) -> str:
    api_key = os.getenv(api_key_env)
    if not api_key:
        env_values = load_env_file(REPO_ROOT / "configs" / ".env")
        api_key = env_values.get(api_key_env) or env_values.get(api_key_env.lower())
        if not api_key and api_key_env in {"DASHSCOPE_API_KEY", "dashscope_api_key"}:
            api_key = env_values.get("DASHSCOPE_API_KEY") or env_values.get("dashscope_api_key")
    if not api_key:
        raise ReaudioDashScopeError(
            f"{api_key_env} is not set. Export it or add dashscope_api_key to configs/.env."
        )
    return api_key


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


def check_binary(name: str) -> None:
    if not shutil.which(name):
        raise ReaudioDashScopeError(f"Required binary not found: {name}. Install ffmpeg in WSL.")


def run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, check=True, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except FileNotFoundError as exc:
        raise ReaudioDashScopeError(f"Command not found: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise ReaudioDashScopeError(f"Command failed: {command[0]}\n{detail}") from exc


def milliseconds_to_seconds(value: Any) -> float:
    try:
        return float(value) / 1000.0
    except (TypeError, ValueError):
        return 0.0


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\u3000", " ")).strip()


def safe_string(value: Any) -> str:
    try:
        return str(value)
    except Exception:
        return ""


def parse_numbered_lines(text: str) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    parsed: list[str] = []
    for line in lines:
        line = re.sub(r"^\s*\d+\s*[\.、)]\s*", "", line).strip()
        if line:
            parsed.append(line)
    return parsed


def extract_dashscope_text(response: Any) -> str:
    output = getattr(response, "output", None)
    if isinstance(output, dict):
        choices = output.get("choices") or []
        if choices:
            return str(choices[0].get("message", {}).get("content") or "")
        return str(output.get("text") or "")
    return str(output or response)


def as_float(value: Any) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def format_clock(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    rest = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{rest:05.2f}"


def format_srt_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    if millis == 1000:
        secs += 1
        millis = 0
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


if __name__ == "__main__":
    main(sys.argv[1:])
