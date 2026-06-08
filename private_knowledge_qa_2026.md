# 2026 私有知识问答 Pipelines 选型指南

> 更新时间：2026-06-08  
> 适用场景：企业私有知识问答、技术文档问答、代码库问答、结构化业务数据问答、多模态 PDF RAG。

## 1. 核心判断

2026 年的私有知识问答不再适合默认从“切 chunk、建向量库、Top-K 检索”开始。更准确的行业状态是：

> RAG 没有退场，但它已经从默认答案变成了需要按场景选择的工程组件。

长上下文、Prompt Caching、Agentic Retrieval、Text-to-SQL 和 Deep Research 都已经成熟到足以改变架构选型。工程上应先判断数据规模、查询类型、权限审计要求、延迟预算和成本预算，再决定是否需要重型 RAG。

## 2. 对原观点的真实性审查

| 原观点 | 判断 | 修正说明 |
|---|---:|---|
| RAG 不再是默认答案 | 基本成立 | 长上下文、Prompt Caching、Agentic Search 已经让很多中小知识库不必先建向量库。但 RAG 仍是大规模、频繁更新、强权限控制、强证据追踪场景的主力。 |
| Claude 1M / Gemini 2M 可全量塞上下文 | 部分成立 | Claude 已发布 1M 上下文。Gemini 1.5 Pro 曾支持 2M，但当前 Google 模型页显示 Gemini 2.5 Pro 为 1M 上下文；具体能力必须按模型、API 与套餐确认。 |
| Prompt Caching 二次调用成本降低 90% | 基本成立但需限定 | OpenAI 和 Anthropic 都有缓存输入降本能力；最高降本取决于缓存命中率、稳定前缀、TTL、模型价格和动态内容位置。 |
| Cursor 正在弱化 Codebase Indexing，转向直接抓取 | 证据不足 | Cursor 官方仍描述 codebase indexing 会为文件计算 embeddings；更准确说法是代码场景正在走向“语义索引 + grep/regex + 文件读取 + agent loop”的混合搜索。 |
| Deep Research 证明非传统 RAG 架构优越 | 过度推断 | Deep Research 证明 planner/reader/aggregator 式 agent research 很强，但它并不否定 RAG；许多 deep research 流程本质上仍包含检索、阅读、筛选和证据聚合。 |
| Baseline RAG 禁用于生产 | 方向正确，表述过绝对 | 简单切片 + embedding + Top-K 可用于原型和低风险场景；生产级知识问答通常需要 hybrid search、rerank、metadata filter、citation、eval 和监控。 |
| Reranker 是最高性价比升级，效果比换 embedding 大 10 倍 | 方向正确，数字不可泛化 | Reranker 常常有效，尤其适合从较大候选集中筛出高相关证据；但“10 倍”必须由具体 eval 证明。 |
| Contextual Retrieval 可将召回失败率降到 1/3 | 基本有出处 | Anthropic 的实验中，contextual embedding + contextual BM25 + rerank 将 top-20 retrieval failure 从 5.7% 降到 1.9%，约减少 67%。但这是特定实验，不应无条件套用。 |
| 结构化数据应走 Text-to-SQL/API，而非向量化 | 成立 | 对 CRM、ERP、订单、库存、指标等结构化数据，SQL/API 工具调用通常比向量库更正确、更可审计。 |
| Eval 比换模型重要 | 成立 | 没有评估闭环的 prompt 调参不可控。Ragas、Phoenix、TruLens 等工具都围绕 RAG/Agent eval、trace 和质量评估展开。 |

## 3. 决策模型

启动私有知识问答项目前，先回答五个问题。

| 决策维度 | 关键问题 | 对架构的影响 |
|---|---|---|
| 数据规模 | 稳定知识是否能放进可用上下文窗口？ | 能放下且更新不频繁时，优先考虑长上下文 + 缓存。 |
| 查询类型 | 是精确定位、语义模糊、多跳推理，还是指标查询？ | 精确定位偏 keyword/grep；语义模糊偏 embedding；多跳偏 agent；指标偏 SQL/API。 |
| 数据形态 | 是非结构化文档、结构化数据库、代码、PDF 图表，还是混合数据？ | 不同数据形态应进入不同检索工具，而不是都塞进向量库。 |
| 可信度要求 | 是否必须给出处、页码、图片、字段、版本？ | 强审计场景需要 evidence model、citation verifier 和 trace。 |
| 成本延迟 | 是高频客服、内部助手，还是低频研究报告？ | 高频场景偏缓存和轻量检索；高价值低频任务可用 Deep Research。 |

