# Agent RAG Growth Roadmap

> 目标：围绕 `pipelines_rag`，从一个个可证伪的小实验开始，训练问题定义、基线设计、度量和失败归因能力；先判断 RAG 何时真的比长上下文 LM 有价值，再逐步积累图文 RAG 产品能力。Agentic RAG 是长期可能方向，不是当前交付目标。

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

## 2. 先回答：什么时候真的需要 RAG？

一个必须保留的怀疑是：如果一本书能够完整放进旗舰模型的长上下文，为什么不直接把整本书交给 LM？

RAG 不应被当作默认正确的架构。对“单本、静态、能够放进上下文、查询次数不多”的资料，直接长上下文 LM 是必须击败的基线。RAG 增加了解析、切块、索引、检索和重排等环节，也把错误从“模型没有读懂”扩展成“内容没有解析好、切块切错、证据没有召回、排序不对”。如果它不能在准确性、证据质量、成本或延迟上带来可重复的收益，就没有必要仅因为行业惯例而建设复杂 pipeline。

RAG 可能开始有价值的条件，目前只作为**待实验验证的假设**：

- 语料接近或超过模型的实际可用上下文，无法稳定地一次输入。
- 文档很多、无关信息很多，检索能够减少注意力稀释。
- 同一知识库会被反复查询，预处理和索引成本可以被摊薄。
- 知识频繁更新，需要增量更新，而不是反复重送全部上下文。
- 答案必须给出稳定、可检查的页码、段落或图片证据。
- 数据存在权限、租户或范围边界，不能把全部内容交给一次模型调用。
- 问题依赖 PDF 中的表格、截图或架构图，纯文本长上下文本身已经丢失关键信息。

### 2.1 最小对照实验

第一步不建设更复杂的系统，只用一本有文字和图片的技术书或 PDF，准备约 20–30 个小而有代表性的问题，覆盖：局部事实、跨章节综合、image-helpful、image-required 和“文档中没有答案”。在同一个模型、同一版资料和尽量一致的回答要求下比较：

- A：长上下文 LM，直接提供整本文档。
- B：最简单的 text-only RAG，只提供 top-k 检索片段。
- C：当前 image-aware RAG，加入图片抽取、caption 和 hybrid retrieval。

只记录足够支持决策的指标：答案正确率、证据是否正确且充分、无答案时能否拒答、token/延迟/成本，以及失败发生在解析、检索还是生成阶段。必要时重复运行少量样本观察稳定性，不先搭建完整评测平台。

这个实验只回答一个问题：**在一本书的规模上，RAG 是否在至少一个重要维度上形成稳定、值得工程成本的收益？** 如果 A 与 B/C 相当或更好，且成本可接受，就暂时使用长上下文；随后只改变一个变量，把语料扩大到“明显小于、接近、超过可用上下文”三个量级，寻找 RAG 真正出现优势的拐点。

### 2.2 升级门槛

- 只有长上下文基线已经跑过，才讨论 RAG 的收益。
- 只有收益能在固定问题集上复现，才增加新的检索、重排或索引策略。
- 只有 image-required 问题证明纯文本方案存在稳定缺口，才继续投入图片 pipeline。
- 只有确定性 RAG 反复出现可归类、且可能通过动态决策解决的失败，才小范围尝试 Agentic RAG。

每一轮都遵循同一个工程闭环：

    问题 -> 假设 -> 最小基线 -> 单一变量 -> 固定评测 -> 失败归因 -> 下一步决策

## 3. 两条成长主线

### 3.1 工程主线：做出可验证的图文 RAG 原型

工程主线的目标不是尽快堆出完整系统，而是让 pipelines_rag 的每一层都能被单独检查、比较和改进；当核心路线被数据支持后，再逐步推进成可使用、可观察的产品原型。

优先积累的能力：

- 高质量非结构化信息解析：PDF layout、图片抽取、表格/架构图/截图处理。
- 图片 RAG：L1/L2 image filter、VLM caption、inline vs separate caption indexing。
- 检索与回答：Chroma、hybrid BM25 + vector、evidence citation、answer grounding。
- 评估体系：labeled question set、recall@k、caption quality、evidence metrics。
- 可观测性：trace、token、latency、cost、run registry。
- 产品界面：PDF 上传、ingest 任务状态、query UI、证据图片预览、eval dashboard。
- Agent 工作流只在确定性 pipeline 的收益和失败边界清楚后再评估。

