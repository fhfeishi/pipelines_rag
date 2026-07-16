# 网页静态采集工具：crawl4ai / Firecrawl / Scrapling

> 更新：2026-07-16。本文是 `parsers/rewebpage_*.py` 的统一说明与验证记录。

## 1. 定位

三个脚本都属于 **Layer 0 网页采集**：把 URL 固化为可检查的 JSON、Markdown 和可选视觉快照。
它们暂不直接生成完整 StaticParsePackage；后续优先让 `page.md` 走
`markdown -> 元素流 -> write_package`，需要保留视觉证据时再让 `page.pdf` 进入
`redox_opendataloaderpdf`。

| 脚本 | 后端 | 适合场景 | API key | 可生成 PDF |
|------|------|----------|---------|------------|
| `rewebpage_craw.py` | crawl4ai 本地 Chromium | JS 页面、Markdown + 浏览器快照；默认后端 | 否 | 是，浏览器原生打印 |
| `rewebpage_firecrawl.py` | Firecrawl v2 云 API / 自托管 | 本机不跑浏览器、跨环境复现、交叉验证 | 官方云需要 | 是，整页截图转 A4 PDF |
| `rewebpage_scrapling.py` | Scrapling HTTP / Dynamic / Stealthy | 轻量 HTTP 抓取、CSS 定向正文、强反爬备选 | 否 | 否 |
| `rewebpage_common.py` | 共享模型/写盘层 | URL、链接、标题、词数、目录和产物归一化 | — | Firecrawl 共用图片转换 |

旧的空占位 `rescrapy_scrapling.py` 已删除，Scrapling 现归入统一的 `rewebpage_*` 命名和
`static_structurer` registry。

## 2. 统一输出

单 URL 调用会**直接**写到 `--out-dir`，不再额外套一层 slug：

```text
outputs/<source-stem>/<tool>/
├── page.json             # {snapshot: 归一化字段, raw: provider 字段/省略说明}
├── page.md               # RAG/人工检查的主文本产物
├── page.html             # 可选
├── page.png              # 可选，crawl4ai / Firecrawl
├── page.pdf              # 可选，crawl4ai / Firecrawl
└── page.mhtml            # 可选，仅 crawl4ai
```

批量 URL 调用才会使用 `<slug>/page.*`，避免文件覆盖。

`page.json.snapshot` 的共同字段：

```text
provider, url, source_url, title, description, status_code,
word_count, headings, markdown,
links_internal, links_external, images, metadata, fetched_at
```

统一层还做了以下规范化：

- 只接受绝对 `http(s)` URL；相对链接按最终 URL 补全并去除 fragment。
- 中英文混合词数按英文 token + 单个 CJK 字符粗略统计，避免中文整段被记为 1 词。
- Markdown 统一 LF，并保证非空文件以换行结束。
- Firecrawl 的 Markdown/HTML/screenshot 大字段不重复塞进 JSON；`raw._omitted_artifacts`
  记录是否已另存和原字段长度。
- 单 URL 后端失败会返回非零退出码；多 URL 允许保留成功产物，但任一失败时总退出码仍非零。
- 三个脚本均有 `--dry-run`，不会导入 SDK、访问网络或写文件。

## 3. 安装

三套依赖拆成独立 extra，默认 PDF/RAG 环境不会自动安装浏览器和云 SDK：

```bash
# crawl4ai
uv sync --extra webpage-crawl4ai
uv run crawl4ai-setup
# setup 需要 sudo 安装系统库时，也可先只装浏览器：
uv run playwright install chromium

# Firecrawl
uv sync --extra webpage-firecrawl

# Scrapling（HTTP 模式安装后即可用；browser 模式还要安装浏览器）
uv sync --extra webpage-scrapling
uv run scrapling install
```

当前 `uv.lock` 解析到：crawl4ai 0.9.2、firecrawl-py 4.32.0、Scrapling 0.4.11、
markdownify 1.2.3。`pyproject.toml` 只规定兼容下限，具体复现版本以 lock 为准。

Firecrawl key 读取顺序：

1. `--api-key`
2. 环境变量 `FIRECRAWL_API_KEY`
3. `configs/.env` 中的 `FIRECRAWL_API_KEY` / `firecrawl_api_key`

