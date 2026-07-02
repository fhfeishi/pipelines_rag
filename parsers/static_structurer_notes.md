# static_structurer.py notes

`static_structurer.py` 是 `parsers/` 的轻量总入口，用来把“文档、网页、音视频、文本”等来源先当作静态信息处理，并统一落到 `outputs/<source-stem>/` 结构包里。

当前边界：

- 不做动态视频画面理解。
- 不把各工具揉成一个大实现；具体能力继续留在 `redox_*`、`rewebpage_*`、`reaudio_*` 等脚本。
- 总入口只负责检测来源类型、选择工具、分配输出目录、调用子脚本、写 `static_parse_manifest.json`。
- 如果子脚本失败，总入口仍会尽量写出 manifest，然后以非零状态退出，方便回看失败命令和输出目录。

常用命令：

```bash
python -m parsers.static_structurer --list-tools
python -m parsers.static_structurer path/to/file.pdf
python -m parsers.static_structurer "https://example.com/article" --parse-page-pdf
python -m parsers.static_structurer video.mp4 --kind media -- --max-seconds 60
```

工具默认选择：

| kind | 默认 tool |
|------|-----------|
| `pdf` | `opendataloader_pdf` |
| `webpage` | `crawl4ai` |
| `media` | `dashscope_asr` |
| `text` | `copy_text` |

使用经验继续积累：

- PDF 解析质量、图片抽取、阅读顺序问题：写到 `redox_notes.md`。
- 网页抓取、代理、截图 PDF、Firecrawl/crawl4ai 对比：写到 `rewebpage_notes.md`。
- 音视频 ASR、yt-dlp、ffmpeg、DashScope 失败模式：写到 `reaudio_notes.md`。
- 总入口是否好用、目录契约是否顺手：写到本文件。
