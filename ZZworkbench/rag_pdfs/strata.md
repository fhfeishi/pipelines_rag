# rag_pdfs 产品章程

> 仓库级分层与优先级见根目录 [`strata.md`](../strata.md)。
> Agent 成长路线见 [`agent_rag_growth_roadmap.md`](../agent_rag_growth_roadmap.md)。

## 1. 项目一句话

把技术文档 PDF 中的图片（截图 / 表格 / 架构图）在 ingest 阶段读成可检索、可引用的文本证据，让 RAG 能回答 image-helpful / image-required 问题；query 阶段保持纯文本 hybrid 检索。

## 2. 在仓库中的位置

`rag_pdfs` 是 **Layer 2–3**（图片 RAG ingest + 检索 + eval），**上游依赖** `parsers/` 产出的 StaticParsePackage（Layer 1）。

```text
parsers (Layer 1)                    rag_pdfs (Layer 2–3)
─────────────────                    ────────────────────
PDF/网页 → layout + document.md  →   filter → VLM caption → chunk → Chroma
                                     → hybrid query → eval
```

**过渡态说明**：当前 `ingest_img` 仍可内联调用 opendataloader（经 `pdf_parser.py`），与 `parsers/redox_opendataloaderpdf` 逻辑重复。目标态是 ingest **默认消费** `--parse-dir` 指向的 Layer 1 产物；内联 parse 仅作快捷/兼容路径。

## 3. 核心原则

**Vision at ingestion, text at retrieval.**

| 阶段 | 做法 |
|------|------|
| Ingest | VLM 读图一次 → caption → 文本 chunk 索引 |
| Query | hybrid BM25 + vector；不送图给 VLM |
| Eval | 标注 text-only / image-helpful / image-required；对比 A/B/C/D |

详见 `notes.md` 与 `plan.md` §1–3。

## 4. 模块职责（目标态）

| 模块 | 职责 | 备注 |
|------|------|------|
| `model_runtime.py` | 声明式模型目录、角色绑定、API/本地模型工厂、缓存与索引指纹 | provider adapter 层 |
| `prompts.py` | 版本化回答、查询改写、历史摘要提示词 | 新增；避免提示词散落在 CLI |
| `ingest_img.py` | CLI 编排：读 parse 包 → filter → caption → chunk → Chroma | 现 782 行 monolith，应收窄为编排层 |
| `runtime.py` | settings/catalog 加载 + 依赖组合 | 三条 CLI 统一按 role 取模型；保留旧参数覆盖 |
| `index_manifest.py` | embedding contract 持久化与打开索引时校验 | mismatch 拒绝；legacy 默认告警、可严格拒绝 |
| `parse_source.py`（新增） | 消费 StaticParsePackage（`--parse-dir`） | 内联 parse 降级为兼容路径 |
| `captioner.py`（新增，从 ingest 拆出） | VLM caption + quality_flag + resume 缓存 | |
| `indexer.py`（新增，从 ingest 拆出） | chunk JSONL 落盘 + Chroma 构建 | 顺带评估 rmtree 全量重建策略 |
| `pdf_layout.py` | ✅ 已迁至 `parsers/document/layout.py`（2026-07-07） | 现为 shim + RAG 专属模型（ImageCaption / SkippedImage） |
| `pdf_parser.py` | ✅ 已迁至 `parsers/document/opendataloader.py`（2026-07-07） | 现为纯 re-export shim |
| `pdf_filter.py` | L1/L2 图片过滤 | 留在 rag_pdfs（RAG 专用）；删除未被调用的 `filter_image_elements` 或改为唯一入口 |
| `caption_chunks.py` | inline / separate chunk 构建 | 留在 rag_pdfs |
| `hybrid_retrieve.py` | BM25 + vector | 留在 rag_pdfs；BM25 分词需支持中文后再上中文标注集 |
| `query_img.py` | 三策略 query CLI | evidence、debug score、answer 复用同一次检索 |
| `eval_img.py` | 批量实验与 evidence 指标 | 留在 rag_pdfs |

