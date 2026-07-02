> `parsers` 工具箱说明、启动文档
reaudio_xx: 处理 音视频（音频）  --> 结构化和润色之后的`markdown`
redox_xx:处理 文档  --> 图文编排的`markdown`
rewebpages_xx:利用AI工具获取网页`url`的信息  --> 得到 dpf、json、markdown，如果可以尽量还是图文编排的。
rescrapy_xx:利用AI网络爬虫工具获取网页`url`的信息  --> 得到 dpf、json、markdown，如果可以尽量还是图文编排的。
> 输出还是尽量保存到跟输入 `stem` 同名的 `outputs/ste_subdir/` 下
> 对应的notes文档就当作一个小单元/小功能的简单汇总，因为还可能出现增补的情况

## 汇总入口：静态解析 / 静态结构化 / 静态重构

统一入口命名为：

```bash
python -m parsers.static_structurer
```

这个名字比 `parser` 更宽：它不只负责“读取”，还负责把来源数据整理成稳定的静态结构包。当前不处理动态视频画面理解；音视频先按“抽音频 -> ASR -> Markdown/JSON”作为静态信息处理。

推荐用法：

```bash
# PDF -> outputs/<stem>/opendataloader_pdf/
python -m parsers.static_structurer path/to/file.pdf

# 网页 -> outputs/<url-stem>/crawl4ai/...，可选继续解析 page.pdf
python -m parsers.static_structurer "https://example.com/article" --parse-page-pdf

# 音频/视频 -> outputs/<stem>/reaudio_dashscope/
python -m parsers.static_structurer path/to/video.mp4 --kind media -- --max-seconds 60

# 查看已登记工具
python -m parsers.static_structurer --list-tools
```

当前 registry：

| tool | 来源类型 | 下游脚本 | notes |
|------|----------|----------|-------|
| `opendataloader_pdf` | PDF | `parsers.redox_opendataloaderpdf` | `redox_notes.md` |
| `crawl4ai` | webpage URL | `parsers.rewebpage_craw` | `rewebpage_notes.md` |
| `firecrawl` | webpage URL | `parsers.rewebpage_firecrawl` | `rewebpage_notes.md` |
| `dashscope_asr` | audio/video file or URL | `parsers.reaudio_dashscope` | `reaudio_notes.md` |
| `copy_text` | md/txt/html | builtin copy + `document.md` | `static_structurer_notes.md` |

统一输出包尽量遵循：

```text
outputs/<source-stem>/
├── static_parse_manifest.json
├── source.pdf / source.md / source.txt / ...
├── opendataloader_pdf/
│   ├── <layout>.json
│   ├── images/
│   ├── elements.jsonl
│   ├── images.jsonl
│   ├── parse_summary.json
│   └── document.md
├── crawl4ai/...
├── firecrawl/...
└── reaudio_dashscope/...
```

`static_structurer.py` 只做轻编排：选择工具、分配目录、写 manifest；具体工具的参数、依赖、失败经验继续沉淀在各自脚本和 notes 中。

## 当前可测的单工具接口

### PDF：`redox_opendataloaderpdf.py`

```bash
python -m parsers.redox_opendataloaderpdf /mnt/e/static_docs/sources/getting-the-most-out-of-codex.pdf \
  --out outputs/static_docs/getting-the-most-out-of-codex/opendataloader_pdf \
  --quiet
```

用于验证：Java/opendataloader 可用、`source.pdf` 保存、layout JSON、`elements.jsonl`、`images.jsonl`、`document.md` 和相对图片路径。

### 音视频：`reaudio_dashscope.py`

```bash
python -m parsers.reaudio_dashscope /mnt/e/static_docs/sources/proxy-tun.m4a \
  --output-dir outputs/static_docs/proxy-tun/reaudio_dashscope \
  --formats md,json,srt \
  --max-seconds 30 \
  --no-polish \
  --dry-run
```

用于验证：本地 m4a/mp4 输入识别、ffmpeg/yt-dlp 依赖位置、输出路径规划、DashScope key 状态；`--dry-run` 不调用 API、不写输出文件。

## 当前优先级：redox 文档解析工具

目标：把网页快照、PDF 或其它文档源整理成一个可检查、可接入 RAG 的图文 Markdown 包。

推荐输出契约：

```text
outputs/<source-stem>/
├── source.pdf                 # 原始或转换后的 PDF
├── opendataloader_pdf/
│   ├── <layout>.json          # parser 原始 layout JSON
│   ├── images/                # 外链图片
│   ├── elements.jsonl         # 阅读顺序元素清单
│   ├── images.jsonl           # 图片清单、页码、bbox、路径
│   ├── parse_summary.json     # 解析摘要
│   └── document.md            # 图文编排 Markdown
```

`document.md` 应优先满足：

- 保留 parser 阅读顺序；必要时支持 bbox 重排对照。
- 图片使用相对路径引用，方便 Obsidian、GitHub 或前端预览。
- 每张图片附近保留页码、bbox、原始 image source 等 metadata。
- Markdown 是人工检查和后续 caption ingest 的桥梁，不直接替代 `rag_pdfs` 的 JSONL/chunk 产物。

当前入口：

```bash
python -m parsers.redox_opendataloaderpdf path/to/file.pdf
python -m parsers.static_structurer path/to/file.pdf
```

已完成第一版：

- 从 `elements.jsonl` 同源元素流生成图文编排 `document.md`。
- 在 Markdown 图片块中保留相对路径、页码、bbox、原始 image source。

下一步优先补齐：

1. 继续验证 `document.md` 的阅读顺序和图文邻近关系。
2. 评估 `flat` vs `bbox` 阅读顺序在不同 PDF 上的差异。
3. 让 `rag_pdfs` caption/evidence citation 复用 `document.md` 或同源 metadata。
