# 精致小型 RAG 顶层设计

> 状态：v1 设计基线，2026-08-29。  
> 范围：模型运行时、query workflow、prompt、会话上下文、LangSmith 可观测性。  
> 上游解析契约与图片 ingest 的 canonical 决策仍以根 strata.md、rag_pdfs/strata.md
> 和 rag_pdfs/plan.md 为准。

## 1. 结论先行

本项目不应复制一个大而全的 RAG 平台。最适合当前仓库的是：

1. 保留现有 Layer 0–3：StaticParsePackage → 图片 caption → chunk → hybrid retrieve → eval。
2. LangChain 只承担稳定接口和可组合组件：Document、Embeddings、BaseChatModel、
   prompt、Runnable、vector store。
3. LangGraph 先做有上限、可复盘的确定性 query workflow，不直接上自治 Agent。
4. LangSmith 从第一版 graph 开始记录 trace，并复用现有 labeled JSONL 做离线评估；
   本地 JSONL 仍是可离线、可版本化的事实源。
5. 不为普通 chat、flash、VLM 各写一套重复类。它们都是 chat model，差别用
   role binding 和 capabilities 表达。Embedding 保持独立接口。
6. 本地模型同时支持 cached repo 和 explicit local path。生产服务优先把本地推理
   放到 vLLM、Xinference 或 Ollama 后面，再通过 OpenAI-compatible API 接入；
   进程内 Transformers 仅作为学习和单用户开发模式。

## 2. 开源样本：产品成熟度与技术贴合度分开看

Star 为 2026-08-29 GitHub API 快照，只用于粗略衡量社区规模，不代表架构质量。

