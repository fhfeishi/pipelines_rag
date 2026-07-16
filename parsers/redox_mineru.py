"""CPU-first MinerU adapter for the repository's static parse package.

MinerU 3.x exposes its stable local entry through the ``mineru`` CLI.  This
module deliberately orchestrates that public interface instead of importing
MinerU's changing internal classes.  It forces the CPU-capable ``pipeline``
backend, limits CPU/memory concurrency, preserves the raw MinerU output, and
normalizes the useful artifacts for the rest of this repository.

Typical usage::

    # Inspect the command and resource settings without downloading models.
    python -m parsers.redox_mineru inputs/example.pdf --dry-run

    # Pure-CPU parse (first run downloads the pipeline models).
    python -m parsers.redox_mineru inputs/example.pdf \
      --model-source modelscope --threads 4 --processing-window-size 4

    # Re-normalize an existing raw/ directory without invoking MinerU.
    python -m parsers.redox_mineru inputs/example.pdf \
      --out outputs/example/mineru --skip-parse
"""

from __future__ import annotations

import argparse
import filecmp
import hashlib
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from parsers.document.package import write_jsonl


REPO_ROOT = Path(__file__).resolve().parents[1]
SUPPORTED_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".docx", ".pptx", ".xlsx"}
IMAGE_LINK_RE = re.compile(
    r"!\[(?P<alt>[^\]]*)\]\((?P<target><[^>]+>|[^)\s]+)"
    r"(?:\s+['\"][^)]*['\"])?\)"
)
GIB = 1024**3


class MinerUAdapterError(RuntimeError):
    """Expected user-facing error from the MinerU adapter."""


def default_thread_count() -> int:
    """Use a conservative default on shared CPU-only development machines."""

    return min(4, max(1, os.cpu_count() or 1))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "用 MinerU pipeline 后端在纯 CPU 环境解析文档，并输出项目统一的静态解析包。"
        )
    )
    parser.add_argument("document", nargs="?", type=Path, help="本地文档路径。")
    parser.add_argument(
        "--out",
        type=Path,
        help="输出目录；默认 outputs/<source-stem>/mineru/。",
    )
    parser.add_argument(
        "--method",
        choices=["auto", "txt", "ocr"],
        default="auto",
        help="MinerU pipeline 解析方式。",
    )
    parser.add_argument("--lang", help="OCR 语言，例如 ch、korean 或 arabic。")
    parser.add_argument("--start", type=int, help="起始页，0-based。")
    parser.add_argument("--end", type=int, help="结束页，0-based。")
    parser.add_argument(
        "--formula",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="启用/禁用公式解析；CPU 首次试跑可用 --no-formula 加速。",
    )
    parser.add_argument(
        "--table",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="启用/禁用表格解析；CPU 首次试跑可用 --no-table 加速。",
    )
    parser.add_argument(
        "--model-source",
        choices=["auto", "huggingface", "modelscope", "local"],
        default="auto",
        help="MinerU 模型来源；国内网络可选 modelscope。",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=default_thread_count(),
        help="ONNX intra-op CPU 线程数；默认最多 4。",
    )
    parser.add_argument(
        "--inter-op-threads",
        type=int,
        default=1,
        help="ONNX inter-op 线程数；默认 1，避免多个模型互抢 CPU。",
    )
    parser.add_argument(
        "--render-threads",
        type=int,
        default=2,
        help="PDF 渲染并发；默认 2。",
    )
    parser.add_argument(
        "--processing-window-size",
        type=int,
        default=4,
        help="处理窗口；默认 4，以吞吐换较低峰值内存。",
    )
    parser.add_argument(
        "--task-timeout",
        type=int,
        default=7200,
        help="等待本地 MinerU 任务的秒数；CPU 默认 7200。",
    )
    parser.add_argument(
        "--mineru-bin",
        default=os.environ.get("MINERU_BIN", "mineru"),
        help="MinerU CLI 路径或命令名。",
    )
    parser.add_argument(
        "--no-copy-source",
        action="store_true",
        help="不把输入文档复制到输出包根目录。",
    )
    parser.add_argument(
        "--skip-parse",
        action="store_true",
        help="跳过 MinerU，只归一化 --out/raw 中已有结果。",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="删除 --out/raw 后重新解析；仅影响本工具的 raw 和规范化产物。",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="显示 CPU 配置和命令，不运行 MinerU、不写文件。",
    )
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="检查 MinerU CLI、内存和磁盘，然后退出；document 可省略。",
    )
    return parser.parse_args(argv)


