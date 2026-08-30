# pipelines-rag 启动章程

> 本文件是仓库级 canonical charter。PDF 图片 RAG 产品细节见 [`rag_pdfs/strata.md`](rag_pdfs/strata.md)；
> 职业规划与 agent 成长路线见 [`agent_rag_growth_roadmap.md`](agent_rag_growth_roadmap.md)。

## 1. 项目一句话

构建一个**支持图文交流**的 Agentic RAG 原型：把技术文档（PDF、网页、音视频转写等）中的非结构化信息，尤其是**图片里的关键证据**，在 ingest 阶段读成可检索文本，在 query 阶段用纯文本 hybrid 检索回答 image-helpful / image-required 问题；同时把工程拆成可检查、可评估、可迭代的层次。

## 2. 背景与定位

- **产品问题**：传统 text-only RAG 在 ingest 丢弃图片，导致操作类问题缺细节、表格/架构类问题直接答错。
- **核心原则**：**Vision at ingestion, text at retrieval** —— VLM 只在 ingest 读图一次生成 caption；query 不送图。
- **职业定位**（见 `agent_rag_growth_roadmap.md`）：这是第一个长期项目，同时走两条线：
  - **产品线**：非结构化解析 → 图片 RAG → eval → 服务化/UI/trace。
  - **原理线**：tool calling、state、retrieval、eval、observability 的实战理解。
- **当前阶段**：ingest / query / hybrid / eval CLI 已跑通；解析工具箱 `parsers/` 有初版；**工程边界尚未理顺**——这是当前首要整理对象。

## 3. 仓库分层（目标架构）

```text
Layer 0    来源采集          URL / 音视频 / 本地 PDF / 纯文本 / 存量 markdown
                │
Layer 1    静态结构化        parsers/  →  StaticParsePackage
                │              (elements.jsonl, images, document.md, manifest)
                │              document.md = 人类/产品门面；elements.jsonl = 机器接口
                │
Layer 1.5  知识蒸馏（实验）   knowledge/ →  OKF 式概念包（概念文件 + frontmatter + 互链）
                │              ★不阻塞当前优先级；见 §3.2(c)
                │
Layer 2    图片 RAG ingest   rag_pdfs/ →  filter → caption → chunk → Chroma
                │
Layer 3    检索与评估         rag_pdfs/ →  检索工具箱（hybrid / agentic 导航）→ eval
                │
Layer 4    agent workflow    知识图导航优先，不足降级文档 RAG（roadmap Phase 4）
```

| 包 | 职责 | 不应承担 |
|----|------|----------|
| `parsers/` | 测试与沉淀**解析工具**；产出人类可检查的静态结构包 | VLM caption、向量索引、RAG 策略实验 |
| `rag_pdfs/` | **图片 RAG 闭环**：过滤、caption、chunk、检索、eval | 重复实现各解析 backend 的全套 CLI 逻辑 |
| `rag_langchain/` | 冻结历史材料，不扩展 | 任何新功能 |
| `configs/` | pydantic-settings；密钥在 `.env` | — |

**StaticParsePackage 最小契约**（Layer 1 → Layer 2 的接口）：

```text
outputs/<source-stem>/
├── static_parse_manifest.json
├── source.pdf | source.md | ...
└── <tool_subdir>/          # 如 opendataloader_pdf/
    ├── *.json              # layout
    ├── images/
    ├── elements.jsonl
    ├── images.jsonl
    ├── parse_summary.json
    └── document.md         # 图文编排，相对图片路径
```

### 3.1 目标态模块地图（重构落点）

依赖倒置修正后，代码应长成这样（渐进迁移，不要求一次到位）：