## 4. 六类主流 Pipeline

### 4.1 长上下文 + Prompt Caching

适合：

- 几十到几百份稳定文档；
- 文档更新不频繁；
- 用户问题需要跨文档综合；
- 能接受较大的单次输入 token，但希望通过缓存降低多轮成本。

典型流程：

```text
文档整理 -> 稳定上下文拼接 -> Prompt Cache -> LLM 全量阅读 -> 带引用回答
```

优势：

- 工程简单，常常只是文档拼接与上下文管理；
- 保留完整语境，避免 chunk 丢失上下文；
- 对稳定前缀和重复查询，缓存能显著降低成本与延迟。

风险：

- 名义上下文窗口不等于可靠可用上下文；
- 长上下文仍可能出现位置偏差、遗漏、多跳推理衰减；
- 强权限隔离、多租户、高频更新场景不适合简单全量塞入。

### 4.2 生产级 RAG

适合：

- 万级或更大规模非结构化文档；
- 文档持续更新；
- 权限、来源、页码、版本必须可追踪；
- 查询语义模糊，无法只靠 SQL、grep 或 regex。

推荐流程：

```text
解析 -> 清洗 -> chunk -> metadata -> BM25 + vector hybrid
-> rerank -> context pack -> grounded answer -> citation check
```

生产级 RAG 的最低配置不应只是“向量库 Top-K”。更稳的组合是：

- BM25 处理术语、编号、函数名、产品型号、错误码；
- embedding 处理语义改写、同义表达和模糊查询；
- reranker 从较大候选集中筛出真正相关证据；
- metadata filter 处理权限、时间、文档类型、业务域；
- citation verifier 检查答案是否被证据支持；
- eval/trace/feedback 形成持续改进闭环。

### 4.3 Agentic Retrieval

适合：

- 代码库、日志、配置、API 文档；
- 多跳问题，例如“这个行为在哪里定义、在哪里调用、为什么失败”；
- 需要动态选择 grep、read file、SQL、API、web/search 等工具。

典型流程：

```text
问题 -> Agent 规划 -> 多轮搜索/读取/执行工具 -> 汇总证据 -> 回答
```

为什么代码场景尤其适合：

- 函数名、类名、import path、错误码天然适合精确搜索；
- agent 可以先 grep 定位，再读完整文件，而不是只读碎片 chunk；
- 调用链、定义跳转、测试失败分析通常需要多步工具调用。

风险：

- 成本和延迟高；
- 弱模型容易循环、误判工具结果或过早停止；
- 必须设置 step limit、工具白名单、trace、失败退出策略。

### 4.4 Text-to-SQL / Tool-to-API

适合：

- CRM、ERP、BI、工单、订单、库存、指标库；
- 问题可以落到表、字段、过滤条件、聚合函数；
- 需要确定性结果和可审计执行记录。

典型流程：

```text
自然语言 -> schema/table 选择 -> SQL/API 生成 -> 执行 -> 结果解释
```

工程原则：

- 不要把结构化数据打碎后塞入向量库；
- schema 描述、字段释义、枚举值、业务口径比 embedding 更重要；
- SQL 必须有权限控制、只读限制、执行前检查和结果校验；
- 面向外部用户时，不应无保护地暴露任意 Text-to-SQL。

### 4.5 Deep Research

适合：

- 高价值、低频、可等待的研究任务；
- 需要阅读大量材料并生成报告；
- 需要拆解子问题、多轮证据收集、冲突信息处理。

典型流程：

```text
Planner -> 子问题队列 -> Retriever/Reader 多轮读取
-> Evidence table -> Aggregator -> 报告
```

