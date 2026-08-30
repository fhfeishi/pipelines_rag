# parsers 工具箱

> 仓库分层见根目录 [`strata.md`](../strata.md)。`parsers` 负责 **Layer 0–1**：来源采集与静态结构化。
> `rag_pdfs` 负责 Layer 2–3，**不应**反向被 parsers 依赖。

## 命名约定

| 前缀 | 职责 | 产出 |
|------|------|------|
| `redox_*` | 文档/PDF 解析 | 图文编排 `document.md` + layout 资产 |
| `rewebpage_*` | 网页 URL 抓取 | PDF / JSON / Markdown |
| `reaudio_*` | 音视频（Cloud ASR） | 转写 Markdown / JSON / SRT |
| `rescrapy_*` | 旧的 selector/定向抽取实验 | 仅保留未实现的 Parsel 调研位 |

输出尽量放在 `outputs/<source-stem>/<tool_subdir>/`；各工具的 notes 文件记录依赖与踩坑。

## 架构位置

```text
Layer 0   rewebpage_* / reaudio_* / 本地文件
              ↓
Layer 1   redox_* / static_structurer  →  StaticParsePackage
              ↓
Layer 2   rag_pdfs.ingest_img（filter / caption / chunk）
```

**依赖倒置已修复（2026-07-07）**：parse 核心已下沉至 `parsers/document/`：

| 模块 | 内容 |
|------|------|
| `document/layout.py` | `LayoutElement`、阅读顺序、section/context、bbox 工具（原 `rag_pdfs/pdf_layout.py`） |
| `document/opendataloader.py` | `run_opendataloader`（已合并 pages/quiet 变体）、flatten、`find_existing_layout_json`（已合并两份实现）、路径工具 |
| `document/package.py` | `write_jsonl` / `read_jsonl` / `as_jsonable` |

`redox_opendataloaderpdf` 只 import `parsers.document.*`；`rag_pdfs/pdf_layout.py` /
`pdf_parser.py` 降级为兼容 shim（RAG 专属的 `ImageCaption` / `SkippedImage` 留在 rag_pdfs）。
**约束**：`parsers/document/` 禁止 import `rag_pdfs`。

## 工具完成度与处置（2026-07-07 盘点）

| 模块 | 完成度 | 处置 |
|------|--------|------|
| `redox_opendataloaderpdf` | ~95%，完整实现契约 | 默认主力；CPU 友好、依赖较轻 |
| `redox_mineru` | ~90%，CPU pipeline 真实 smoke 通过 | 高质量备选；依赖/模型较重，先按页试跑 |
| `static_structurer` | ~80% | 保持轻编排；修 `--parse-page-pdf` 的 slug 路径查找 |
| `rewebpage_common` | ~95% | 三后端共享 snapshot 模型、URL/链接/词数归一化、扁平单 URL 输出与 bundle writer |
| `rewebpage_craw` | ~95% | crawl4ai 0.9.2 真实 smoke 通过；Markdown/HTML/PNG/PDF/MHTML 可选，已取消多余 slug 层 |
| `rewebpage_firecrawl` | ~90% | v2 SDK/截图格式已校准，probe 通过；缺 API key 未跑真实 scrape |
| `rewebpage_scrapling` | ~90% | HTTP 真实 smoke 通过；支持 http/dynamic/stealthy + CSS selector；当前不产视觉 PDF |
| `reaudio_dashscope` | ~85% | 可用；缺 manifest；非图片 RAG 主线，按 `reaudio_notes.md` P0–P3 慢速推进 |
| `script_lm` | ~5% 草稿 | 成型前不算工具 |
| `redox_liteparse` / `redox_unlimitedocr` | 0%，仅注释占位 | **删除文件或实现最小 CLI 二选一**；调研结论留在 `redox_notes.md` |
| `rescrapy_parsel` | 0%，空文件 | 删除或实现最小 CLI；Scrapling 已迁入 `rewebpage_scrapling` |