```text
parsers/
├── document/                  # 共享 parse 核心（从 rag_pdfs.pdf_layout / pdf_parser 下沉）
│   ├── layout.py              # LayoutElement、阅读顺序、section/context 提取
│   ├── opendataloader.py      # run_opendataloader（合并 redox 的 with_pages 变体）
│   ├── package.py             # StaticParsePackage 模型 + load/write_package、JSONL/manifest 读写
│   └── markdown.py            # 图文编排 markdown writer（从 redox 下沉）+ markdown→元素流 adapter
├── redox_opendataloaderpdf.py # 只剩 CLI + Markdown 编排，import parsers.document.*
├── static_structurer.py       # 编排入口（现状即可）
└── rewebpage_* / reaudio_*    # Layer 0 采集，不参与 document 契约

rag_pdfs/
├── runtime.py                 # settings 读取 + embedding 初始化（新增，解除 query/eval → ingest 反向 import）
├── parse_source.py            # 消费 StaticParsePackage（--parse-dir），过渡期兼容内联 parse
├── pdf_filter.py              # L1/L2 过滤（RAG 专用，留下）
├── captioner.py               # VLM caption + 质量校验 + 缓存（从 ingest_img 拆出）
├── caption_chunks.py          # chunk 构建（现状即可）
├── indexer.py                 # JSONL 落盘 + Chroma 构建（从 ingest_img 拆出）
├── hybrid_retrieve.py         # 检索（现状即可）
└── ingest_img / query_img / eval_img   # CLI 收窄为参数解析 + 阶段编排
```

依赖方向：`rag_pdfs → parsers.document`（单向）；三个 CLI 平级，共同依赖 `runtime` 与各阶段模块。

### 3.2 演进设计定稿（2026-07-10 讨论）

三个设计决定，按生效时间排序：(a)(b) 随下一步代码落地；(c) 为实验性方向，eval 闭环后立项。

#### (a) 统一接口：以 document.md 为门面的 StaticParsePackage

RAG 知识库的原始输入统一为**图文编排 markdown**，但接口"货币"不是裸 markdown，
而是包：`document.md`（人类/产品门面，LLM 读取效率高）+ `elements.jsonl`（机器接口，
caption/citation 所需的结构化 metadata）**从同一个元素流生成**。裸 markdown 作为唯一
接口会丢失 bbox/元素 ID，逼下游写脆弱的 markdown 反解析器。

统一的杠杆在**元素流**层：每个工具的职责收敛为 `源 → list[LayoutElement]`，
共享 writer（`parsers/document/`）负责元素流 → 完整包：

```text
任意源 ──(各工具 adapter)──> 元素流 ──(共享 write_package)──> StaticParsePackage
```

落点（`parsers/document/package.py` 扩展 + 新增 `markdown.py`）：

- `StaticParsePackage` pydantic 模型 + `load_package(dir)`（供 ingest `--parse-dir` 消费）
  + `write_package(out_dir, elements, ...)`（内部调用共享 markdown writer，
  从 redox 的 `write_image_aware_markdown` 下沉）。
- **`markdown → 元素流` adapter**（一个 adapter 双收益）：让网页路径改走
  统一的 `rewebpage_* page.md → 元素流`（默认 crawl4ai，可用 Firecrawl/Scrapling 交叉验证；
  替代截图 PDF → opendataloader 的纯图片元素路径，找回文字），
  同时支持存量 markdown 直接入库。
- 音视频转写分段即 text 元素流，套同一 writer（低优先级）。
- 不新造 `static_interface.py`；`static_structurer` 保持唯一 CLI 编排入口，
  Python API 挂在 `parsers.document` 下。

#### (b) 坐标系：section_path 为主坐标，页码/bbox 为物理坐标

页码与多级标题是两种坐标：**标题路径 = 逻辑坐标**（跨源通用：PDF/网页/markdown/转写
都有或可构造标题层级，且对 LLM 是语义信息）；**页码 + bbox = 物理坐标**（仅"纸面"源有意义，
但证据展示/原图定位不可丢）。设计：

- 元素流遍历时维护 heading 栈，给每个元素挂 `section_path: ["h1", "h2", ...]`；
- `elements.jsonl`、caption prompt、chunk metadata、evidence citation 以 `section_path`
  为主锚点（替代现在"最近单个 heading"的 `section_title`，后者保留为降级值）；
- `page` / `bbox` 降级为源特定物理 metadata，保留在图片 metadata 中用于原图定位与 UI 预览；
- 兜底：PDF 解析的 heading 层级质量参差，`section_path` 允许降级为扁平最近标题，
  页码作为 PDF 源兜底坐标。