def validate_args(args: argparse.Namespace) -> None:
    for name in (
        "threads",
        "inter_op_threads",
        "render_threads",
        "processing_window_size",
        "task_timeout",
    ):
        if getattr(args, name) < 1:
            raise MinerUAdapterError(f"--{name.replace('_', '-')} 必须大于 0。")
    if args.start is not None and args.start < 0:
        raise MinerUAdapterError("--start 必须大于或等于 0。")
    if args.end is not None and args.end < 0:
        raise MinerUAdapterError("--end 必须大于或等于 0。")
    if args.start is not None and args.end is not None and args.end < args.start:
        raise MinerUAdapterError("--end 不能小于 --start。")
    if args.skip_parse and args.overwrite:
        raise MinerUAdapterError("--skip-parse 与 --overwrite 不能同时使用。")


def resolve_document(value: Path | None) -> Path:
    if value is None:
        raise MinerUAdapterError("document 是必需参数（--doctor 除外）。")
    path = value.expanduser().resolve()
    if not path.is_file():
        raise MinerUAdapterError(f"输入文档不存在或不是文件：{path}")
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        supported = ", ".join(sorted(SUPPORTED_SUFFIXES))
        raise MinerUAdapterError(f"不支持的文件类型 {path.suffix!r}；支持：{supported}")
    return path


def default_out_dir(document: Path) -> Path:
    return REPO_ROOT / "outputs" / document.stem / "mineru"


def find_mineru_bin(value: str) -> str | None:
    candidate = Path(value).expanduser()
    if candidate.parent != Path(".") or candidate.is_absolute():
        return str(candidate.resolve()) if candidate.is_file() else None
    return shutil.which(value)


def mineru_version() -> str | None:
    try:
        return importlib.metadata.version("mineru")
    except importlib.metadata.PackageNotFoundError:
        return None


def existing_parent(path: Path) -> Path:
    candidate = path.expanduser().resolve()
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


def total_memory_bytes() -> int | None:
    meminfo = Path("/proc/meminfo")
    if not meminfo.is_file():
        return None
    for line in meminfo.read_text(encoding="utf-8").splitlines():
        if line.startswith("MemTotal:"):
            return int(line.split()[1]) * 1024
    return None


def doctor_report(out_dir: Path, mineru_bin: str) -> dict[str, Any]:
    executable = find_mineru_bin(mineru_bin)
    memory = total_memory_bytes()
    disk = shutil.disk_usage(existing_parent(out_dir))
    warnings: list[str] = []
    if executable is None:
        warnings.append("未找到 mineru CLI；安装可选依赖：uv sync --extra mineru-cpu")
    if memory is not None and memory < 16 * GIB:
        warnings.append("物理内存低于 MinerU 官方 pipeline 最低建议 16 GiB。")
    if disk.free < 20 * GIB:
        warnings.append("可用磁盘低于 MinerU 官方本地部署最低建议 20 GiB。")
    return {
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "cpu_count": os.cpu_count(),
        "memory_gib": round(memory / GIB, 2) if memory is not None else None,
        "disk_free_gib": round(disk.free / GIB, 2),
        "mineru_bin": executable,
        "mineru_version": mineru_version(),
        "backend": "pipeline",
        "warnings": warnings,
    }


def build_command(
    args: argparse.Namespace,
    document: Path,
    raw_dir: Path,
    executable: str,
) -> list[str]:
    command = [
        executable,
        "-p",
        str(document),
        "-o",
        str(raw_dir),
        "-b",
        "pipeline",
        "-m",
        args.method,
        "-f",
        str(args.formula).lower(),
        "-t",
        str(args.table).lower(),
    ]
    if args.lang:
        command.extend(["-l", args.lang])
    if args.start is not None:
        command.extend(["-s", str(args.start)])
    if args.end is not None:
        command.extend(["-e", str(args.end)])
    return command


