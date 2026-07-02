# rag_pdfs Launch Charter

> 模板原文见仓库历史（`strata_example.md`）；本文件为 `rag_pdfs` 的已填写章程。

## 1. 项目一句话

把技术文档 PDF 中的图片（截图 / 表格 / 架构图）在 ingest 阶段读成可检索、可引用的文本证据，让 RAG 能回答 image-helpful / image-required 问题；同时把项目推进成一个可观察、可评估、可产品化的 Agentic RAG 原型。

## 2. 背景

- 传统 text-only RAG 在 ingest 时丢弃图片，导致答案缺乏可操作性或直接答错。
- 核心结论（见 `notes.md`）：**Vision at ingestion, text at retrieval** —— VLM 只在 ingest 读图一次生成 caption，query 阶段纯文本检索。
- 已有资产：完整 ingest / query / eval CLI、L1/L2 图片过滤、hybrid BM25+vector 检索、两份真实 PDF 的 caption 与 Chroma 索引。
- 2026-06-10 起本目录为 canonical 包（`rag_langchain/` 为历史探索材料）。
- 根目录 `agent_rag_growth_roadmap.md` 将本项目定位为两条主线：产品线（Agentic RAG 原型）和原理线（理解 tool calling / state / retrieval / eval / tracing）。
- 当前新增前置重点：先把解析工具层做稳，形成 `source data -> PDF -> layout JSON + images -> 图文编排 Markdown` 的可检查产物，再进入 caption、chunk、index、eval。

## 3. 核心目标

1. 维持端到端闭环：PDF → parse → filter → caption → chunk → Chroma → query。
2. 建立稳定的解析中间层：PDF → layout JSON / images / elements.jsonl / images.jsonl / image-aware Markdown。
3. 用标注问题集定量回答 inline vs separate caption 索引策略（notes.md 实验 A/B/C/D）。
4. caption 质量可度量：section 锚点覆盖率、quality_flag、recall@k。
5. 所有行为变化沉淀进 `plan.md` / `logs.md`。

## 4. 非目标

- 不做 query 阶段多模态（图片不进 query payload；E 组仅作上限对照）。
- 不用 CLIP 图像向量作主路径。
- 暂不做生产部署 / API 服务化、cross-encoder reranker（hybrid 已替代）。

## 5. 成功标准

- `uv run -m rag_pdfs.{ingest_img,query_img,eval_img} --help` 可运行。
- 单 PDF ingest 产出 4 种 chunk JSONL + `image_captions.jsonl` + Chroma collection。
- `eval_img` 在标注问题集上输出 gold image/chunk recall@k 与 evidence 统计。
- 行为变化均有 `logs.md` 追加记录与最小验证命令。

## 6. 系统流程

```text
PDF
-> opendataloader-pdf 解析（layout JSON + PNG）
-> 图文编排 Markdown（可人工检查阅读顺序、图片位置、metadata）
-> LayoutElement 序列（阅读顺序 / section title）
-> L1/L2 图片过滤（skip 写 skipped_images.jsonl）
-> VLM caption（带上下文，缓存到 image_captions.jsonl，quality_flag 校验）
-> text_only / inline / separate / mixed 四种 chunk
-> Qwen3 embedding + Chroma（4 collection）
-> query：hybrid BM25+vector 检索 -> DeepSeek 生成（区分 text/image 证据）
-> eval：策略 × 检索 × alpha 矩阵，recall@k 与 evidence 指标
```

## 7. 主要入口

```bash
uv run -m rag_pdfs.ingest_img tmp/raws/foo.pdf --out tmp/outs/foo --build-chroma
uv run -m rag_pdfs.query_img --index tmp/outs/foo --strategy separate --show-evidence "question"
uv run -m rag_pdfs.eval_img --questions rag_pdfs/eval_questions.sample.jsonl --retrieve-only
```

核心依赖：Java 11+（opendataloader-pdf）、DashScope key（qwen-vl-max caption）、DeepSeek key（answer LLM）、本地 Qwen3 embedding（`QWEN3_EMBEDDING_06B_PATH`）。

## 8. 文档契约

| 文件 | 职责 |
|------|------|
| `plan.md` | 技术路线、阶段状态、待办优先级、设计决策 |
| `logs.md` | 追加式运行记录（真实命令、结果、修复、决策） |
| `notes.md` | 背景研究与实验设计（博客结论、A/B/C 方案） |
| `strata.md` | 本章程：为什么存在、流程、成功标准 |
| 仓库根 `AGENTS.md` | 跨项目 agent 规则（parsers + rag_pdfs） |
| 仓库根 `agent_rag_growth_roadmap.md` | 产品线与 agent 原理成长路线 |
| 仓库根 `parsers/plan.md` | 解析工具箱与输出约定 |
| 仓库根 `git_notes.md` | git 历史问题与恢复记录 |

## 9. 当前阶段与下一步

- 当前阶段：ingest / query / hybrid / eval 骨架全部跑通，2 份真实 PDF 已索引；解析工具箱已有 `parsers/redox_opendataloaderpdf.py` 初版。
- 当前优先级：优先处理数据解析为 PDF、layout JSON、图片资产和图文编排 Markdown 的 tool，使解析结果可人工检查、可复用、可接入 RAG ingest。
- 后续顺序：解析/Markdown tool 稳定 → 标注问题集（text-only / image-helpful / image-required）→ `eval_img` 定量 A/B/C/D → hybrid alpha 调参 → 阅读顺序验证。

## 10. 产品化与 Agent 成长路线

本项目同时作为 Agentic RAG 产品原型和 agent 工程能力训练场：

- 产品线：把图片 RAG pipeline 推进到可使用、可观察、可评估的产品原型，逐步补齐服务层、UI、trace、token/cost 观测和 eval dashboard。
- 原理线：围绕 tool calling、ReAct loop、state machine / graph workflow、memory / retrieval、eval / tracing、human-in-the-loop 等主题理解 agent 底层。
- 工程原则：先让解析产物可检查，再让图片 RAG 的 labeled question set、A/B/C/D 实验、recall@k、caption quality 和失败分析做扎实，最后进入更复杂的 agent workflow 和产品化界面。

详细路线见仓库根目录 [`agent_rag_growth_roadmap.md`](../agent_rag_growth_roadmap.md)。