优势是质量上限高，缺点是成本、延迟、可控性和权限管理都更复杂。它适合研究报告，不适合高 QPS 客服问答。

### 4.6 多模态文档 RAG

适合：

- 技术 PDF、论文、图表、流程图、架构图、截图；
- 图像信息可能是 load-bearing evidence；
- 回答时需要引用页码、图片、表格或局部区域。

推荐流程：

```text
PDF -> layout parse -> text/table/image 分流 -> VLM caption
-> text chunk + image_caption chunk -> hybrid retrieval
-> image-aware rerank -> answer with text + image evidence
```

对技术 PDF 来说，关键不是“是否加图片 caption”，而是系统评估三种策略：

- `text_only`：只索引文本；
- `inline`：caption 插回正文；
- `separate`：caption 作为独立 `image_caption` evidence chunk。

评估问题应标注图片角色：

- `none`：不需要图片；
- `illustrative`：图片辅助理解；
- `load-bearing`：答案本身依赖图片、表格或流程图。

这也正是 `pipelines-rag` 当前 image-index RAG 轨道需要继续推进的核心实验。

## 5. 选型决策表

| 场景 | 首选方案 | 备选/增强 |
|---|---|---|
| 小型稳定知识库，几百份文档以内 | 长上下文 + Prompt Caching | 轻量 keyword index |
| 大规模非结构化文档库 | Hybrid RAG + Reranker | Contextual Retrieval |
| 技术 PDF，图表重要 | 多模态文档 RAG | image-aware rerank |
| 代码库问答 | Agentic Retrieval + grep/read/index | 结构化代码图谱 |
| CRM/ERP/指标查询 | Text-to-SQL 或 Tool-to-API | 语义层/指标层 |
| 高价值研究报告 | Deep Research | RAG/long context 作为 reader |
| 强权限、多租户、强审计 | RAG / Tool-based retrieval | citation verifier |
| 高频低延迟客服 | 缓存 + 小模型 + 精简 RAG | 问答对缓存 |

## 6. 推荐基线架构

如果从零搭建企业私有知识问答，推荐做成路由式架构，而不是单一路线：

```text
用户问题
  -> Query Router
    -> 结构化问题：SQL/API
    -> 小型稳定知识：Long Context + Cache
    -> 大规模文档：Hybrid RAG + Rerank
    -> 代码/多跳：Agentic Retrieval
    -> 高价值研究：Deep Research
  -> Evidence Aggregation
  -> Answer Generation
  -> Citation Verification
  -> Eval / Trace / Feedback
```

路由器不必一开始就复杂。最小可行版本可以先用规则：

- 包含指标、数量、时间范围、排名：优先 SQL/API；
- 包含文件路径、函数、报错、配置：优先 agentic code search；
- 命中文档集合且规模较大：走 RAG；
- 知识库小且稳定：走 long context；
- 用户明确要求报告、对比、调研：走 deep research。

## 7. 评估体系

私有知识问答的 eval 应同时覆盖检索和生成。

### 7.1 检索指标

| 指标 | 含义 |
|---|---|
| Recall@K | gold evidence 是否进入前 K 条 |
| MRR | 正确证据首次出现的位置 |
| image caption recall@K | 多模态文档中，关键图片 caption 是否被召回 |
| metadata accuracy | 页码、文档、版本、权限、图片 ID 是否正确 |
| context budget | 每次回答送入模型的字符数/token 数 |

### 7.2 生成指标

| 指标 | 含义 |
|---|---|
| answer correctness | 答案是否正确 |
| faithfulness | 答案是否被证据支持 |
| citation accuracy | 引用是否指向真实支撑内容 |
| abstention quality | 无证据时是否拒答或说明不足 |
| user utility | 是否解决真实用户任务 |

### 7.3 最小评估集

建议从真实日志中抽取至少 100 个 query，并标注：

- 用户问题；
- gold answer；
- gold evidence 文档/页码/chunk/image_id；
- 查询类型：精确、语义、多跳、结构化、研究型；
- 图片角色：`none / illustrative / load-bearing`；
- 权限域和时间有效性。

## 8. 数据质量要求