def build_environment(
    args: argparse.Namespace,
) -> tuple[dict[str, str], dict[str, str]]:
    settings = {
        "CUDA_VISIBLE_DEVICES": "",
        "MINERU_INTRA_OP_NUM_THREADS": str(args.threads),
        "MINERU_INTER_OP_NUM_THREADS": str(args.inter_op_threads),
        "MINERU_PDF_RENDER_THREADS": str(args.render_threads),
        "MINERU_PROCESSING_WINDOW_SIZE": str(args.processing_window_size),
        "MINERU_API_MAX_CONCURRENT_REQUESTS": "1",
        "MINERU_TASK_RESULT_TIMEOUT_SECONDS": str(args.task_timeout),
    }
    if args.model_source != "auto":
        settings["MINERU_MODEL_SOURCE"] = args.model_source
    env = os.environ.copy()
    env.update(settings)
    return env, settings


def shell_join(command: list[str]) -> str:
    return " ".join(
        json.dumps(part) if any(char.isspace() for char in part) else part
        for part in command
    )


def prepare_for_parse(out_dir: Path, raw_dir: Path, *, overwrite: bool) -> None:
    if raw_dir.exists() and any(raw_dir.iterdir()):
        if not overwrite:
            raise MinerUAdapterError(
                f"raw 输出已存在：{raw_dir}；用 --skip-parse 复用，或 --overwrite 重跑。"
            )
        shutil.rmtree(raw_dir)
    if overwrite:
        image_dir = out_dir / "images"
        if image_dir.exists():
            shutil.rmtree(image_dir)
        for name in (
            "document.md",
            "elements.jsonl",
            "images.jsonl",
            "parse_summary.json",
        ):
            (out_dir / name).unlink(missing_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)


def preserve_source(document: Path, out_dir: Path) -> str:
    target = out_dir.parent / f"source{document.suffix.lower()}"
    target.parent.mkdir(parents=True, exist_ok=True)
    if document.resolve() != target.resolve():
        shutil.copy2(document, target)
    try:
        return Path(os.path.relpath(target.resolve(), out_dir.resolve())).as_posix()
    except ValueError:
        return str(target.resolve())


def relative_to(path: Path | None, base: Path) -> str | None:
    if path is None:
        return None
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def choose_artifact(
    candidates: list[Path],
    *,
    source_stem: str,
    preferred_parent: Path | None = None,
) -> Path | None:
    if not candidates:
        return None

    def score(path: Path) -> tuple[int, int, int, str]:
        stem_match = 0 if path.stem == source_stem else 1
        parent_match = (
            0 if preferred_parent is not None and path.parent == preferred_parent else 1
        )
        return (parent_match, stem_match, len(path.parts), path.as_posix())

    return min(candidates, key=score)


def discover_artifacts(raw_dir: Path, source_stem: str) -> dict[str, Path | None]:
    files = [path for path in raw_dir.rglob("*") if path.is_file()]
    markdown = choose_artifact(
        [path for path in files if path.suffix.lower() == ".md"],
        source_stem=source_stem,
    )
    preferred_parent = markdown.parent if markdown else None
    content_list = choose_artifact(
        [path for path in files if path.name.endswith("_content_list.json")],
        source_stem=f"{source_stem}_content_list",
        preferred_parent=preferred_parent,
    )
    middle = choose_artifact(
        [path for path in files if path.name.endswith("_middle.json")],
        source_stem=f"{source_stem}_middle",
        preferred_parent=preferred_parent,
    )
    model = choose_artifact(
        [path for path in files if path.name.endswith("_model.json")],
        source_stem=f"{source_stem}_model",
        preferred_parent=preferred_parent,
    )
    return {
        "markdown": markdown,
        "content_list": content_list,
        "middle": middle,
        "model": model,
    }


