"""opendataloader-pdf 解析脚本：PDF -> layout JSON / Markdown / 图片清单。

典型输入是 `parsers.script_craw` 生成的网页快照 PDF：

    outputs/webpages/<page-slug>/page.pdf

用法：
    python -m parsers.script_oppdf
    python -m parsers.script_oppdf outputs/webpages/qwen.ai_blog_id_qwen-agentworld/page.pdf
    python -m parsers.script_oppdf outputs/webpages/qwen.ai_blog_id_qwen-agentworld/
"""

from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rag_pdfs.pdf_layout import is_image, is_text, reorder_elements_by_bbox
from rag_pdfs.pdf_parser import (
    load_elements,
    path_for_storage,
    require_java,
    resolve_image_path,
    run_opendataloader,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WEBPAGE_DIR = REPO_ROOT / "outputs" / "webpages"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="用 opendataloader-pdf 解析 PDF，输出 layout JSON、Markdown、图片和元素清单。",
    )
    parser.add_argument(
        "pdf",
        nargs="?",
        type=Path,
        help=(
            "PDF 文件或包含 page.pdf 的目录；省略时自动选择 outputs/webpages 下最新的 page.pdf。"
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="解析输出目录；默认写到 PDF 同级目录的 opendataloader_pdf/。",
    )
    parser.add_argument(
        "--skip-parse",
        action="store_true",
        help="复用 --out 中已有 layout JSON，不重新调用 opendataloader-pdf。",
    )
    parser.add_argument(
        "--reading-order",
        choices=["flat", "bbox"],
        default="flat",
        help="元素顺序：flat 保留 opendataloader flatten 顺序；bbox 按页和 bbox 重排。",
    )
    parser.add_argument(
        "--pages",
        help="只解析指定页，透传给 opendataloader-pdf，例如 1-3 或 1,3,5。",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="降低 opendataloader-pdf 输出噪声。",
    )
    return parser.parse_args()


def latest_page_pdf(root: Path = DEFAULT_WEBPAGE_DIR) -> Path:
    candidates = sorted(root.glob("*/page.pdf"), key=lambda p: p.stat().st_mtime)
    if not candidates:
        raise FileNotFoundError(
            f"未找到 {root}/<slug>/page.pdf；请先运行 python -m parsers.script_craw。"
        )
    return candidates[-1].resolve()


def resolve_pdf_arg(value: Path | None) -> Path:
    if value is None:
        return latest_page_pdf()
    path = value.expanduser()
    if path.is_dir():
        path = path / "page.pdf"
    path = path.resolve()
    if not path.exists():
        raise FileNotFoundError(path)
    if path.suffix.lower() != ".pdf":
        raise ValueError(f"输入必须是 PDF 或含 page.pdf 的目录：{path}")
    return path


def find_existing_opendataloader_json(out_dir: Path) -> Path:
    ignored = {
        "summary.json",
        "parse_summary.json",
        "elements.json",
        "images.json",
        "elements.jsonl",
        "images.jsonl",
    }
    candidates = sorted(
        [
            path
            for path in out_dir.glob("*.json")
            if path.name not in ignored and not path.name.endswith(".schema.json")
        ],
        key=lambda p: p.stat().st_mtime,
    )
    if not candidates:
        raise RuntimeError(f"--skip-parse set, but no layout JSON found in {out_dir}")
    return candidates[-1]


def run_opendataloader_with_pages(
    pdf_path: Path,
    out_dir: Path,
    image_dir: Path,
    *,
    pages: str | None,
    quiet: bool,
) -> Path:
    """本地轻包装：支持 pages/quiet，同时保持 rag_pdfs.pdf_parser 的默认参数。"""
    try:
        import opendataloader_pdf
    except ImportError as exc:
        raise RuntimeError(
            "Missing package: opendataloader-pdf. Install it with:\n"
            "    uv add opendataloader-pdf"
        ) from exc

    out_dir.mkdir(parents=True, exist_ok=True)
    image_dir.mkdir(parents=True, exist_ok=True)

    opendataloader_pdf.convert(
        input_path=[str(pdf_path)],
        output_dir=str(out_dir),
        format="json,markdown",
        image_output="external",
        image_format="png",
        image_dir=str(image_dir),
        pages=pages,
        quiet=quiet,
    )

    json_files = sorted(out_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
    if not json_files:
        json_files = sorted(out_dir.rglob("*.json"), key=lambda p: p.stat().st_mtime)
    if not json_files:
        raise RuntimeError(f"No JSON output found in {out_dir}")
    return json_files[-1]


def as_jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def export_elements(json_path: Path, out_dir: Path, image_dir: Path, *, reading_order: str) -> dict[str, Any]:
    elements = load_elements(json_path)
    if reading_order == "bbox":
        elements = reorder_elements_by_bbox(elements)

    element_rows: list[dict[str, Any]] = []
    image_rows: list[dict[str, Any]] = []
    type_counts: Counter[str] = Counter()

    for element in elements:
        type_counts[element.type or "<empty>"] += 1
        row = {
            "index": element.index,
            "type": element.type,
            "page": element.page,
            "content": element.content,
            "source": element.source,
            "bbox": as_jsonable(element.bbox),
            "raw_id": element.raw_id,
        }
        element_rows.append(row)

        if is_image(element):
            image_path = resolve_image_path(element.source or "", json_path, image_dir)
            image_rows.append(
                {
                    "index": element.index,
                    "page": element.page,
                    "source": element.source,
                    "image_path": path_for_storage(image_path, out_dir),
                    "bbox": as_jsonable(element.bbox),
                    "exists": bool(image_path and image_path.exists()),
                }
            )

    write_jsonl(out_dir / "elements.jsonl", element_rows)
    write_jsonl(out_dir / "images.jsonl", image_rows)

    text_chars = sum(len(element.content) for element in elements if is_text(element))
    return {
        "layout_json": path_for_storage(json_path, out_dir),
        "elements_jsonl": "elements.jsonl",
        "images_jsonl": "images.jsonl",
        "element_count": len(elements),
        "text_element_count": sum(1 for element in elements if is_text(element)),
        "image_element_count": len(image_rows),
        "text_chars": text_chars,
        "type_counts": dict(type_counts.most_common()),
        "reading_order": reading_order,
    }


def main() -> None:
    args = parse_args()
    pdf_path = resolve_pdf_arg(args.pdf)
    out_dir = (args.out or (pdf_path.parent / "opendataloader_pdf")).expanduser().resolve()
    image_dir = out_dir / "images"

    print(f"输入 PDF : {pdf_path}")
    print(f"输出目录 : {out_dir}")

    require_java()

    if args.skip_parse:
        json_path = find_existing_opendataloader_json(out_dir)
        print(f"复用 JSON: {json_path}")
    elif args.pages or args.quiet:
        json_path = run_opendataloader_with_pages(
            pdf_path,
            out_dir,
            image_dir,
            pages=args.pages,
            quiet=args.quiet,
        )
    else:
        # 主线 parser 的默认实现，保持与 rag_pdfs.ingest_img 一致。
        json_path = run_opendataloader(pdf_path, out_dir, image_dir)

    summary = {
        "source_pdf": str(pdf_path),
        "output_dir": str(out_dir),
        "image_dir": path_for_storage(image_dir, out_dir),
        "parsed_at": datetime.now(timezone.utc).isoformat(),
        "opendataloader_json": path_for_storage(json_path, out_dir),
        "java": shutil.which("java"),
    }
    summary.update(
        export_elements(
            json_path,
            out_dir,
            image_dir,
            reading_order=args.reading_order,
        )
    )
    (out_dir / "parse_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(
        "完成："
        f"{summary['element_count']} 个元素，"
        f"{summary['text_element_count']} 个文本元素，"
        f"{summary['image_element_count']} 个图片元素。"
    )
    print(f"清单：{out_dir / 'elements.jsonl'}")
    print(f"图片：{out_dir / 'images.jsonl'}")
    print(f"摘要：{out_dir / 'parse_summary.json'}")


if __name__ == "__main__":
    main()