密钥只用于运行，不写入产物或日志。

## 4. 推荐调用

### 4.1 汇总入口

```bash
# 默认 crawl4ai，输出到 outputs/<source-stem>/crawl4ai/page.*
uv run -m parsers.static_structurer \
  "https://example.com/article"

# Firecrawl
uv run -m parsers.static_structurer \
  "https://example.com/article" --tool firecrawl

# Scrapling 轻量 HTTP
uv run -m parsers.static_structurer \
  "https://example.com/article" --tool scrapling

# 网页 PDF 继续进入 opendataloader（仅 crawl4ai / Firecrawl）
uv run -m parsers.static_structurer \
  "https://example.com/article" --tool crawl4ai --parse-page-pdf

# backend 参数放在 -- 后
uv run -m parsers.static_structurer \
  "https://example.com/article" --tool scrapling -- \
  --fetcher dynamic --wait-selector article --include-html
```

Scrapling 不产出 `page.pdf`；与 `--parse-page-pdf` 同用时 manifest 会写一条明确的
`skipped` 记录，而不是递归猜文件或静默失败。

### 4.2 直接调用

```bash
# crawl4ai：Markdown + PDF；按需保留 PNG/HTML/MHTML
uv run --extra webpage-crawl4ai -m parsers.rewebpage_craw \
  "https://example.com/article" \
  --out-dir outputs/manual/crawl4ai \
  --keep-png --include-html

# Firecrawl：Markdown + screenshot PNG/PDF
uv run --extra webpage-firecrawl -m parsers.rewebpage_firecrawl \
  "https://example.com/article" \
  --out-dir outputs/manual/firecrawl \
  --include-html --include-images

# Scrapling：优先从 HTTP 开始；页面依赖 JS 时再升级 dynamic/stealthy
uv run --extra webpage-scrapling -m parsers.rewebpage_scrapling \
  "https://example.com/article" \
  --out-dir outputs/manual/scrapling \
  --fetcher http --selector article --include-html

uv run --extra webpage-scrapling -m parsers.rewebpage_scrapling \
  "https://example.com/app" \
  --fetcher dynamic --wait-selector '.article-loaded' --network-idle

uv run --extra webpage-scrapling -m parsers.rewebpage_scrapling \
  "https://example.com/protected" \
  --fetcher stealthy --solve-cloudflare
```

## 5. 后端细节与选择

### crawl4ai

- 使用 `BrowserConfig` 管浏览器环境、`CrawlerRunConfig` 管单次抓取。
- 默认用 `fit_markdown`，`--raw-markdown` 可切换原始 Markdown。
- `pdf=True` 直接读取 `result.pdf`，不再依赖私有 hook 截图后用 Pillow 手工分页。
- 可选 `--respect-robots`、`--wait-for css:...`、`--include-mhtml`。
- 默认代理只读取环境变量，不再写死 `127.0.0.1:7897` 或某个域名/IP。

### Firecrawl

- 使用当前 Python SDK 的 `Firecrawl(...).scrape(url, formats=...)`。
- screenshot format 按 v2 规范传 `{"type":"screenshot","fullPage":true,"quality":85}`。
- 下载到的截图先规范为 PNG；需要 PDF 时按 A4 比例分页。
- `--probe` 只测 API 域名连通性，不需要 key、不消耗 scrape 额度。
- 官方云无 key 时会在调用前给出明确错误；自托管 `--api-url` 可按服务配置运行。

### Scrapling

- `--fetcher http`：最快、资源最少，超时参数在适配层从毫秒换算为 Scrapling HTTP 的秒。
- `--fetcher dynamic`：执行 JS，支持 `--wait-selector`、`--network-idle`、`--headful`。
- `--fetcher stealthy`：强反爬备选，`--solve-cloudflare` 只允许在该模式使用。
- 默认依次选择 `article`、`main`、`[role=main]`、`body` 转 Markdown；也可显式
  `--selector`。HTML 到 Markdown 使用 `markdownify`，会保留标题、列表、链接、图片引用。