def load_content_list(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("content_list", [])
    if not isinstance(payload, list):
        raise MinerUAdapterError(f"MinerU content_list 不是数组：{path}")
    return [item for item in payload if isinstance(item, dict)]


def is_remote_reference(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https", "data"} or value.startswith("#")


def resolve_raw_asset(reference: str, bases: list[Path], raw_dir: Path) -> Path | None:
    value = unquote(reference.strip().strip("<>"))
    if not value or is_remote_reference(value):
        return None
    value = value.split("#", 1)[0].split("?", 1)[0]
    raw_root = raw_dir.resolve()
    candidates = (
        [Path(value)] if Path(value).is_absolute() else [base / value for base in bases]
    )
    for candidate in candidates:
        resolved = candidate.resolve()
        try:
            resolved.relative_to(raw_root)
        except ValueError:
            continue
        if resolved.is_file():
            return resolved
    return None


def canonical_image(
    source: Path,
    *,
    raw_dir: Path,
    image_dir: Path,
    copied: dict[Path, Path],
) -> Path:
    resolved = source.resolve()
    if resolved in copied:
        return copied[resolved]
    image_dir.mkdir(parents=True, exist_ok=True)
    target = image_dir / source.name
    if target.exists():
        if not filecmp.cmp(source, target, shallow=False):
            digest = hashlib.sha1(
                source.relative_to(raw_dir.resolve()).as_posix().encode("utf-8"),
                usedforsecurity=False,
            ).hexdigest()[:8]
            target = image_dir / f"{source.stem}_{digest}{source.suffix.lower()}"
    if not target.exists() or not filecmp.cmp(source, target, shallow=False):
        shutil.copy2(source, target)
    copied[resolved] = target
    return target


def block_text(block: dict[str, Any]) -> str:
    for key in ("text", "content", "table_body", "code_body", "equation"):
        value = block.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, list):
            text = "\n".join(str(item).strip() for item in value if str(item).strip())
            if text:
                return text
    for key in ("image_caption", "table_caption", "chart_caption"):
        value = block.get(key)
        if isinstance(value, list):
            text = "\n".join(str(item).strip() for item in value if str(item).strip())
            if text:
                return text
    return ""


def normalize_content_list(
    blocks: list[dict[str, Any]],
    *,
    raw_dir: Path,
    out_dir: Path,
    artifact_parent: Path,
    markdown_parent: Path | None,
    copied: dict[Path, Path],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], Counter[str]]:
    elements: list[dict[str, Any]] = []
    images: list[dict[str, Any]] = []
    type_counts: Counter[str] = Counter()
    bases = [artifact_parent]
    if markdown_parent and markdown_parent not in bases:
        bases.append(markdown_parent)
    bases.append(raw_dir)

    for index, block in enumerate(blocks):
        block_type = str(block.get("type") or "unknown")
        type_counts[block_type] += 1
        original_source = str(block.get("img_path") or "")
        canonical_source = ""
        source_asset = resolve_raw_asset(original_source, bases, raw_dir)
        if source_asset is not None:
            target = canonical_image(
                source_asset,
                raw_dir=raw_dir,
                image_dir=out_dir / "images",
                copied=copied,
            )
            canonical_source = relative_to(target, out_dir) or ""

        element = {
            "index": index,
            "type": block_type,
            "page": block.get("page_idx"),
            "content": block_text(block),
            "source": canonical_source or original_source or None,
            "bbox": block.get("bbox"),
            "raw_id": block.get("id"),
            "provider": "mineru",
            "provider_data": block,
        }
        elements.append(element)
        if original_source or block_type in {"image", "chart", "table"}:
            images.append(
                {
                    "index": index,
                    "type": block_type,
                    "page": block.get("page_idx"),
                    "source": original_source or None,
                    "image_path": canonical_source or None,
                    "bbox": block.get("bbox"),
                    "exists": bool(canonical_source),
                    "caption": block_text(block),
                }
            )
    return elements, images, type_counts


def rewrite_markdown_images(
    markdown: str,
    *,
    markdown_parent: Path,
    raw_dir: Path,
    out_dir: Path,
    copied: dict[Path, Path],
) -> str:
    def replace(match: re.Match[str]) -> str:
        target_value = match.group("target").strip("<>")
        source = resolve_raw_asset(target_value, [markdown_parent, raw_dir], raw_dir)
        if source is None:
            return match.group(0)
        target = canonical_image(
            source,
            raw_dir=raw_dir,
            image_dir=out_dir / "images",
            copied=copied,
        )
        return f"![{match.group('alt')}]({relative_to(target, out_dir)})"

    return IMAGE_LINK_RE.sub(replace, markdown)