### 3.2 方法线：训练解决问题的工程化思路

方法线的目标不是追逐 RAG 或 Agent 框架，而是学习如何把一个模糊判断变成可运行、可反驳、可复盘的实验。

建议学习顺序：

    定义问题
    -> 写出可证伪假设
    -> 选择最简单基线
    -> 控制变量
    -> 固定数据与指标
    -> 对失败分层归因
    -> 做一个最小改动
    -> 回归验证

需要反复回答的问题：

- 我要验证的具体判断是什么，什么结果会推翻它？
- 当前最简单、最强的基线是什么？
- 这一轮只改变了哪个变量？
- 指标是否真的对应用户问题，而不是只方便实现？
- 错误来自数据、解析、检索、生成，还是评估本身？
- 收益是否足以覆盖新增的复杂度、token、延迟和维护成本？
- 下一步应该继续、停止，还是换问题？

Agent 的 tool calling、state、memory、eval 和 observability 可以在后续遇到真实需求时逐步学习；现在不把“做成 Agentic”本身当作成功标准。

## 4. 项目内演进路线

### Phase 0：长上下文 LM vs RAG

目标：先证明 RAG 在当前问题上是否值得存在。

- 选定一本代表性技术书或 PDF 和约 20–30 个固定问题。
- 跑长上下文 LM、text-only RAG、image-aware RAG 三组最小对照。
- 记录质量、证据、拒答、token、延迟、成本和失败归因。
- 根据结果决定：保留简单方案、扩大语料寻找拐点，或继续图片 RAG 实验。

完成标准：能够用数据说明“一本书是否需要 RAG”，并明确下一轮只增加哪个规模或问题变量。

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

### Phase 4：有证据后再考虑 Agentic RAG

目标：只让 agent 处理确定性 pipeline 已经暴露、且确实需要动态决策的环节。

最早只考虑少量候选：query rewrite、证据充分性检查和失败分类。每项能力都必须有确定性基线、停止条件和独立评测；如果没有稳定的质量或效率收益，就继续使用普通 workflow。

### Phase 5：沉淀自己的工程判断

目标：从一轮轮实验反推系统边界，而不是从框架名称反推需求。

    Experiment = question + hypothesis + baseline + variable + metric + decision
    System = data contract + pipeline + evaluator + observability

真正的积累是：知道何时该增加一个组件、它解决哪类已观察到的失败、如何证明它有效，以及何时应该删掉它。做好长期系统需要持续积累和足够驱动；当前阶段先把这个最小工程闭环练熟。

## 5. 当前优先级

短期最值得投入的是：

1. 选择一本代表性资料，建立 20–30 个固定问题的小评测集。
2. 先跑整本书的长上下文 LM 基线。
3. 用相同问题跑 text-only 与 image-aware RAG 对照。
4. 比较答案、证据、拒答、成本和延迟，并逐例做失败归因。
5. 只根据实验暴露的问题，选择扩大知识库规模或继续 A/B/C/D、caption quality、recall@k 实验。

当前不急于进入 FastAPI、TypeScript UI、复杂 dashboard 或 Agent workflow。先用最小记录方式完成实验闭环；只有数据证明需要长期重复运行时，再把手工记录逐步产品化。

## 6. 判断标准

这条路线的核心判断不是“学了多少框架”，而是：

- 是否能说明当前任务为什么需要 RAG，而不是长上下文 LM。
- 是否设置了足够强、足够公平的基线。
- 是否一次只改变一个主要变量，并接受实验推翻原假设。
- 是否能把非结构化文档变成可靠证据。
- 是否能解释答案来自哪里。
- 是否能度量一次改动有没有变好。
- 是否能 debug RAG 失败原因。
- 是否能把实验能力包装成可使用的产品原型。

最终目标不是证明 RAG 或 Agentic RAG 必然正确，而是形成一套能持续判断“何时该用什么复杂度”的工程能力，并在证据支持时，把它沉淀成围绕技术文档和图片证据的高质量 RAG 原型。