**契约现实差距**：PDF 的 StaticParsePackage 已有 `redox_opendataloaderpdf` 和
`redox_mineru` 两个实现，顶层 manifest 由 `static_structurer` 写入；两者当前各自做一次
规范化，尚未统一收敛到 `document.write_package`。
网页/音视频工具是 Layer 0 采集，产物（`page.{json,md,pdf}`、`<stem>.{md,json,srt}`）**不是**契约包；
三个网页后端已统一 `page.json.snapshot` 字段与单 URL 扁平输出（直接写入 `<tool_subdir>/`）；
当前视觉路径是 `page.pdf` → opendataloader：crawl4ai 原生打印 PDF 可能保留文字层，
Firecrawl 截图 PDF 通常得到纯图片元素；文字主路径仍待 `page.md → 元素流` adapter（见 `rewebpage_notes.md`）。
本机 `outputs/` 现存产物多为 2026-06 旧 CLI 遗留（缺 manifest / source.pdf / document.md），
验证契约时应用 `static_structurer` 重跑生成新包，勿以旧产物为准。

## StaticParsePackage 契约

Layer 1 输出必须足够让人工检查，并可供 RAG ingest 复用：

```text
outputs/<source-stem>/
├── static_parse_manifest.json   # static_structurer 写入
├── source.pdf                   # 或 source.md / 转写源
└── <tool_subdir>/               # 如 opendataloader_pdf/
    ├── *.json
    ├── images/
    ├── elements.jsonl
    ├── images.jsonl
    ├── parse_summary.json
    └── document.md
```

`document.md` 要求：阅读顺序、相对图片路径、页码/bbox/source metadata。

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
| `mineru` | PDF | `parsers.redox_mineru`（CPU pipeline） | `redox_notes.md` |
| `crawl4ai` | webpage URL | `parsers.rewebpage_craw` | `rewebpage_notes.md` |
| `firecrawl` | webpage URL | `parsers.rewebpage_firecrawl` | `rewebpage_notes.md` |
| `scrapling` | webpage URL | `parsers.rewebpage_scrapling` | `rewebpage_notes.md` |
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
├── mineru/
│   ├── raw/
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

### PDF（纯 CPU 备选）：`redox_mineru.py`

```bash
uv sync --extra mineru-cpu
uv run -m parsers.redox_mineru --doctor
uv run -m parsers.static_structurer inputs/example.pdf --tool mineru -- \
  --model-source modelscope --start 0 --end 0 --method txt \
  --no-formula --no-table
```

固定使用 MinerU `pipeline` 后端；保存 `raw/` 并从 `content_list` 规范化为同类
`document.md / elements.jsonl / images.jsonl / parse_summary.json`。本机 CPU smoke 与
资源/依赖注意事项见 `redox_notes.md`。

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

下一步优先补齐（顺序即优先级；设计定稿见根 `strata.md` §3.2）：

1. ~~**下沉 parse 核心**~~：✅ 2026-07-07 完成，见上文 `parsers/document/` 表。
2. **StaticParsePackage 接口落地**（根 strata §8 第 3 步）：
   - `package.py` 加 `StaticParsePackage` 模型 + `load_package` / `write_package`；
   - `write_image_aware_markdown` 从 redox 下沉到 `document/markdown.py`；
   - 元素流加 **`section_path`** 主坐标（heading 栈，替代单一 `section_title`；
     page/bbox 降级为物理 metadata）；
   - OKF 兼容：`document.md` 加 frontmatter（type/title/source/timestamp）、包根生成 `index.md`；
   - `rag_pdfs.ingest_img --parse-dir` 用 `load_package` 消费包。
3. **`markdown → 元素流` adapter**（`document/markdown.py`）：网页改走统一的
   `rewebpage_* page.md → 元素流`（默认 crawl4ai，也可用 Firecrawl/Scrapling 交叉验证；
   替代截图 PDF 的纯图片路径），存量 markdown 同路径入库。
4. 继续验证 `document.md` 的阅读顺序和图文邻近关系；评估 `flat` vs `bbox` 在不同 PDF 上的差异。
5. 让 `rag_pdfs` caption/evidence citation 复用 `document.md` 或同源 metadata。
6. 清理剩余占位模块（liteparse / unlimitedocr / rescrapy_parsel）：删除或实现最小 CLI。
