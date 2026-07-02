"""Unified static parsing/structuring entry for parser experiments.

This module is intentionally a thin orchestrator. The concrete experiments stay
in focused files such as ``redox_opendataloaderpdf.py``, ``rewebpage_craw.py``,
``rewebpage_firecrawl.py`` and ``reaudio_dashscope.py``; this entry chooses one
of them, gives it an output directory, and writes a small manifest.

Examples:
    python -m parsers.static_structurer path/to/file.pdf
    python -m parsers.static_structurer https://example.com/article --kind webpage
    python -m parsers.static_structurer video.mp4 --kind media --max-seconds 60
    python -m parsers.static_structurer --list-tools
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_ROOT = REPO_ROOT / "outputs"

PDF_SUFFIXES = {".pdf"}
AUDIO_SUFFIXES = {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav", ".webm"}
VIDEO_SUFFIXES = {".avi", ".flv", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".webm"}
TEXT_SUFFIXES = {".htm", ".html", ".md", ".markdown", ".txt"}


@dataclass(frozen=True)
class ToolSpec:
    key: str
    module: str
    kinds: tuple[str, ...]
    description: str
    notes: str


@dataclass
class RunRecord:
    tool: str
    module: str
    command: list[str]
    status: str
    output_dir: str | None = None
    error: str | None = None


TOOLS: dict[str, ToolSpec] = {
    "opendataloader_pdf": ToolSpec(
        key="opendataloader_pdf",
        module="parsers.redox_opendataloaderpdf",
        kinds=("pdf",),
        description="PDF -> layout JSON / extracted images / elements JSONL / image-aware Markdown",
        notes="parsers/redox_notes.md",
    ),
    "crawl4ai": ToolSpec(
        key="crawl4ai",
        module="parsers.rewebpage_craw",
        kinds=("webpage",),
        description="Webpage URL -> page JSON / Markdown / screenshot PDF via crawl4ai",
        notes="parsers/rewebpage_notes.md",
    ),
    "firecrawl": ToolSpec(
        key="firecrawl",
        module="parsers.rewebpage_firecrawl",
        kinds=("webpage",),
        description="Webpage URL -> page JSON / Markdown / screenshot PDF via Firecrawl API",
        notes="parsers/rewebpage_notes.md",
    ),
    "dashscope_asr": ToolSpec(
        key="dashscope_asr",
        module="parsers.reaudio_dashscope",
        kinds=("media",),
        description="Local audio/video or media URL -> transcript JSON / Markdown / SRT",
        notes="parsers/reaudio_notes.md",
    ),
    "copy_text": ToolSpec(
        key="copy_text",
        module="builtin",
        kinds=("text",),
        description="Local static text/Markdown/HTML -> normalized source file and document.md",
        notes="parsers/static_structurer_notes.md",
    ),
}


class StaticStructurerError(Exception):
    """Expected user-facing error from the static structurer."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Route PDFs, webpages and audio/video-like static sources through parser "
            "experiments, then collect outputs under outputs/<source-stem>/."
        ),
    )
    parser.add_argument("source", nargs="?", help="Local file path or http(s) URL.")
    parser.add_argument(
        "--kind",
        choices=["auto", "pdf", "webpage", "media", "text"],
        default="auto",
        help="Source kind. auto detects from URL/suffix.",
    )
    parser.add_argument(
        "--tool",
        choices=["auto", *TOOLS.keys()],
        default="auto",
        help="Parser backend. auto chooses a conservative default for the detected kind.",
    )
    parser.add_argument(
        "--out-root",
        type=Path,
        default=DEFAULT_OUT_ROOT,
        help=f"Root output directory. Default: {DEFAULT_OUT_ROOT}",
    )
    parser.add_argument(
        "--stem",
        help="Override the output stem used in outputs/<stem>/.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned commands without running parser backends or writing files.",
    )
    parser.add_argument(
        "--list-tools",
        action="store_true",
        help="List known parser backends and exit.",
    )
    parser.add_argument(
        "--parse-page-pdf",
        action="store_true",
        help=(
            "After a webpage backend writes page.pdf, also run redox_opendataloaderpdf "
            "into the same source package."
        ),
    )
    parser.add_argument(
        "--reading-order",
        choices=["flat", "bbox"],
        default="flat",
        help="Reading order passed to redox_opendataloaderpdf.",
    )
    args, backend_args = parser.parse_known_args(argv)
    args.backend_args = backend_args[1:] if backend_args[:1] == ["--"] else backend_args
    return args


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    if args.list_tools:
        print_tool_table()
        return
    if not args.source:
        raise SystemExit("Error: source is required unless --list-tools is used.")

    try:
        manifest = run(args)
    except StaticStructurerError as exc:
        raise SystemExit(f"Error: {exc}") from exc

    failed = [item for item in manifest["records"] if item["status"] == "failed"]
    if failed:
        manifest_path = manifest.get("manifest_path") or "(dry-run)"
        raise SystemExit(
            f"Error: {len(failed)} backend run(s) failed. See manifest: {manifest_path}"
        )

    print(f"完成：kind={manifest['kind']} tool={manifest['tool']}")
    if not args.dry_run:
        print(f"输出包：{manifest['source_dir']}")
        print(f"Manifest：{manifest['manifest_path']}")