80% 的私有知识问答失败不是因为模型不够强，而是数据进入 pipeline 前已经损坏。

必须优先处理：

- PDF 乱码、断行、页眉页脚污染；
- 表格被切碎后失去行列语义；
- 图片、截图、流程图没有 caption；
- 文档版本混乱，新旧制度同时召回；
- metadata 缺失，无法按权限、时间、业务线过滤；
- citation 无法追溯到页码、图片或原始字段。

对技术 PDF 尤其要注意：

- 表格应尽量结构化提取；
- 架构图和流程图应在 ingest 阶段生成 caption；
- 图片路径、`image_id`、`page`、`chunk_type`、`chunk_id`、`source_pdf` 必须一致；
- 保守过滤图片，宁可多 caption，也不要丢掉 load-bearing evidence。

## 9. 对 `pipelines-rag` 的落地建议

当前项目目标是测试图片 caption 应该 inline 进入正文，还是作为独立 `image_caption` evidence chunk。结合 2026 的架构判断，建议继续聚焦以下路线：

1. 扩充标注问题集  
   为每个问题标注 `none / illustrative / load-bearing` 图片角色，并补充 `gold_image_ids` 与 `gold_chunk_ids`。

2. 固定 A/B/C 对照  
   在相同 PDF、相同 chunk 参数、相同 embedding、相同 top-k 下比较 `text_only`、`inline`、`separate`。

3. 调 hybrid alpha  
   系统比较 `alpha=0.3/0.5/0.7` 对 image caption recall@K、context 膨胀和 answer quality 的影响。

4. 先测 hybrid，再决定 reranker  
   如果 hybrid fusion 已能稳定召回 image evidence，reranker 可以暂缓；如果 image chunk 与 text chunk 互相挤压，再接 cross-encoder 或 LLM rerank。

5. JSONL 输出保留证据细节  
   每条 eval 结果应保留 retrieved chunks、`chunk_type`、`image_id`、`page`、分数和最终答案，方便失败分析。

## 10. 最终结论

2026 年私有知识问答的稳健路线是：

> 小知识库优先长上下文，大知识库优先生产级 RAG，结构化数据走 SQL/API，代码和复杂多跳走 Agentic Retrieval，高价值低频任务走 Deep Research。所有路线都必须以 eval、数据质量和证据可追踪为中心。

对本项目而言，最重要的下一步不是追逐更复杂的 RAG 组件，而是把 image-index RAG 的评估闭环做扎实：标注问题集、跑策略矩阵、量化 image caption recall@K，再决定 inline、separate、hybrid alpha 和 reranker 的取舍。

## 参考来源

- Anthropic: [1M context is now generally available for Claude Opus 4.6 and Claude Sonnet 4.6](https://claude.com/blog/1m-context-ga)
- Anthropic: [Prompt caching](https://www.anthropic.com/news/prompt-caching)
- Anthropic: [Contextual Retrieval](https://www.anthropic.com/research/contextual-retrieval)
- OpenAI: [Prompt caching](https://platform.openai.com/docs/guides/prompt-caching)
- OpenAI: [Introducing deep research](https://openai.com/index/introducing-deep-research/)
- Google Cloud: [Gemini models on Vertex AI](https://docs.cloud.google.com/vertex-ai/docs/generative-ai/learn/models)
- Google Cloud: [Long context on Vertex AI](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/long-context)
- Cursor: [Securely indexing large codebases](https://cursor.com/blog/secure-codebase-indexing)
- Cursor: [Fast regex search: indexing text for agent tools](https://cursor.com/blog/fast-regex-search)
- Cohere: [Reranking with Cohere](https://docs.cohere.com/v2/docs/reranking-with-cohere)
- LlamaIndex: [Query Pipeline for Advanced Text-to-SQL](https://docs.llamaindex.ai/en/stable/examples/pipeline/query_pipeline_sql/)
- Ragas: [Available metrics](https://docs.ragas.io/en/latest/concepts/metrics/available_metrics/)
- Arize Phoenix: [LLM tracing and evaluation](https://phoenix.arize.com/)
- TruLens: [Evaluate LLM apps, agents, and RAG](https://www.trulens.org/)