| 项目 | Star | 与本项目最相关的真实代码 | 取其所长 | 不照搬 |
|---|---:|---|---|---|
| [RAGFlow](https://github.com/infiniflow/ragflow) | 89,544 | [模型注册](https://github.com/infiniflow/ragflow/blob/main/rag/llm/__init__.py)、[embedding adapter](https://github.com/infiniflow/ragflow/blob/main/rag/llm/embedding_model.py)、[ingest composition root](https://github.com/infiniflow/ragflow/blob/main/internal/ingestion/task/embedder.go) | 按模型类型分接口；provider registry；batch、截断、错误、token 统计在 adapter 内统一；composition root 注入依赖 | 动态反射注册和大量 provider 类对小项目过重 |
| [AnythingLLM](https://github.com/Mintplex-Labs/anything-llm) | 65,348 | [LLM、embedder、vector DB 工厂](https://github.com/Mintplex-Labs/anything-llm/blob/master/server/utils/helpers/index.js) | LLM、embedding、vector store 是独立可替换轴；产品层只拿统一接口 | 巨型 switch 和重复 provider/class switch 会随 provider 数量线性膨胀 |
| [LightRAG](https://github.com/HKUDS/LightRAG) | 39,257 | [EmbeddingFunc](https://github.com/HKUDS/LightRAG/blob/main/lightrag/utils.py)、[LightRAG composition object](https://github.com/HKUDS/LightRAG/blob/main/lightrag/lightrag.py)、[OpenAI-compatible 示例](https://github.com/HKUDS/LightRAG/blob/main/examples/lightrag_openai_compatible_demo.py) | embedding 维度、token 上限、query/document 非对称能力属于索引契约；LLM/embedding/storage 依赖注入；工作区隔离 | graph RAG 数据结构和本项目图片证据问题不同 |
| [Langchain-Chatchat](https://github.com/chatchat-space/Langchain-Chatchat) | 38,590 | [模型平台配置](https://github.com/chatchat-space/Langchain-Chatchat/blob/master/libs/chatchat-server/chatchat/settings.py)、[ChatOpenAI 与 Embeddings 构造](https://github.com/chatchat-space/Langchain-Chatchat/blob/master/libs/chatchat-server/chatchat/server/utils.py) | 中国本地模型场景；把 Xinference、Ollama、OneAPI 统一成 OpenAI-compatible 平台；模型平台与用途配置分离 | 0.3 已主动停止在 Web 进程内按本地路径加载大模型；这说明 direct local 更适合作为开发模式，不宜作为生产默认 |
| [Chat LangChain](https://github.com/langchain-ai/chat-langchain) | 6,441 | [角色模型、retry/fallback](https://github.com/langchain-ai/chat-langchain/blob/master/src/agent/config.py)、[middleware 编排](https://github.com/langchain-ai/chat-langchain/blob/master/agent.py)、[上下文摘要 prompt](https://github.com/langchain-ai/chat-langchain/blob/master/src/prompts/context_summary_prompt.py)、[LangSmith connector](https://github.com/langchain-ai/chat-langchain/blob/master/connectors/langsmith.py) | 模型 role registry；prompt 单独版本化；摘要、retry、fallback 是 workflow/middleware；LangSmith key 不下发浏览器 | 当前版本是文档 Agent，不是本项目固定 ingest/query RAG 的直接模板 |

另外筛选了 Dify、Onyx、Quivr 等高星项目。它们能证明完整产品需要任务状态、权限、
会话、provider 和 UI 边界，但体量或技术栈与当前仓库不匹配，不作为首版代码模板。

## 3. 从样本中提炼出的硬约束

### 3.1 模型实例化不能散落在 CLI

M0 之前，ingest_img.py 与 query_img.py 分别直接构造 ChatOpenAI，
query_img.py 和 eval_img.py 又反向 import ingest_img.build_embeddings，曾造成：

- 模型切换必须改多个入口；
- API、本地、缓存三种来源的校验规则重复；
- eval 很难可靠记录本次到底用了哪个模型；
- Chroma 索引与 embedding 维度、归一化设置没有显式绑定。

M0 新增 model_runtime.py 作为统一模型边界；M1 已新增 runtime.py composition
root，三个 CLI 现在按 role 取实例，不再从 ingest 反向导入模型构造。

### 3.2 embedding 是索引 schema，不只是一个可热切换依赖

以下任意变化都必须视为新索引版本：

- provider/runtime；
- model 与 revision；
- dimensions；
- normalize_embeddings；
- document/query 前缀或 encode kwargs。

model_runtime.embedding_fingerprint() 生成不含密钥的稳定指纹；M1 已在 Chroma
构建完成后写入 index_manifest.json，并在 query/eval 打开 collection 时校验。
fingerprint mismatch 必须拒绝；没有 manifest 的旧索引默认告警兼容，也可启用严格拒绝。

### 3.3 cache 路径不应写入 PATH

PATH 是可执行文件搜索路径。模型缓存应使用：

- HF_HOME；
- HUGGINGFACE_HUB_CACHE；
- MODELSCOPE_CACHE。

model_runtime.configure_model_cache_environment() 已按此实现，而且使用 setdefault，
不覆盖部署环境的显式设置。local 模式直接使用明确目录，不依赖下载来源。

## 4. 模型抽象：kind、role、runtime、source 四轴

### 4.1 Kind

| kind | LangChain 返回接口 | 最小功能 |
|---|---|---|
| chat | BaseChatModel / Runnable | invoke、ainvoke、stream；vision 仍通过多模态 message 调同一接口 |
| embedding | Embeddings | embed_documents、embed_query 及异步对应接口 |

不要额外创建 FlashModel 或 VLMModel 基类。它们不是新的调用协议。

### 4.2 Role

| role | 用途 | 推荐温度 | 是否进入 query 主链 |
|---|---|---:|---|
| embedding | 文档与 query 向量 | 不适用 | 是 |
| answer | 基于证据回答 | 0 | 是 |
| flash | 便宜、快速的通用小任务 | 0 | 间接 |
| query_rewrite | 多轮问题转独立检索 query | 0 | 可选节点 |
| summary | 压缩旧会话 | 0 | 可选节点 |
| vision_ingest | ingest 时图片 caption | 0 | 只在 ingest |
| evaluator | 离线 judge | 0 | 不进线上回答链 |

一个物理模型可以绑定多个 role。vision_ingest role 必须声明 vision capability；
其余能力如 structured_output、tool_calling、streaming 也由 catalog 声明并在
workflow 构建时校验。

### 4.3 Runtime 与 Source

| runtime | source | 首版支持 | 说明 |
|---|---|---|---|
| openai_compatible | api | 已有骨架 | ChatOpenAI / OpenAIEmbeddings；base_url 可指云 API 或本地推理服务 |
| sentence_transformers | local | 已有骨架，推荐本地 embedding | 任意来源下载好的模型仓库目录 |
| sentence_transformers | huggingface | 已有骨架 | repo id + cache_root + pinned revision |
| sentence_transformers | modelscope | 已有骨架 | snapshot_download 后把本地目录交给 sentence-transformers；需可选 modelscope 依赖 |
| transformers | local/huggingface/modelscope | 已有骨架，开发模式 | HuggingFacePipeline + ChatHuggingFace；首版只承诺文本 chat |

示例 catalog 在 configs/models.example.toml。API key 只存 configs/.env 或进程环境，
catalog 仅保存 api_key_env 名称。
ModelScope cached 模式是可选安装：uv sync --extra modelscope；默认安装不引入下载器。

## 5. Prompt 设计

Prompt 必须单独设计，但不需要先做复杂 Prompt CMS。首版规则：

1. 本地源码是 canonical，可 code review、可离线启动。
2. 每个 prompt 有稳定逻辑名和版本，如 rag-answer-v1。
3. trace metadata 记录 prompt 版本；若以后从 LangSmith Hub 发布，再额外记录
   prompt owner/name/commit。
4. model class 不拥有业务 prompt。模型负责调用，workflow 决定任务。
5. answer、query rewrite、history summary、VLM caption、judge 分开；
   不用一个全能 system prompt。

已新增 prompts.py：

- ANSWER_PROMPT：保持现有 query 行为；
- QUERY_REWRITE_PROMPT：仅把多轮指代转成独立检索 query；
- HISTORY_SUMMARY_PROMPT：保留决定、约束、标识符和未解决项；
- PROMPT_VERSIONS：供 trace/eval 写入。

## 6. LangGraph：先做确定性 workflow，不急着做 Agent

LangChain 官方把固定 retrieve→generate 归为 2-step RAG，把带 query enhancement、
retrieval validation、有限重试的流程归为 hybrid RAG。当前项目最适合后者，但每条
路径必须有调用次数上限。

目标 state：

    QueryState
      messages                 原始短期会话
      history_summary          旧消息摘要，不是证据
      question                 本轮原始问题
      search_query             独立检索 query
      retrieval_attempt        0 或 1
      evidence                 Document 列表，唯一一次事实源
      retrieval_scores         vector/BM25/fused
      context                  token budget 后的证据文本
      answer                   最终答案
      citations                image_id/chunk_id/page
      diagnostics              timing、token、拒答原因

首版 graph：

    START
      -> prepare_query
      -> retrieve
      -> evidence_gate
           sufficient -> assemble_context
           insufficient 且未重写 -> rewrite_query -> retrieve
           insufficient 且已重写 -> abstain
      -> generate
      -> citation_check
      -> END

关键限制：

- 最多两次 retrieval；默认仍只检索一次。
- evidence_gate 首先用确定性信号：top score、证据数量、source/metadata 完整性；
  没有数据证明前不加一次昂贵 LLM grader。
- generate 只消费 state.evidence，不能在 chain 内再次检索。
- show-evidence 复用同一 evidence 和 score，不再第三次算分。
- graph 是 orchestration，不改变现有 A/B/C/D 检索实验变量。

这也直接修复根 strata.md 已记录的 query 最多重复检索三次问题。

## 7. 会话、上下文压缩与记忆是三件事

### 7.1 Short-term conversation state

用 LangGraph checkpointer 按 thread_id 保存。开发期 InMemorySaver；
单机持久化可用 SQLite saver；服务化后用 Postgres saver。官方 persistence
模型会在 graph step 后保存 checkpoint，支持恢复和调试。

### 7.2 Conversation compression

当消息接近模型上下文阈值时：

- summary role 压缩旧消息；
- 最近 N 条消息原样保留；
- summary 写回 state.history_summary；
- summary 只帮助理解指代，不算知识库证据，不能被 citation 引用。

阈值应按 token 或模型 profile，而不是固定消息条数。官方
SummarizationMiddleware 可供 Agent 路径复用；自定义 StateGraph 可用独立 summary node。

### 7.3 Retrieval context compression

这是另一条预算：

- mixed evidence 去重；
- 按 fused score 与 chunk type 分配预算；
- caption chunk 不切碎；
- text chunk 可按 section_path 聚合；
- 超预算先降 fetch_k/final_k 或做确定性截断，再评估是否需要 reranker。

不要用 conversation summarizer 改写证据，否则 citation 失去可追溯性。

### 7.4 Long-term memory

跨 thread 的用户偏好、事实或项目记忆暂缓。它需要用户/租户 scope、写入策略、
删除和隐私边界。当前产品问题是文档证据 RAG，不能让未治理的个人 memory 污染答案。

## 8. LangSmith：trace 与 eval 同时设计

LangChain/LangGraph 可通过 LANGSMITH_TRACING=true 自动追踪；项目名由
LANGSMITH_PROJECT 指定。每次 root run 还应写以下 metadata：

| 类别 | 字段 |
|---|---|
| 代码 | git_sha、pipeline_version、graph_version |
| 解析 | parse_manifest_id、parser、source_doc_id |
| 索引 | index_id、embedding_fingerprint、chunk_strategy |
| 检索 | retrieval_mode、alpha、fetch_k、top_k |
| 模型 | role_to_model_id，不含 key/base64 图片 |
| Prompt | answer/query_rewrite/summary/caption version |
| 会话 | thread_id 的不可逆或内部 ID，不放用户隐私 |
| 实验 | question_id、image_role、dataset_split |

推荐 span 名：

    rag.query
      rag.prepare_query
      rag.retrieve
        rag.embed_query
        rag.bm25
        rag.fuse
      rag.evidence_gate
      rag.assemble_context
      llm.answer
      rag.citation_check

现有 eval_questions JSONL 继续作为本地 canonical 标注集，再同步为 LangSmith dataset。
指标分层：

- retrieval：gold chunk/image recall@k、MRR、evidence 数；
- generation：correctness、groundedness、citation validity、abstention；
- system：token、latency、cost、失败类型；
- 图片：caption quality_flag、section anchor coverage、image-required 子集 recall。

先跑 offline evaluation 比较版本；线上 trace 只做抽样 evaluator，失败样本再回灌数据集。
LangSmith 是观测和评估层，不应成为 query 成功的硬依赖。

## 9. 服务与 UI 边界

UI 不直接拿 model、Chroma 或 LangSmith key。未来服务层最小 API：

- POST /v1/query/stream：question、thread_id、index_id、strategy；
- POST /v1/ingest：parse package id，返回 job_id；
- GET /v1/jobs/{job_id}：阶段、进度、错误；
- GET /v1/evidence/{id}：文本或图片证据、page/bbox；
- POST /v1/feedback：run_id、score、comment；
- GET /v1/eval-runs/{id}：本地摘要或安全代理后的 LangSmith 链接。

早期 UI 只展示上传/选择 parse package、ingest 状态、流式回答、证据图片和页码。
不在核心闭环验证前建设复杂 dashboard。

## 10. 目标模块边界

保持当前目录演进，不另起一个与 rag_pdfs 竞争的新 RAG 包：

    rag_pdfs/
      model_runtime.py        已有：catalog、role、factory、fingerprint
      prompts.py              已有：版本化 prompt
      runtime.py              已有：composition root，供三个 CLI 平级使用
      index_manifest.py       已有：embedding contract 写入与校验
      parse_source.py         规划：load StaticParsePackage
      captioner.py            规划：vision_ingest role + cache
      indexer.py              规划：index manifest + Chroma
      hybrid_retrieve.py      已有：检索算法
      query_workflow.py       下一阶段：有限状态 LangGraph
      observability.py        下一阶段：run metadata / trace helper
      query_img.py            CLI，只做参数和 workflow 调用
      eval_img.py             本地 eval + 可选 LangSmith sync

runtime.py 是 composition root；model_runtime.py 是更低层、可独立测试的模型 adapter。
这样既延续根 strata.md 的模块图，又避免把所有模型/provider 逻辑塞回 runtime.py。

## 11. 分阶段落地

### M0：模型边界与 prompt 基线（本次）

- model catalog + factory；
- API/cached/local source；
- chat role/capability；
- embedding fingerprint；
- versioned prompt；
- 不切换 canonical CLI 的模型构造，避免一次同时改 ingest/query/eval。

### M1：composition root（已完成，2026-08-29）

- 新增 runtime.py，加载 catalog；
- ingest/query/eval 改为按 role 取模型；
- index manifest 写 embedding fingerprint；
- query 打开索引时校验 fingerprint；
- 修复 query 重复检索；
- 保持三个 CLI 参数向后兼容。
- legacy 索引默认告警，部署可用严格模式拒绝。

### M2：deterministic LangGraph

- 加入显式 langgraph/langsmith 依赖；
- QueryState + 单次 retrieve + generate；
- 再加入最多一次 rewrite 分支；
- checkpointer 先 InMemory/SQLite；
- trace metadata 与节点名固定。

### M3：评估闭环

- 完成 20–30 个固定问题；
- 长上下文、text-only、image-aware 三组基线；
- A/B/C/D；
- 本地 JSONL 与 LangSmith dataset 同步；
- 只有指标暴露稳定问题后再加 reranker/LLM evidence grader。

### M4：服务/UI

- FastAPI job/query surface；
- streaming、evidence preview、feedback；
- Postgres checkpointer；
- 权限/租户边界；
- 再评估 agentic markdown navigation 和跨 thread memory。

## 12. v1 验收线

- 任一 role 的模型只在一个工厂中实例化；
- 同一 model id 在进程内复用；
- API key 不进入 catalog、trace、日志；
- cached/local 两种模式有明确错误；
- index 能拒绝 embedding fingerprint 不匹配；
- 一次普通 query 只检索一次；
- answer 使用的 evidence 与 UI/LangSmith 展示的是同一对象；
- thread 可恢复，summary 不冒充 evidence；
- 没有 LangSmith 网络时本地 query/eval 仍工作；
- 每次架构增强都能在固定问题集上比较，而不是只看 demo。

## 13. 主要官方依据

- [LangChain models 与 init_chat_model](https://docs.langchain.com/oss/python/langchain/models)
- [LangChain retrieval 与 2-step/agentic/hybrid RAG](https://docs.langchain.com/oss/python/langchain/retrieval)
- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- [LangGraph memory](https://docs.langchain.com/oss/python/langgraph/add-memory)
- [LangChain short-term memory 与 summarization](https://docs.langchain.com/oss/python/langchain/short-term-memory)
- [LangSmith tracing quickstart](https://docs.langchain.com/langsmith/observability-quickstart)
- [LangSmith evaluation](https://docs.langchain.com/langsmith/evaluation)