`section_path` 属于 Layer 1 契约，并入 `write_package` 一步实现（两种检索后端共同依赖）。

#### (c) 实验方向：Layer 1.5 知识层（OKF）与 Layer 2–3 重定义

**Layer 1.5 知识蒸馏**（参考 [Google OKF v0.1](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/HEAD/okf/SPEC.md)，2026-06 发布：
markdown 目录 + YAML frontmatter，唯一必填字段 `type`，`index.md`/`log.md` 保留名，
markdown 链接连成概念图）：

| | StaticParsePackage（文档层） | OKF 式知识包（知识层） |
|---|---|---|
| 组织单位 | 源文档 | 概念（一个概念一个文件） |
| 性质 | 忠实转写、机器生成、可重跑 | 策展/蒸馏、有解释成分 |
| 用途 | 证据真相（page/bbox/原图引用） | agent 导航、直接入上下文 |
| 失效模式 | 解析错误（可检查） | 蒸馏幻觉、知识过期 |

约束：概念文件必须带 evidence 链接指回文档层（元素 ID / `imgcap-*` / page），幻觉可追溯。
现在只做**低成本 OKF 兼容**：`write_package` 给 `document.md` 加 frontmatter
（`type` / `title` / `source` / `timestamp`），包根生成 `index.md`。蒸馏层在 eval 闭环前不上马。

**Layer 2–3 重定义**：从「canonical 固定 pipeline」变为「检索工具箱 + 度量仪器」。
检索层不可删，理由：① 规模——语料一大 agent 不可能全读，grep/BM25/vector 是 agent 的
候选发现工具；② 长尾——蒸馏有损，image-required 细节问题必须降级文档层检索；
③ 度量——"agent 读 markdown 是否够"是实验问题，eval 框架就活在这一层。

```text
Layer 2–3 检索工具箱
  ├─ 后端 1：chunk + Chroma + hybrid（现有，A/B/C/D 载体）
  ├─ 后端 2：agentic markdown 导航（grep + index.md + section_path 跳转）← G 组
  └─ eval：同一标注集正面对决
```

新实验组（细节见 `rag_pdfs/notes.md`）：**F 组** = 概念级检索（依赖 Layer 1.5 蒸馏）；
**G 组** = agentic markdown 导航 vs hybrid 检索。两组都排在标注集就位之后。

## 4. 已知工程问题（需按优先级修正）

### 4.1 依赖方向倒置（P0）——✅ 已修复（2026-07-07）

原状：`parsers/redox_opendataloaderpdf.py` import `rag_pdfs.pdf_layout` / `rag_pdfs.pdf_parser`，底层工具箱依赖上层 RAG 包。

修复：parse 核心已下沉到 `parsers/document/`（`layout.py` / `opendataloader.py` / `package.py`）：

- `redox_opendataloaderpdf` 改 import `parsers.document.*`，删除本地重复的
  `run_opendataloader_with_pages` / `find_existing_opendataloader_json` / `write_jsonl`。
- `run_opendataloader` 合并 pages/quiet 变体为单一实现。
- `rag_pdfs/pdf_layout.py` / `pdf_parser.py` 降级为兼容 shim（re-export + RAG 专属模型
  `ImageCaption` / `SkippedImage` 留在 rag_pdfs）；新代码直接 import `parsers.document.*`。

剩余：`rag_pdfs.ingest_img` 实现 `--parse-dir` 消费 Layer 1 产物（见 §8 第 3 步）。

### 4.2 `ingest_img.py` 职责过重（P1）

现状：单文件编排 parse → filter → caption → chunk → Chroma + batch/resume/dry-run（~780 行）。

目标：按阶段拆模块或 pipeline 函数，CLI 只做参数与编排；各阶段输入输出对齐 StaticParsePackage / chunk JSONL 契约。

### 4.3 双路径 parse 未统一（P1）

- Path A：`uv run -m rag_pdfs.ingest_img foo.pdf`（内联 opendataloader）
- Path B：`python -m parsers.redox_opendataloaderpdf foo.pdf` → 再 ingest