拆分顺序与全局优先级见根 `strata.md` §3.1、§8：先下沉 `pdf_*`，再实现 `--parse-dir`，最后拆 ingest。
每一步保持三个 CLI `--help` 可运行并追加 `logs.md`。

## 5. 系统流程

```text
[上游] parsers → StaticParsePackage (elements.jsonl, images/, document.md)
      ↓
LayoutElement 序列（阅读顺序 / section title）
      ↓
L1/L2 图片过滤（skip → skipped_images.jsonl）
      ↓
VLM caption（image_captions.jsonl，quality_flag）
      ↓
text_only / inline / separate / mixed 四种 chunk JSONL
      ↓
Qwen3 embedding + Chroma（4 collection）
      ↓
query：hybrid 检索 → DeepSeek 生成（区分 text/image evidence）
      ↓
eval：策略 × 检索 × alpha，recall@k 与 evidence 统计
```

## 6. 核心目标

1. 维持端到端闭环：parse 包 → filter → caption → chunk → Chroma → query → eval。
2. 与 Layer 1 **契约对齐**：ingest 不再 silently 重复 parse；`document.md` 可人工预检。
3. 用标注问题集定量回答 inline vs separate（实验 A/B/C/D）。
4. caption 质量可度量：section 锚点、`quality_flag`、recall@k。
5. 行为变化沉淀进 `plan.md` / `logs.md`。

## 7. 非目标

- query 阶段多模态（E 组仅上限对照）。
- CLIP 图像向量主路径。
- 在 parse 层未稳定前重写 hybrid 或 agent 编排。

## 8. 成功标准

- 三 CLI `--help` 可运行。
- 单 PDF ingest 产出 4 种 chunk JSONL + `image_captions.jsonl` + 可选 Chroma。
- `eval_img` 在 sample 问题集上输出 gold image/chunk recall@k。
- **推荐工作流**：`parsers.redox_*` → 人工看 `document.md` → `ingest_img --parse-dir ...`。

## 9. 主要入口

```bash
# 推荐：先 parse，再 ingest
python -m parsers.redox_opendataloaderpdf tmp/raws/foo.pdf
uv run -m rag_pdfs.ingest_img tmp/raws/foo.pdf --out tmp/outs/foo --skip-parse --build-chroma
# 注：--parse-dir 为规划中的显式接口，当前可用 --skip-parse + 对齐的 --out 布局

uv run -m rag_pdfs.query_img --index tmp/outs/foo --strategy separate --show-evidence "question"
uv run -m rag_pdfs.eval_img --questions rag_pdfs/eval_questions.sample.jsonl --retrieve-only
```

依赖：Java 11+、DashScope（caption）、DeepSeek（answer）、本地 Qwen3 embedding（`QWEN3_EMBEDDING_06B_PATH`）。

## 10. 当前阶段与下一步

- **已完成**：ingest/query/hybrid/eval 骨架；2 份真实 PDF 索引；L1/L2 过滤；caption 缓存与 quality_flag；
  `pdf_layout` / `pdf_parser` 下沉至 `parsers/document/`（2026-07-07，依赖方向已修正）。
- **进行中**：与 `parsers/` 统一 parse 契约；减少 `ingest_img` monolith。
- **下一步**：
  1. `ingest_img --parse-dir` 用 `load_package` 显式接入 StaticParsePackage；
     chunk metadata / caption prompt / citation 改以 `section_path` 为主锚点
     （page/bbox 保留为物理坐标，见根 `strata.md` §3.2b）。
  2. 标注问题集 + eval A/B/C/D 定量结论。
  3. hybrid alpha 调参与阅读顺序验证。
  4. eval 闭环后：F 组（概念级检索）/ G 组（agentic markdown 导航）实验，
     见 `notes.md` 实验组表与根 `strata.md` §3.2c——本包定位从"固定 pipeline"
     演进为"检索工具箱 + 度量仪器"。

详细技术状态见 [`plan.md`](plan.md)。
