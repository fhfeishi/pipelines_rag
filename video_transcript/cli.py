"""CLI entry — minimal usage: transcript audio.m4a

Full pipeline:
    media -> DashScope ASR -> DeepSeek polish + title -> {stem}.transcript.txt
"""

from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path

from loguru import logger

from configs.config import settings
from video_transcript.media import supported_suffixes
from video_transcript.pipeline import RunResult, prepare_only, run
from video_transcript.polish import PolishError
from video_transcript.transcribe.dashscope_asr import DashscopeAsrError
from video_transcript.media import MediaError

EPILOG = f"""
Supported formats: {", ".join(supported_suffixes())}

Examples:
  transcript audio.m4a
  transcript /mnt/d/download/resources/proxy-tun.m4a
  transcript audio.m4a -o tmp/outs -l zh
  transcript --batch "tmp/raws/*.m4a" -o tmp/outs --flat
  transcript audio.m4a --no-polish
  transcript video.mp4 --prepare-only   # 仅测试抽轨/转码，不调用 API
""".strip()


def _parse_formats(raw: str) -> list[str]:
    formats = [part.strip().lower() for part in raw.split(",") if part.strip()]
    allowed = {"txt", "json", "srt"}
    unknown = [f for f in formats if f not in allowed]
    if unknown:
        raise argparse.ArgumentTypeError(f"Unknown format(s): {', '.join(unknown)}")
    return formats or ["txt", "json"]


def _resolve_out_dir(input_path: Path, out_arg: Path | None, *, flat: bool) -> Path | None:
    if out_arg is None:
        return None
    if flat:
        return out_arg
    if input_path.stem in out_arg.name:
        return out_arg
    return out_arg / input_path.stem


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="transcript",
        description="音视频转文案：听写 → 润色 → 标题 → 可直接使用的正文",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=EPILOG,
    )
    parser.add_argument("inputs", nargs="*", metavar="FILE", help="音频/视频文件路径")
    parser.add_argument("-o", "--out", type=Path, metavar="DIR", help="输出目录")
    parser.add_argument("-l", "--language", default="zh", help="语言：zh / en / auto（默认 zh）")
    parser.add_argument("--flat", action="store_true", help="批量时所有结果写入同一目录")
    parser.add_argument("--batch", action="store_true", help="将参数视为 glob 模式")
    parser.add_argument(
        "-f", "--format",
        type=_parse_formats,
        default=_parse_formats("txt,json"),
        help="输出格式：txt,json,srt（默认 txt,json）",
    )
    parser.add_argument("--keep-audio", action="store_true", help="保留提取的 16k 音频")
    parser.add_argument("--no-polish", action="store_true", help="仅听写，不润色")
    parser.add_argument("--no-title", action="store_true", help="不生成标题")
    parser.add_argument("--force", action="store_true", help="忽略缓存，强制重跑")
    parser.add_argument("--no-resume", action="store_true", help="禁用缓存")
    parser.add_argument("--polish-model", default=settings.polish_model, help="润色模型")
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="仅探测媒体并做无损抽轨+ASR 标准化，不调用云端 API",
    )
    parser.add_argument(
        "--max-seconds",
        type=float,
        metavar="SEC",
        help="只处理前 N 秒（测试/省 token 用）",
    )
    return parser


def _print_prepare(prep, *, input_path: Path, out_dir: Path | None) -> None:
    info = prep.info
    print(
        f"✓ {input_path.name}\n"
        f"  类型: {info.kind} | 时长: {info.duration_ms/1000:.1f}s\n"
        f"  容器: {info.format_name}\n"
        f"  音频: {info.audio_codec} {info.audio_sample_rate}Hz {info.audio_channels}ch"
        + (f" | 视频: {info.video_codec}" if info.video_codec else "")
        + "\n"
        f"  抽轨: {prep.extract_mode} -> {prep.extracted_path}\n"
        f"  ASR:  {prep.asr_mode} -> {prep.asr_path}\n"
        f"  步骤: {' | '.join(prep.steps)}"
    )


def _collect_inputs(args: argparse.Namespace) -> list[Path]:
    if not args.inputs:
        print("用法: transcript <音频或视频文件>\n")
        print(EPILOG)
        raise SystemExit(2)
    if args.batch:
        paths = [Path(p) for pattern in args.inputs for p in sorted(glob.glob(pattern))]
    else:
        paths = [Path(p) for p in args.inputs]
    if not paths:
        raise SystemExit("没有匹配到任何文件。")
    return paths


def _print_result(result: RunResult) -> None:
    title_part = f'"{result.title}" ' if result.title else ""
    print(
        f"✓ {Path(result.input_path).name}\n"
        f"  标题: {result.title or '(无)'}\n"
        f"  正文: {result.deliverable_path} ({result.text_chars} 字)\n"
        f"  耗时: {result.elapsed_s:.1f}s | 缓存: asr={result.asr_cached} polish={result.polish_cached}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    language = None if args.language == "auto" else args.language
    flat = args.flat or (args.batch and args.out is not None)

    try:
        if args.prepare_only:
            for input_path in _collect_inputs(args):
                out_dir = _resolve_out_dir(input_path, args.out, flat=flat)
                prep = prepare_only(input_path, out_dir=out_dir, max_seconds=args.max_seconds)
                _print_prepare(prep, input_path=input_path, out_dir=out_dir)
            return 0

        results: list[RunResult] = []
        for input_path in _collect_inputs(args):
            out_dir = _resolve_out_dir(input_path, args.out, flat=flat)
            results.append(
                run(
                    input_path,
                    out_dir=out_dir,
                    language=language,
                    formats=args.format,
                    polish=not args.no_polish,
                    title=not args.no_title,
                    keep_audio=args.keep_audio,
                    resume=not args.no_resume,
                    force=args.force,
                    polish_model=args.polish_model,
                    max_seconds=args.max_seconds,
                )
            )
    except (MediaError, DashscopeAsrError, PolishError, ValueError, FileNotFoundError) as exc:
        logger.error("{}", exc)
        return 1

    for result in results:
        _print_result(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