def run(args: argparse.Namespace) -> dict[str, Any]:
    source = args.source
    kind = detect_kind(source, args.kind)
    tool_key = choose_tool(kind, args.tool)
    source_stem = sanitize_stem(args.stem or infer_stem(source, kind))
    out_root = args.out_root.expanduser().resolve()
    source_dir = out_root / source_stem
    records: list[RunRecord] = []

    print(f"source : {source}")
    print(f"kind   : {kind}")
    print(f"tool   : {tool_key}")
    print(f"package: {source_dir}")

    if tool_key == "copy_text":
        record = run_copy_text(source, source_dir, dry_run=args.dry_run)
        records.append(record)
    elif tool_key == "opendataloader_pdf":
        record = run_pdf_tool(
            source,
            source_dir,
            reading_order=args.reading_order,
            dry_run=args.dry_run,
            backend_args=args.backend_args,
        )
        records.append(record)
    elif tool_key in {"crawl4ai", "firecrawl"}:
        record = run_webpage_tool(
            source,
            source_dir,
            tool_key=tool_key,
            dry_run=args.dry_run,
            backend_args=args.backend_args,
        )
        records.append(record)
        if args.parse_page_pdf and args.dry_run:
            page_pdf = expected_webpage_pdf(source, source_dir / tool_key)
            records.append(
                run_pdf_tool(
                    str(page_pdf),
                    source_dir,
                    reading_order=args.reading_order,
                    dry_run=True,
                    backend_args=[],
                )
            )
        elif args.parse_page_pdf and record.status == "ok":
            page_pdf = newest_file(source_dir / tool_key, "page.pdf")
            if page_pdf:
                records.append(
                    run_pdf_tool(
                        str(page_pdf),
                        source_dir,
                        reading_order=args.reading_order,
                        dry_run=False,
                        backend_args=[],
                    )
                )
            else:
                records.append(
                    RunRecord(
                        tool="opendataloader_pdf",
                        module=TOOLS["opendataloader_pdf"].module,
                        command=[],
                        status="skipped",
                        error=f"No page.pdf found under {source_dir / tool_key}",
                    )
                )
    elif tool_key == "dashscope_asr":
        records.append(
            run_media_tool(
                source,
                source_dir,
                dry_run=args.dry_run,
                backend_args=args.backend_args,
            )
        )
    else:  # pragma: no cover - guarded by argparse/choose_tool
        raise StaticStructurerError(f"Unsupported tool: {tool_key}")

    manifest = {
        "source": source,
        "kind": kind,
        "tool": tool_key,
        "source_stem": source_stem,
        "source_dir": str(source_dir),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": bool(args.dry_run),
        "records": [asdict(item) for item in records],
    }

    if args.dry_run:
        manifest["manifest_path"] = None
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return manifest

    source_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = source_dir / "static_parse_manifest.json"
    manifest["manifest_path"] = str(manifest_path)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def print_tool_table() -> None:
    rows = [
        {
            "tool": spec.key,
            "kinds": ",".join(spec.kinds),
            "module": spec.module,
            "notes": spec.notes,
            "description": spec.description,
        }
        for spec in TOOLS.values()
    ]
    print(json.dumps(rows, ensure_ascii=False, indent=2))


def detect_kind(source: str, requested: str) -> str:
    if requested != "auto":
        return requested
    if is_url(source):
        suffix = Path(urlparse(source).path).suffix.lower()
        if suffix in AUDIO_SUFFIXES or suffix in VIDEO_SUFFIXES:
            return "media"
        return "webpage"
    suffix = Path(source).suffix.lower()
    if suffix in PDF_SUFFIXES:
        return "pdf"
    if suffix in AUDIO_SUFFIXES or suffix in VIDEO_SUFFIXES:
        return "media"
    if suffix in TEXT_SUFFIXES:
        return "text"
    raise StaticStructurerError(
        f"Could not detect source kind from {source!r}. Pass --kind pdf|webpage|media|text."
    )


def choose_tool(kind: str, requested: str) -> str:
    if requested != "auto":
        spec = TOOLS[requested]
        if kind not in spec.kinds:
            raise StaticStructurerError(f"Tool {requested!r} does not support kind {kind!r}.")
        return requested
    defaults = {
        "pdf": "opendataloader_pdf",
        "webpage": "crawl4ai",
        "media": "dashscope_asr",
        "text": "copy_text",
    }
    return defaults[kind]


def run_pdf_tool(
    source: str,
    source_dir: Path,
    *,
    reading_order: str,
    dry_run: bool,
    backend_args: list[str],
) -> RunRecord:
    pdf_path = dry_run_path(source) if dry_run else resolve_existing_file(source)
    out_dir = source_dir / "opendataloader_pdf"
    command = [
        sys.executable,
        "-m",
        TOOLS["opendataloader_pdf"].module,
        str(pdf_path),
        "--out",
        str(out_dir),
        "--reading-order",
        reading_order,
        *backend_args,
    ]
    return run_command_record(
        tool="opendataloader_pdf",
        module=TOOLS["opendataloader_pdf"].module,
        command=command,
        output_dir=out_dir,
        dry_run=dry_run,
    )


