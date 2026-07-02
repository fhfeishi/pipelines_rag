# Agent RAG Growth Roadmap

> 目标：围绕 `pipelines_rag` 积累可落地的 Agentic RAG 产品能力，同时系统理解 agent 的底层原理与工程边界。

## 1. 背景判断

当前技术栈以 Python 为主、TypeScript 为辅，已经足够支撑完整的 AI 应用原型：

- Python：RAG、agent、eval、pipeline、后端服务、数据处理。
- TypeScript：UI、交互、dashboard、产品体验。
- Rust：作为系统性能、CLI 工具、并发任务和工程品味储备，暂不作为主线。

下一阶段不急于堆更多语言或框架，而是把注意力放到一个可验证、可追踪、可迭代的真实系统上。

这个仓库适合作为长期实验田，因为它解决的不是普通 RAG demo，而是技术文档中真实存在的难题：PDF 里的截图、表格、架构图如何进入检索和回答链路。

核心方向：

```text
Vision at ingestion, text at retrieval.
```

即在 ingest 阶段用 VLM 读图并生成 caption，在 query 阶段保持纯文本检索和回答。

## 2. 两条成长主线

### 2.1 产品线：做出 Agentic RAG 产品原型

产品线的目标是把 `pipelines_rag` 从实验 pipeline 推进成一个可使用、可观察、可评估的产品原型。

优先积累的能力：

- 高质量非结构化信息解析：PDF layout、图片抽取、表格/架构图/截图处理。
- 图片 RAG：L1/L2 image filter、VLM caption、inline vs separate caption indexing。
- 检索与回答：Chroma、hybrid BM25 + vector、evidence citation、answer grounding。
- 评估体系：labeled question set、recall@k、caption quality、evidence metrics。
- Agent 工作流：检索策略选择、query rewrite、证据充分性检查、失败分析。
- 可观测性：trace、token、latency、cost、run registry。
- 产品界面：PDF 上传、ingest 任务状态、query UI、证据图片预览、eval dashboard。
- 输出体验：必要时加入语音输出，但不作为早期核心。

### 2.2 原理线：理解 Agent 底层

原理线的目标不是追逐所有框架，而是理解 agent 为什么有效、为什么失控、如何调试、如何评估。

建议学习顺序：

```text
LLM 基础
-> tool calling / function calling
-> ReAct loop
-> state machine / graph workflow
-> memory / retrieval
-> eval / tracing
-> human-in-the-loop
-> multi-agent
```

需要反复回答的问题：

- agent 的状态在哪里？
- 每一步为什么调用这个工具？
- 工具失败如何恢复？
- 上下文如何压缩？
- 什么时候应该停止？
- 如何证明 agent workflow 比普通 workflow 更好？
- 如何观察 token、latency、cost、错误率？
- 如何做回归测试？

## 3. 项目内演进路线

### Phase 1：把图片 RAG 评估做扎实

目标：证明当前核心路线是否有效，而不是只跑通 demo。

重点任务：

- 建立 labeled question set，覆盖 `text-only` / `image-helpful` / `image-required`。
- 跑通 notes.md 中的 A/B/C/D 实验：
  - A：text-only baseline。
  - B：inline caption。
  - C：separate caption chunks。
  - D：separate without context。
- 记录 recall@k、gold image recall、caption quality、token、latency、cost。
- 分析失败原因：caption 错、图片过滤误伤、chunk 错、检索没召回、生成幻觉。

完成标准：

- `eval_img` 可以稳定输出策略对比结果。
- 每次行为变化都更新 `rag_pdfs/plan.md` 并追加 `rag_pdfs/logs.md`。
- 能说明 separate caption 是否优于 inline caption，以及优势出现在哪类问题上。

### Phase 2：服务化与产品原型

目标：把 CLI pipeline 包成一个可以使用的系统。

建议结构：

```text
Core Pipeline
  ingest / query / eval / caption cache / hybrid retrieval

Service Layer
  FastAPI / job status / run registry / trace id / token and cost accounting

UI Layer
  PDF upload / ingest status / query with evidence / image citation preview / eval dashboard
```

早期 UI 不追求复杂，但要让核心能力可见：

- 上传或选择 PDF。
- 运行 ingest。
- 查询问题。
- 展示 text evidence 和 image caption evidence。
- 点击查看原图、页码和 bbox。
- 查看 eval run 的指标变化。

### Phase 3：加入可观测性

目标：让每一次 RAG 或 agent run 都能被复盘。

关键对象：

- trace id。
- model call span。
- retrieval span。
- caption span。
- token usage。
- latency。
- cost。
- selected chunks。
- final answer。
- eval score。

这一步可以调研并选择 Langfuse、Phoenix 或 OpenTelemetry 风格的实现，但早期也可以先用本地 JSONL run log 建立数据结构。

### Phase 4：Agentic RAG 工作流

目标：让 agent 承担有价值的编排和诊断，而不是为了 agent 而 agent。

适合本项目的 agent 能力：

- 判断问题是否需要 image evidence。
- 自动选择 `text-only` / `inline` / `separate` 策略。
- 根据检索结果判断证据是否充分。
- 证据不足时改写 query 并重试。
- 对答案进行 groundedness check。
- 对 eval 失败案例生成原因分类。
- ingest 时发现低质量 caption，触发重试或人工复核。

早期 agent 应更像一个有状态、有工具、有追踪的 RAG 分析助手，而不是完全自治的聊天机器人。

### Phase 5：沉淀自己的 Agent Kernel 理解

目标：从实践反推底层抽象。

最终应形成自己的 mental model：

```text
Agent = model + tools + state + policy + memory + evaluator + observability
```

其中：

- model 负责语言理解和决策建议。
- tools 负责真实世界动作。
- state 负责跨步骤记忆和恢复。
- policy 负责边界、停止条件、权限和风险控制。
- memory/retrieval 负责长期知识。
- evaluator 负责判断是否真的变好。
- observability 负责让系统可调试、可优化、可回归。

## 4. 当前优先级

短期最值得投入的是：

1. 标注问题集。
2. A/B/C/D 实验。
3. recall@k 和 evidence 指标。
4. caption quality 评估。
5. query/eval 失败案例分析。

这些工作完成后，再进入 FastAPI、TypeScript UI、trace/token/cost dashboard，会更稳。

## 5. 判断标准

这条路线的核心判断不是“学了多少框架”，而是：

- 是否能把非结构化文档变成可靠证据。
- 是否能解释答案来自哪里。
- 是否能度量一次改动有没有变好。
- 是否能 debug agent/RAG 失败原因。
- 是否能把实验能力包装成可使用的产品原型。

最终目标是形成一个既能展示产品价值、又能训练底层理解的系统：一个围绕技术文档、图片证据和 agentic workflow 的高质量 RAG 原型。