def render_fallback_markdown(
    document: Path, elements: list[dict[str, Any]], *, parsed_at: str
) -> str:
    lines = [
        "---",
        "type: static-parse",
        f"source: {json.dumps(str(document), ensure_ascii=False)}",
        "parser: mineru",
        "backend: pipeline",
        f"parsed_at: {json.dumps(parsed_at)}",
        "---",
        "",
        f"# {document.stem}",
        "",
    ]
    current_page: Any = object()
    for element in elements:
        page = element.get("page")
        if page is not None and page != current_page:
            lines.extend(["", "---", "", f"## Page {page}", ""])
            current_page = page
        image_path = element.get("source")
        if image_path and element.get("type") in {"image", "chart", "table"}:
            lines.extend([f"![{element['type']} {element['index']}]({image_path})", ""])
        content = str(element.get("content") or "").strip()
        if content:
            lines.extend([content, ""])
    return "\n".join(lines).rstrip() + "\n"


def middle_version(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return payload.get("_version_name") if isinstance(payload, dict) else None


def normalize_outputs(
    document: Path,
    out_dir: Path,
    raw_dir: Path,
    *,
    parsed_at: str,
) -> dict[str, Any]:
    artifacts = discover_artifacts(raw_dir, document.stem)
    markdown_path = artifacts["markdown"]
    content_list_path = artifacts["content_list"]
    if markdown_path is None and content_list_path is None:
        raise MinerUAdapterError(
            f"在 {raw_dir} 中未找到 MinerU Markdown 或 *_content_list.json。"
        )

    blocks = load_content_list(content_list_path)
    copied: dict[Path, Path] = {}
    artifact_parent = (
        content_list_path.parent
        if content_list_path is not None
        else markdown_path.parent  # type: ignore[union-attr]
    )
    elements, images, type_counts = normalize_content_list(
        blocks,
        raw_dir=raw_dir,
        out_dir=out_dir,
        artifact_parent=artifact_parent,
        markdown_parent=markdown_path.parent if markdown_path else None,
        copied=copied,
    )
    write_jsonl(out_dir / "elements.jsonl", elements)
    write_jsonl(out_dir / "images.jsonl", images)

    if markdown_path is not None:
        provider_markdown = markdown_path.read_text(encoding="utf-8", errors="replace")
        body = rewrite_markdown_images(
            provider_markdown,
            markdown_parent=markdown_path.parent,
            raw_dir=raw_dir,
            out_dir=out_dir,
            copied=copied,
        )
        frontmatter = [
            "---",
            "type: static-parse",
            f"source: {json.dumps(str(document), ensure_ascii=False)}",
            "parser: mineru",
            "backend: pipeline",
            f"parsed_at: {json.dumps(parsed_at)}",
            "---",
            "",
        ]
        document_markdown = "\n".join(frontmatter) + body.lstrip()
        if not document_markdown.endswith("\n"):
            document_markdown += "\n"
    else:
        document_markdown = render_fallback_markdown(
            document, elements, parsed_at=parsed_at
        )
    (out_dir / "document.md").write_text(document_markdown, encoding="utf-8")

    return {
        "raw_output_dir": relative_to(raw_dir, out_dir),
        "mineru_markdown": relative_to(markdown_path, out_dir),
        "mineru_content_list": relative_to(content_list_path, out_dir),
        "mineru_middle_json": relative_to(artifacts["middle"], out_dir),
        "mineru_model_json": relative_to(artifacts["model"], out_dir),
        "mineru_output_version": middle_version(artifacts["middle"]),
        "mineru_result_method": (
            markdown_path.parent.name
            if markdown_path is not None
            and markdown_path.parent.name in {"auto", "txt", "ocr"}
            else None
        ),
        "document_md": "document.md",
        "elements_jsonl": "elements.jsonl",
        "images_jsonl": "images.jsonl",
        "element_count": len(elements),
        "image_element_count": len(images),
        "copied_image_count": len(copied),
        "text_chars": sum(len(str(item.get("content") or "")) for item in elements),
        "type_counts": dict(type_counts.most_common()),
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    validate_args(args)
    document = resolve_document(args.document)
    out_dir = (args.out or default_out_dir(document)).expanduser().resolve()
    raw_dir = out_dir / "raw"
    report = doctor_report(out_dir, args.mineru_bin)
    executable = report["mineru_bin"] or args.mineru_bin
    command = build_command(args, document, raw_dir, executable)
    env, cpu_settings = build_environment(args)

    print(f"输入文档 : {document}")
    print(f"输出目录 : {out_dir}")
    print("后端     : pipeline (CPU)")
    print(f"资源设置 : {json.dumps(cpu_settings, ensure_ascii=False)}")
    if args.skip_parse:
        print(f"复用 raw : {raw_dir}")
    else:
        print(f"$ {shell_join(command)}")
    for warning in report["warnings"]:
        print(f"警告：{warning}")

    if args.dry_run:
        return {
            "dry_run": True,
            "source": str(document),
            "output_dir": str(out_dir),
            "command": command,
            "environment": cpu_settings,
            "doctor": report,
        }

    out_dir.mkdir(parents=True, exist_ok=True)
    previous_summary: dict[str, Any] = {}
    previous_summary_path = out_dir / "parse_summary.json"
    if args.skip_parse and previous_summary_path.is_file():
        try:
            previous_summary = json.loads(
                previous_summary_path.read_text(encoding="utf-8")
            )
        except json.JSONDecodeError:
            previous_summary = {}
    started = time.monotonic()
    if args.skip_parse:
        if not raw_dir.is_dir():
            raise MinerUAdapterError(f"--skip-parse 需要已有目录：{raw_dir}")
    else:
        if report["mineru_bin"] is None:
            raise MinerUAdapterError(
                "未找到 mineru CLI。请先执行：uv sync --extra mineru-cpu"
            )
        prepare_for_parse(out_dir, raw_dir, overwrite=args.overwrite)
        try:
            subprocess.run(command, check=True, env=env)
        except subprocess.CalledProcessError as exc:
            raise MinerUAdapterError(
                f"MinerU 解析失败，exit_code={exc.returncode}"
            ) from exc

    normalized_at = datetime.now(timezone.utc).isoformat()
    parsed_at = (
        str(previous_summary.get("parsed_at") or normalized_at)
        if args.skip_parse
        else normalized_at
    )
    preserved_source = (
        None if args.no_copy_source else preserve_source(document, out_dir)
    )
    normalized = normalize_outputs(
        document,
        out_dir,
        raw_dir,
        parsed_at=parsed_at,
    )
    summary = {
        "source_document": str(document),
        "preserved_source": preserved_source,
        "output_dir": str(out_dir),
        "parsed_at": parsed_at,
        "provider": "mineru",
        "backend": "pipeline",
        "method": (
            normalized.get("mineru_result_method")
            or previous_summary.get("method")
            or args.method
        ),
        "formula_enabled": previous_summary.get("formula_enabled", args.formula),
        "table_enabled": previous_summary.get("table_enabled", args.table),
        "command": previous_summary.get("command") if args.skip_parse else command,
        "cpu_settings": (
            previous_summary.get("cpu_settings", cpu_settings)
            if args.skip_parse
            else cpu_settings
        ),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "parse_elapsed_seconds": (
            previous_summary.get(
                "parse_elapsed_seconds", previous_summary.get("elapsed_seconds")
            )
            if args.skip_parse
            else round(time.monotonic() - started, 3)
        ),
        "normalization_only": bool(args.skip_parse),
        "normalized_at": normalized_at,
        "mineru_package_version": mineru_version(),
        "doctor": report,
        **normalized,
    }
    (out_dir / "parse_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    try:
        validate_args(args)
        out_probe = (
            args.out.expanduser().resolve() if args.out else REPO_ROOT / "outputs"
        )
        if args.doctor:
            print(
                json.dumps(
                    doctor_report(out_probe, args.mineru_bin),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return
        summary = run(args)
    except MinerUAdapterError as exc:
        raise SystemExit(f"Error: {exc}") from exc

    if args.dry_run:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return
    print(
        "完成："
        f"{summary['element_count']} 个元素，"
        f"{summary['image_element_count']} 个图像/表格元素，"
        f"{summary['elapsed_seconds']} 秒。"
    )
    print(f"Markdown：{Path(summary['output_dir']) / 'document.md'}")
    print(f"摘要：{Path(summary['output_dir']) / 'parse_summary.json'}")


if __name__ == "__main__":
    main(sys.argv[1:])