目标：Path B 为**推荐主路径**（先人工检查 `document.md`），Path A 在 parse 稳定前保留。

### 4.4 parsers 工具完成度不一（P1）

| 工具 | 状态 | 说明 |
|------|------|------|
| `opendataloader_pdf` | 可用（~95%） | PDF → 完整 StaticParsePackage；默认、依赖较轻的主力 |
| `mineru` | 可用（~90%） | CPU pipeline → 完整同类解析包；3.4.4 首-page 真实 smoke 通过 |
| `crawl4ai` | 可用（~95%） | 网页 → page.{json,md,pdf/png/html/mhtml}；0.9.2 真实 smoke 通过 |
| `firecrawl` | 待 key 验证（~90%） | v2 适配与 probe 通过；缺 API key 未跑真实 scrape |
| `scrapling` | 可用（~90%） | HTTP 真实 smoke 通过；支持 dynamic/stealthy；当前不产视觉 PDF |
| `dashscope_asr` | 可用（~85%） | 音视频 → 转写 MD/JSON/SRT；无 manifest，非完整契约；非主线 |
| `copy_text` | 可用 | 纯文本归一化（static_structurer 内置） |
| `redox_liteparse` / `unlimitedocr` | **占位（仅注释）** | 备选 parser；不投入前只在 notes 保留调研结论 |
| `rescrapy_parsel` | **占位（空文件）** | Scrapling 已迁入 rewebpage；Parsel 留待删除或实现 |
| `script_lm.py` | 草稿（~5%） | LM 后处理设想，未成型 |

处置原则：占位文件不算"工具库"的一部分；要么在下个迭代实现最小 CLI，要么删除文件、只在 `*_notes.md` 保留调研记录。避免目录里"看起来有很多工具，实际只有一个能跑"。

### 4.5 eval 与标注集未闭环（P2，产品验证关键）

骨架 `eval_img.py` 已有；缺足够 labeled question set 与 A/B/C/D 定量结论——见 `agent_rag_growth_roadmap.md` Phase 1。

### 4.6 工程卫生问题（P2，随手修，不单独立项）

2026-07-07 全仓库梳理发现的具体问题，修一个划一个：

- [x] **query 路径重复检索**（2026-08-29）：`RetrievedEvidence` 统一承载文档与 hybrid 分数；evidence 展示和 generation context 复用同一次检索，generation chain 不再访问 retriever。
- [ ] **配置反向耦合**：`query_img` / `eval_img` import `ingest_img.get_setting` / `build_embeddings`；配置与 embedding 初始化应独立成模块（如 `rag_pdfs/runtime.py`），三个 CLI 平级消费。
- [ ] **死代码**：`pdf_filter.filter_image_elements()` 全仓库无调用（ingest 内联用 `classify_image_skip`）；二选一删除。
- [ ] **`write_jsonl` 重复**：~~redox 份~~已收敛到 `parsers.document.package`（2026-07-07）；`ingest_img` / `eval_img` 两份待拆 ingest 时合并。
- [ ] **测试覆盖不足**：rewebpage + MinerU adapter 已有 10 个离线单测（2026-07-16）；
  filter 规则、hybrid fusion、chunk 构建、eval 指标仍需补测。
- [ ] **pyproject 打包/可选依赖**：~~`parsers` 不在 wheel 中~~已加入（2026-07-07）；
  ~~crawl4ai / Firecrawl / Scrapling / MinerU CPU extras~~已声明（2026-07-16）；
  dashscope extra 待补。
- [ ] **BM25 分词仅 `lower().split()`**：中文语料无效；标注集含中文问题前需换 jieba 或字符 n-gram。
- [x] **网页工具输出多一层 slug 嵌套**：2026-07-16 已统一单 URL 扁平输出并删除 `newest_file` 递归猜测。
- [x] **旧名残留**：2026-07-16 已清理 rewebpage 脚本/notes 中的 `script_*` 旧名。
- [ ] ~~`rag_pdfs/notes.md` 尾部混入无关文章~~：已迁至 `knowledge/first_principles_thinking.md`（2026-07-07）。

## 5. 核心目标（仓库级）

