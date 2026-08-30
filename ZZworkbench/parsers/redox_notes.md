

### `opendataloader-pdf`   --> `redox_opendataloaderpdf.py`
特点：`CPU`友好



### `mineru` → `redox_mineru.py`

定位：本地文档解析的高质量备选后端。当前仓库只实现并验证了**纯 CPU 本地路径**；
云端 MinerU API 暂未接入，避免把本地输出契约和账号/API 配额混在一个 adapter 中。

官方当前约束（2026-07-16 核对）：

- 纯 CPU 必须显式选择 `pipeline`；`vlm-engine` / `hybrid-engine` 需要 GPU。
- 官方本地部署最低建议为 16 GiB RAM、20 GiB 磁盘，Python 支持 3.10–3.13。
- MinerU 3.x 的稳定入口是 `mineru` CLI；不传 `--api-url` 时会启动临时本地
  `mineru-api`，任务结束后关闭。仓库 wrapper 调用公开 CLI，不 import 易变的内部类。
- pipeline 的结构化主产物是 `*_content_list.json` / `*_middle.json`，另有 Markdown、
  `layout.pdf`、`span.pdf` 和图片资产。

官方资料：

- [Quick Usage](https://opendatalab.github.io/MinerU/usage/quick_usage/)
- [CLI Tools](https://opendatalab.github.io/MinerU/usage/cli_tools/)
- [Output File Format](https://opendatalab.github.io/MinerU/reference/output_files/)

安装与检查：

```bash
uv sync --extra mineru-cpu
uv run -m parsers.redox_mineru --doctor
uv run -m parsers.redox_mineru inputs/example.pdf --dry-run \
  --model-source modelscope --no-formula --no-table
```

CPU 机器的保守实跑：

```bash
uv run -m parsers.redox_mineru inputs/example.pdf \
  --model-source modelscope \
  --start 0 --end 0 --method txt \
  --no-formula --no-table \
  --threads 4 --inter-op-threads 1 \
  --render-threads 1 --processing-window-size 2
```

wrapper 固定 `-b pipeline` 和 `CUDA_VISIBLE_DEVICES=""`，并设置 MinerU 官方支持的
ONNX/PDF render/window/concurrency 环境变量。公式和表格默认仍开启；`--no-formula`
与 `--no-table` 只建议用于第一次 CPU 可用性验证，正式质量对比应重新开启。

规范输出：

```text
outputs/<stem>/
├── source.pdf
├── static_parse_manifest.json       # 经 static_structurer 时生成
└── mineru/
    ├── raw/                         # MinerU 原始 Markdown/JSON/PDF/images
    ├── images/                      # 规范化图片，重复归一化保持幂等
    ├── document.md                  # frontmatter + 相对图片引用
    ├── elements.jsonl               # content_list 阅读顺序元素
    ├── images.jsonl
    └── parse_summary.json
```

已有 `raw/` 时可只做归一化，不再次推理：

```bash
uv run -m parsers.redox_mineru inputs/example.pdf \
  --out outputs/example/mineru --skip-parse

uv run -m parsers.static_structurer inputs/example.pdf \
  --tool mineru -- --skip-parse
```

依赖体积注意：本机 uv 的 Linux PyTorch 解析仍安装了 CUDA runtime wheel，即使运行期
强制 CPU；完整 `.venv` 约 6.0 GiB。若以后需要严格的 CPU-only 小环境，应单独评估
PyTorch 官方 CPU index，不要在本轮顺手改动整个 RAG 环境的 torch 来源。




### `baidu Unlimited-OCR`   --> `redox_unlimitedocr.py`
特点：gpu





### `llama-index liteparse` 

无论通过 `npm`、`pip` 还是 `cargo install` 安装，命令行接口都是一致的。

解析文件
```bash
# 基本解析
lit parse document.pdf

# 指定输出格式
lit parse document.pdf --format json -o output.json

# 只解析特定页
lit parse document.pdf --target-pages "1-5,10,15-20"

# 关闭 OCR
lit parse document.pdf --no-ocr

# 解析远程 PDF
curl -sL https://example.com/report.pdf | lit parse -
```


批量解析，对整个目录中的文档进行批量解析
```bash
lit batch-parse ./input-directory ./output-directory
```


生成截图,页面截图对 LLM 智能体很关键——它能让模型获取那些仅靠文本无法表达的视觉信息。
```bash
# 截图所有页
lit screenshot document.pdf -o ./screenshots

# 只截特定页
lit screenshot document.pdf --target-pages "1,3,5" -o ./screenshots

# 自定义 DPI
lit screenshot document.pdf --dpi 300 -o ./screenshots
```
> 安装 ImageMagick 以启用图像转 PDF：
```plain
# macOS
brew install imagemagick

# Ubuntu/Debian
apt-get install imagemagick

# Windows
choco install imagemagick.app
```




###  `langchian document_loaders ` --> `redox_langchaindocumentloaders.py`
特点： langchain集成第三方工具, `from langchain_community.document_loaders import xx`


## redox_opendataloaderpdf.py 本地静态文档 smoke（2026-07-02）

目标：先验证单个 PDF parse 工具的接口和输出契约，不批量解析 `/mnt/e/static_docs` 里的所有文档。

测试输入：

```text
/mnt/e/static_docs/sources/getting-the-most-out-of-codex.pdf
```

命令：

```bash
wsl -d Ubuntu-22.04 --cd /home/baheas/wslcodespace/pipelines_rag \
  /home/baheas/.local/bin/uv run -m parsers.redox_opendataloaderpdf \
  /mnt/e/static_docs/sources/getting-the-most-out-of-codex.pdf \
  --out outputs/static_docs/getting-the-most-out-of-codex/opendataloader_pdf \
  --quiet
```

结果：通过。

输出包：

```text
outputs/static_docs/getting-the-most-out-of-codex/
├── source.pdf
└── opendataloader_pdf/
    ├── getting-the-most-out-of-codex.json
    ├── getting-the-most-out-of-codex.md
    ├── document.md
    ├── elements.jsonl
    ├── images.jsonl
    ├── parse_summary.json
    └── images/
```

统计：

| item | count |
|------|-------|
| total elements | 64 |
| text elements | 59 |
| image elements | 4 |
| text chars | 14905 |

观察：

- `source.pdf` 已保存到输出包同级，便于把一个解析包独立迁移/复查。
- `images.jsonl` 中图片路径为 `images/imageFileN.png` 相对路径，且 `exists=true`。
- `document.md` 能按页输出文本和图片块，图片块带 `element_index/type/page/bbox/source/path` metadata。
- 第 1 页的小 X 图标也会作为 image element 保留；后续进入 RAG caption 前仍需要 `rag_pdfs.pdf_filter` 做小图过滤。
- 该 PDF 文本抽取可读，但包含中英混排和部分机器翻译风格文本；这是源 PDF 内容/生成方式的问题，不是 redox 包装层失败。

当前推荐最小验证：

```bash
/home/baheas/.local/bin/uv run -m parsers.redox_opendataloaderpdf --help
/home/baheas/.local/bin/uv run python -m compileall parsers/redox_opendataloaderpdf.py
```

后续只需挑代表性 PDF 继续试：

- 图片/截图多的技术文章：看 `document.md` 图文邻近关系。
- 中文网页 PDF：看编码、标题、段落顺序。
- 大 PDF：看 opendataloader 性能和输出体积。

## redox_mineru.py 本地纯 CPU smoke（2026-07-16）

环境：WSL2 / Python 3.12.13 / MinerU 3.4.4 / 20 logical CPU / 19.53 GiB RAM；
模型源为 ModelScope。输入 `inputs/qwen-agentworld_blog.pdf`（7 页，2.21 MiB），只解析
第 1 页，`method=txt`，关闭公式和表格。

结果：

| 项目 | 结果 |
|------|------|
| 首次运行（含模型下载） | 51.962 秒 |
| 缓存后重跑 | 20.768 秒 |
| ModelScope cache | 约 235 MiB |
| elements | 23 |
| image/chart rows | 4 |
| type counts | text 16 / image 3 / chart 1 / header 2 / footer 1 |
| Markdown 图片引用 | 4，全部存在 |

真实运行确认：临时 `mineru-api` 正常启动并在任务后关闭；`source.pdf`、原始 MinerU
artifact、规范化 Markdown/JSONL/summary 和顶层 manifest 均已生成。连续两次
`--skip-parse` 后规范图片仍为 4 个，无重复副本，原始 `txt / formula=false /
table=false / parse_elapsed=20.768s` 元数据保持不变。