- 当前不保存浏览器截图/PDF；它的价值是轻量文字通道和跨后端正文对照。

建议顺序：普通静态页先 Scrapling HTTP；需要 JS/视觉快照用 crawl4ai；本机浏览器不便运行或
需要云端交叉验证时用 Firecrawl。是否“更好”应比较 `page.md` 的正文完整性、噪声、图片链接和
标题结构，而不是只看请求是否返回 200。

## 6. 网页进入 RAG 的两条路径

```text
文字主路径：page.md -> markdown 元素流 adapter -> StaticParsePackage -> RAG ingest

视觉保真路径：page.pdf -> redox_opendataloaderpdf -> 图片元素 -> VLM caption
```

crawl4ai 的浏览器原生打印 PDF **可能保留文字层**：本次 `example.com` 组合 smoke 被
opendataloader 解析为 3 个文本元素、0 个图片元素。Firecrawl 的 PDF 来自整页截图分页，
以及旧版 crawl4ai 截图转 PDF，通常会成为“每页一张大图”。因此每个站点都要检查
`elements.jsonl/parse_summary.json`；无论 PDF 是否保留文字，`page.md` 仍是网页文字主路径。

## 7. 网络与代理

- crawl4ai：`--proxy-server` / `--no-browser-proxy`，按需加 `--proxy-bypass` 和可重复的
  `--host-resolver-rule`。
- Firecrawl：`--http-proxy` / `--no-http-proxy` 控制本机到 API 和截图 URL 的链路；
  `--proxy auto|basic|stealth|enhanced` 是 Firecrawl 云端访问目标站的另一层代理。
- Scrapling：`--proxy` / `--no-proxy`；默认读取 `HTTPS_PROXY/HTTP_PROXY`。

不要把某台机器的 Clash 端口、fake-ip、hosts 或 `/32` 路由固化进通用脚本。遇到问题先分别
验证“本机到 provider API”和“provider/浏览器到目标网页”两段链路。

## 8. 2026-07-16 验证

通过：

```text
python -m unittest discover -s tests -v                         6/6 OK
python -m compileall -q parsers/...                             OK
ruff check / ruff format --check                                OK
uv pip check                                                     OK
python -m parsers.rewebpage_{craw,firecrawl,scrapling} --help   OK
三个 backend 的 --dry-run                                      OK
python -m parsers.static_structurer --list-tools                6 tools

Scrapling HTTP -> https://example.com                           HTTP 200
  page.json / page.md / page.html                               OK

crawl4ai 0.9.2 -> https://example.com                           HTTP 200
  page.json / page.md / page.html / page.png                    OK
  page.json / page.md / page.pdf                                OK

static_structurer -> crawl4ai / Scrapling                       HTTP 200
  扁平 <tool>/page.* + static_parse_manifest.json               OK
static_structurer --parse-page-pdf -> opendataloader             OK
  example.com: 3 个文本元素 / 0 个图片元素                       OK

Firecrawl API --probe                                           HTTP 200
Firecrawl 无 key -> static_structurer                           预期失败
  exit 1 + manifest record=failed                               OK
wheel 构建 + 三个 extras 元数据                                 OK
```

未验证：Firecrawl 真实 scrape（当前环境未配置 `FIRECRAWL_API_KEY`）；Scrapling dynamic / stealthy
真实反爬站点。Firecrawl 的归一化适配由离线 fake document 单测覆盖。

## 9. 官方资料

- [crawl4ai AsyncWebCrawler](https://docs.crawl4ai.com/api/async-webcrawler/)
- [crawl4ai CrawlResult（screenshot / PDF / MHTML）](https://docs.crawl4ai.com/core/crawler-result/)
- [Firecrawl Python SDK](https://github.com/firecrawl/firecrawl#python)
- [Firecrawl v2 scrape API](https://docs.firecrawl.dev/api-reference/endpoint/scrape)
- [Scrapling fetcher 选择](https://scrapling.readthedocs.io/en/latest/fetching/choosing/)
- [Scrapling DynamicFetcher](https://scrapling.readthedocs.io/en/latest/fetching/dynamic/)
- [Scrapling 安装与 extras](https://github.com/D4Vinci/Scrapling/#installation)