def run_webpage_tool(
    source: str,
    source_dir: Path,
    *,
    tool_key: str,
    dry_run: bool,
    backend_args: list[str],
) -> RunRecord:
    spec = TOOLS[tool_key]
    out_dir = source_dir / tool_key
    out_arg = "--out-dir"
    command = [sys.executable, "-m", spec.module, source, out_arg, str(out_dir), *backend_args]
    return run_command_record(
        tool=tool_key,
        module=spec.module,
        command=command,
        output_dir=out_dir,
        dry_run=dry_run,
    )


def run_media_tool(
    source: str,
    source_dir: Path,
    *,
    dry_run: bool,
    backend_args: list[str],
) -> RunRecord:
    spec = TOOLS["dashscope_asr"]
    out_dir = source_dir / "reaudio_dashscope"
    command = [sys.executable, "-m", spec.module, source, "--output-dir", str(out_dir), *backend_args]
    return run_command_record(
        tool=spec.key,
        module=spec.module,
        command=command,
        output_dir=out_dir,
        dry_run=dry_run,
    )


def run_copy_text(source: str, source_dir: Path, *, dry_run: bool) -> RunRecord:
    source_path = dry_run_path(source) if dry_run else resolve_existing_file(source)
    command = ["builtin:copy_text", str(source_path), str(source_dir)]
    if dry_run:
        print("$ " + " ".join(command))
        return RunRecord(
            tool="copy_text",
            module="builtin",
            command=command,
            status="planned",
            output_dir=str(source_dir),
        )

    source_dir.mkdir(parents=True, exist_ok=True)
    target = source_dir / f"source{source_path.suffix.lower()}"
    shutil.copy2(source_path, target)
    text = source_path.read_text(encoding="utf-8", errors="replace")
    if source_path.suffix.lower() in {".md", ".markdown"}:
        document = text if text.endswith("\n") else text + "\n"
    else:
        document = f"# {source_path.stem}\n\n```text\n{text.rstrip()}\n```\n"
    (source_dir / "document.md").write_text(document, encoding="utf-8")
    return RunRecord(
        tool="copy_text",
        module="builtin",
        command=command,
        status="ok",
        output_dir=str(source_dir),
    )


def run_command_record(
    *,
    tool: str,
    module: str,
    command: list[str],
    output_dir: Path,
    dry_run: bool,
) -> RunRecord:
    print("$ " + shell_join(command))
    if dry_run:
        return RunRecord(
            tool=tool,
            module=module,
            command=command,
            status="planned",
            output_dir=str(output_dir),
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError as exc:
        return RunRecord(
            tool=tool,
            module=module,
            command=command,
            status="failed",
            output_dir=str(output_dir),
            error=f"exit_code={exc.returncode}",
        )
    return RunRecord(
        tool=tool,
        module=module,
        command=command,
        status="ok",
        output_dir=str(output_dir),
    )


def is_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def infer_stem(source: str, kind: str) -> str:
    if is_url(source):
        parsed = urlparse(source)
        raw = (parsed.netloc + parsed.path).strip("/")
        if parsed.query:
            raw += "_" + parsed.query
        return raw or kind
    path = Path(source)
    if path.name == "page.pdf" and path.parent.name:
        return path.parent.name
    return path.stem or kind


def sanitize_stem(value: str) -> str:
    chars = []
    for char in value.strip():
        if char.isalnum() or char in "._-":
            chars.append(char)
        else:
            chars.append("_")
    stem = "".join(chars).strip("._-")
    return (stem or "source")[:120]


def resolve_existing_file(source: str) -> Path:
    path = Path(source).expanduser().resolve()
    if not path.exists():
        raise StaticStructurerError(f"Input file does not exist: {path}")
    if not path.is_file():
        raise StaticStructurerError(f"Input must be a file: {path}")
    return path


def dry_run_path(source: str) -> Path:
    return Path(source).expanduser().resolve()


def newest_file(root: Path, filename: str) -> Path | None:
    if not root.exists():
        return None
    candidates = sorted(root.rglob(filename), key=lambda path: path.stat().st_mtime)
    return candidates[-1] if candidates else None


def expected_webpage_pdf(source: str, tool_out_dir: Path) -> Path:
    return tool_out_dir / sanitize_stem(infer_stem(source, "webpage"))[:100] / "page.pdf"


def shell_join(command: list[str]) -> str:
    return " ".join(quote_arg(part) for part in command)


def quote_arg(value: str) -> str:
    if not value:
        return "''"
    if any(char.isspace() for char in value) or any(char in value for char in '"\'()[]{}'):
        return '"' + value.replace('"', '\\"') + '"'
    return value


if __name__ == "__main__":
    main(sys.argv[1:])