1. **Layer 1 可靠**：任意 PDF/网页源 → 可检查的 `document.md` + 元素清单。
2. **Layer 2–3 可验证**：ingest → hybrid query → eval 端到端；separate vs inline 有数据支撑。
3. **边界清晰**：parsers 与 rag_pdfs 单向依赖；行为变化有 `plan.md` / `logs.md` 记录。
4. **为 Agentic 演进留接口**：manifest、run id、evidence citation 结构稳定，便于后续 trace/agent。

## 6. 非目标（当前）

- query 阶段多模态（E 组仅作上限对照）。
- CLIP 图像向量主路径。
- 生产部署 / 完整 UI（Phase 2+）。
- 在 parsers 未稳定前大改 rag_pdfs 检索算法。
- 在 eval 闭环前建 Layer 1.5 知识蒸馏层（OKF 兼容 frontmatter 除外，见 §3.2c）。

## 7. 成功标准

- `uv run -m rag_pdfs.{ingest_img,query_img,eval_img} --help` 可运行。
- `python -m parsers.static_structurer --list-tools` 可运行；PDF 工具产出完整 StaticParsePackage。
- 单 PDF：**先** redox parse **再** ingest，人工可核对 `document.md` 与 caption 输入一致。
- `eval_img` 在标注集上输出 recall@k / evidence 指标（sample 集先跑通）。

## 8. 当前优先级（2026-07）

```text
1. 文档与边界对齐（本章程 + plan 更新）              ← 2026-07-07 ✅
2. pdf_layout / pdf_parser 下沉 parsers/document/     ← 2026-07-07 ✅（见 §3.1、§4.1）
3. StaticParsePackage 接口落地（见 §3.2 a/b）：       ← 下一步代码
   write_package / load_package + section_path 主坐标 + OKF 兼容 frontmatter/index.md；
   ingest 实现 --parse-dir 用 load_package 消费包，内联 parse 降级为兼容路径
4. markdown → 元素流 adapter（网页/存量 markdown 入库，见 §3.2a）
5. 拆 ingest_img monolith：runtime / captioner / indexer（见 §3.1），
   顺手修 §4.6 的 query 重复检索与反向 import
6. 标注问题集 + eval A/B/C/D 定量结论                 ← parse 稳定后
7. F 组（概念级检索，依赖 Layer 1.5 蒸馏）/ G 组（agentic 导航）实验   ← 6 之后
8. 服务化 / agent workflow / UI                       ← roadmap Phase 2+
```

节奏原则：3–5 每步保持三个 CLI `--help` 可运行并追加 `logs.md`；
不要在 6 之前动检索算法；Layer 1.5 蒸馏在 6 闭环前不上马（OKF 兼容化除外，成本极低）。

## 9. 文档地图

| 文件 | 职责 |
|------|------|
| rag_pdfs/architecture.md | RAG 顶层架构、模型运行时、LangGraph 与 LangSmith 设计 |
| **`strata.md`（本文件）** | 仓库章程、分层、优先级 |
| `rag_pdfs/strata.md` | 图片 RAG 产品章程 |
| `agent_rag_growth_roadmap.md` | 产品线 + agent 原理成长 |
| `parsers/plan.md` | 解析工具箱与输出约定 |
| `rag_pdfs/plan.md` | RAG 技术蓝图与模块状态 |
| `rag_pdfs/notes.md` | 实验设计与博客结论 |
| `rag_pdfs/logs.md` | 追加式运行记录 |
| `AGENTS.md` | Agent 工作规则 |

## 10. 主要入口

```bash
# Layer 1：静态结构化
python -m parsers.static_structurer path/to/doc.pdf
python -m parsers.redox_opendataloaderpdf path/to/doc.pdf

# Layer 2–3：图片 RAG
uv run -m rag_pdfs.ingest_img path/to/doc.pdf --out tmp/outs/foo --build-chroma
uv run -m rag_pdfs.query_img --index tmp/outs/foo --strategy separate --show-evidence "question"
uv run -m rag_pdfs.eval_img --questions rag_pdfs/eval_questions.sample.jsonl --retrieve-only
```
