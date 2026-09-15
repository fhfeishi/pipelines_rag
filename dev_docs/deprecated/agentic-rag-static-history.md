# Agentic RAG 原始笔记归档

> 文档归属：static1。2026-09-16 从 `agentic-rag-static-history.md` 迁入。历史归档，仅作溯源，不作为现行设计依据。正文中的源码路径以仓库根目录或明确标注的 static1 目录为基准，不以本文目录为基准。


归档：2026-09-16。以下两份原文保留历史分析，不继续同步当前实现。后续设计统一见 [合并主文档](../static1_plan/roadmap.md)。

## 原文一：agentic-rag-static.md

# Chat LangChain 的 Agentic RAG Pipeline 源码分析

本文整理对 `third_party/chat-langchain` 的两轮源码分析，重点说明：

- Agent 如何构建；
- 文档知识来自哪里；
- 文档如何加载和进入上下文；
- 文档信息存储在哪里；
- 前后端如何通信；
- 是否支持本地文档；
- 为什么它看起来比传统 RAG 更“聪明”；
- 如何将这种模式复制到中小型知识库项目。

## 一、总体结论

`chat-langchain` 不是传统的本地文档 RAG 系统。它没有在当前仓库中实现以下流程：

```text
本地文档 → 文档解析 → 文本切分 → Embedding → 向量数据库 → Top-K 检索
```

它更像一个托管式 Agentic QA 系统：

```text
用户问题
  ↓
Managed Deep Agents HTTP / Identity
  ↓
LangGraph docs_agent
  ↓
Guardrails
  ↓
LLM 决定调用哪些工具
  ├─ 官方文档 MCP 搜索 / 阅读
  ├─ Pylon Support KB 搜索 / 阅读
  ├─ LangChain Pricing
  └─ 链接检查
  ↓
LLM 综合答案
  ↓
LangGraph 流式返回前端
```

当前仓库主要负责 Agent 配置、工具定义、提示词、中间件和 Next.js 前端；真正的官方文档索引、Pylon 知识库存储以及 Managed Deep Agents 的运行时由外部服务提供。

## 二、Agent 是如何构建的

入口文件是 [`agent.py`](../../third_party/chat-langchain/agent.py)。核心配置如下：

```python
agent = define_deep_agent(
    name="docs_agent",
    model="google_genai:gemini-3.5-flash-lite",
    tools=docs_agent_tools,
    middleware=docs_agent_middleware,
    disable_memory=True,
)
```

自定义工具包括：

- `search_support_articles`
- `get_support_article_content`
- `fetch_langchain_pricing`
- `check_links`

官方文档工具不是在 `agent.py` 中实现的，而是通过 [`connectors/mcp.py`](../../third_party/chat-langchain/connectors/mcp.py) 声明远程 MCP Server：

```python
{
    "transport": "http",
    "url": "https://docs.langchain.com/mcp",
}
```

Managed Deep Agents 在编译 Agent 时，会把这个 MCP 服务暴露的工具自动添加到 Agent 中。

系统提示词主要位于 [`instructions.md`](../../third_party/chat-langchain/instructions.md)。`src/prompts/docs_agent_prompt.py` 是它的 Hub push / eval 镜像，供 [`scripts/push_docs_agent_prompt.py`](../../third_party/chat-langchain/scripts/push_docs_agent_prompt.py) 推送到 LangSmith Prompt Hub；它不是 `agent.py` 中显式传入的本地 prompt 参数。

## 三、文档知识包括哪些

### 1. 官方 LangChain 文档

系统提示词要求 Agent 处理：

- LangChain；
- LangGraph；
- LangSmith；
- Fleet；
- DeepAgents。

这些内容由 `https://docs.langchain.com/mcp` 提供。当前仓库只声明了 MCP 接入地址，没有实现官方文档的抓取、清洗、切分或索引。

官方文档工具主要是：

- `search_docs_by_lang_chain`：搜索文档标题、路径、URL 和摘要；
- `query_docs_filesystem_docs_by_lang_chain`：根据搜索结果读取完整 `.mdx` 页面。

提示词要求不能只根据搜索摘要回答，必须先搜索，再读取相关页面正文。

### 2. Pylon Support Knowledge Base

Support 知识库来自独立的 Pylon SaaS/API，而不是 LangChain 官方文档 MCP。

API 地址：

```text
https://api.usepylon.com
```

配置项：

- `PYLON_API_KEY`
- `PYLON_KB_ID`

代码位于 [`src/tools/pylon_tools.py`](../../third_party/chat-langchain/src/tools/pylon_tools.py)。支持的 collection 包括：

- General；
- OSS (LangChain and LangGraph)；
- LangSmith Observability；
- LangSmith Evaluation；
- LangSmith Deployment；
- SDKs and APIs；
- LangSmith Studio；
- Self Hosted；
- Troubleshooting；
- Security。

需要注意：当前 Pylon 集成没有实现向量检索。它会先拉取全部文章，再由代码过滤公开和已发布内容，并把文章标题和 ID 交给 Agent 选择。

### 3. Pricing 页面

价格问题通过独立的 `fetch_langchain_pricing` 工具实时读取 pricing 页面，不属于文档知识库检索。

### 4. 用户上传的文件

前端允许用户上传图片、代码、Markdown、文本、日志和配置文件等。但这些文件不是被导入知识库，而是作为当前消息的附件发送给 Agent。

当前没有看到 PDF、DOCX、XLSX 的正式文档入库流程。

## 四、官方文档是如何加载的

官方文档加载流程是：

```text
用户问题
  ↓
search_docs_by_lang_chain(query)
  ↓
返回标题、路径、URL、摘要
  ↓
query_docs_filesystem_docs_by_lang_chain(command=...)
  ↓
读取远程文档文件系统中的 .mdx 内容
  ↓
工具结果进入当前 LangGraph 消息上下文
  ↓
LLM 综合回答
```

这里的 `filesystem docs` 不是本机文件系统，而是远程 MCP 服务背后的文档文件系统接口。文档具体如何抓取、解析、切分、索引，当前仓库没有实现，也无法从源码准确推断。

## 五、Pylon 文档是如何加载的

### 第一次加载

`_fetch_all_articles()` 第一次调用时会：

1. 请求 `/knowledge-bases/{kb_id}/articles`；
2. 按 cursor 分页；
3. 最多读取 10 页；
4. 将文章对象保存到进程内的 `_articles_cache`。

collection 信息也会保存到 `_collections_cache`。

### 搜索阶段

`search_support_articles()` 会：

1. 获取全部文章；
2. 过滤 `is_published=true`；
3. 过滤 public visibility；
4. 排除无标题、无 identifier 或无 slug 的文章；
5. 按 collection 过滤；
6. 返回文章元信息。

返回结果类似：

```json
{
  "collections": "all",
  "total": 10,
  "articles": [
    {
      "id": "article-id",
      "title": "Article title",
      "url": "https://support.langchain.com/articles/...",
      "collection": "Troubleshooting"
    }
  ]
}
```

### 阅读阶段

Agent 从搜索结果中选出文章 ID，再调用：

```text
get_support_article_content(article_id)
```

该工具从缓存中找到文章，返回 ID、标题、URL、collection 和正文。正文来自 Pylon 的 `current_published_content_html`，并截取最多 5000 个字符。

因此 Pylon 这一侧的检索更接近：

```text
全量拉取 → 本地过滤 → LLM 选择文章 → 按 ID 读取正文
```

它不是源码中常见的 ANN、BM25 或 hybrid retrieval 实现。

## 六、文档信息存储在哪里

可以分为三层。

### 1. 远程知识源

官方文档存储在 LangChain 的远程文档服务中；Support 文章存储在 Pylon Knowledge Base 中。这些内容不在当前 Git 仓库内。

### 2. 后端进程内缓存

Pylon 工具使用两个 Python 全局变量：

```python
_articles_cache
_collections_cache
```

它们只存在于当前 Agent 进程内，进程重启后丢失。当前源码没有 Redis、数据库或本地文件缓存。

### 3. Agent 会话状态

搜索结果、文档正文和工具调用结果会进入当前 LangGraph 消息状态，供 Agent 继续推理。线程状态由 Managed Deep Agents Runtime 的 checkpointer 管理。

但 `disable_memory=True` 表示关闭跨线程长期语义记忆。它保存的是会话线程状态，不是一个可独立复用的知识库。

## 七、完整问答 Pipeline

系统提示词设计的是 docs-first research workflow：

```text
1. 判断问题是否属于 LangChain 生态
2. 执行 Guardrails 分类
3. 搜索官方文档
4. 搜索 Support KB
5. 读取官方文档正文
6. 读取 Support 文章正文
7. 必要时读取 Pricing
8. 检查准备输出的 URL
9. LLM 综合最终回答
```

提示词要求技术问题的官方文档搜索和 Support 搜索并行，之后的正文读取也并行。它还要求：

- 不能只根据搜索结果回答；
- Support 文章只能使用搜索结果返回的 ID；
- 最终输出的链接必须经过 `check_links` 验证。

### Middleware

[`agent.py`](../../third_party/chat-langchain/agent.py) 中配置了以下中间件：

- `IngressGuardsMiddleware`：最新用户消息最多 50,000 字符；
- `GuardrailsMiddleware`：判断主题是否相关，并阻止 NSFW、虚构、违法、有害用途和提示词提取等请求；
- `CustomSummarizationMiddleware`：上下文超过 130,000 tokens 时摘要旧消息，保留最近 30,000 tokens；
- `ToolRetryMiddleware`：工具失败时重试；
- `ModelRetryMiddleware`：模型失败时重试；
- `ModelFallbackMiddleware`：主模型不可用时切换备用模型。

默认模型是：

```text
google_genai:gemini-3.5-flash-lite
```

备用模型包括：

```text
openai:gpt-5.4-nano
anthropic:claude-haiku-4-5-20251001
```

## 八、前后端如何通信

### 1. 前端创建 LangGraph Client

前端使用 `@langchain/langgraph-sdk` 创建 Client。代码位于 [`frontend/lib/api/langgraph-client.ts`](../../third_party/chat-langchain/frontend/lib/api/langgraph-client.ts)。

请求会携带：

```http
Authorization: Bearer <Supabase access token 或 guest token>
X-Supabase-Region: <region>
```

API 地址来自：

- `NEXT_PUBLIC_LANGGRAPH_API_URL`；
- `NEXT_PUBLIC_LANGGRAPH_API_URL_EXTERNAL`；
- 本地开发时默认 `http://127.0.0.1:2024`。

### 2. 发送问答请求

核心代码位于 [`frontend/lib/hooks/chat/use-stream-handler.ts`](../../third_party/chat-langchain/frontend/lib/hooks/chat/use-stream-handler.ts)。发送结构大致是：

```typescript
{
  messages: [
    {
      role: "user",
      content: userContent
    }
  ]
}
```

调用：

```typescript
client.runs.stream(threadId, "docs_agent", {
  input,
  config: {
    recursion_limit: 100,
    tags: ["Chat-LangChain", "docs_agent"],
    metadata: traceMetadata
  },
  streamMode: ["values", "updates", "messages"],
  streamSubgraphs: true,
  ifNotExists: "create"
})
```

`threadId` 标识会话，`docs_agent` 标识后端 Agent，`ifNotExists: "create"` 允许首次发送消息时创建线程。

### 3. 前端处理流式结果

前端通过异步迭代读取 stream：

```typescript
for await (const chunk of streamResponse) {
  // 更新消息、工具调用和思考步骤
}
```

主要处理：

- `values`：完整状态；
- `updates`：节点和工具执行状态；
- `messages` / `messages/partial`：增量 Token。

前端会把工具调用转成可视化状态，例如：

- Searching documentation；
- Reading documentation；
- Searching support articles；
- Reading support articles；
- Checking documentation links。

### 4. 会话历史

切换线程时调用：

```typescript
client.threads.getState(threadId)
```

然后读取：

```typescript
state.values.messages
```

线程列表通过 `client.threads.search()` 查询，并使用 `metadata.user_id` 过滤。

### 5. 停止生成

用户点击 Stop 时调用：

```typescript
client.runs.cancel(threadId, runId)
```

### 6. 身份与线程隔离

[`identity.py`](../../third_party/chat-langchain/identity.py) 配置了：

- 多区域 Supabase access token 验证；
- 24 小时 guest token；
- 线程按照 actor 隔离；
- 关闭长期 memory；
- Agent 侧管理 credentials。

聊天本身没有自定义 FastAPI/Flask 路由，主要 HTTP 能力由 Managed Deep Agents 托管。前端唯一明显的 API Route 是 guest token 代理，位于 `frontend/app/api/auth/guest/route.ts`。

## 九、文件上传是不是文档 QA

不是严格意义上的文档入库。

### 图片

图片会转成：

```text
data:image/png;base64,...
```

然后以 `image_url` 内容块发送给模型。

### 文本和代码

文本文件在浏览器中读取并解码，然后被拼成一段 Markdown 文本：

```text
**File: example.py**
```
```text
文件内容
```
```

这仍然只是当前消息上下文，不是持久化知识库。

### 当前不具备的能力

- 没有通用 PDF/DOCX/XLSX loader；
- 没有本地文档目录扫描；
- 没有文档切分器；
- 没有 embedding pipeline；
- 没有向量数据库；
- 没有本地文档索引；
- 没有上传文档后的跨会话复用。

## 十、为什么它看起来比传统 RAG 更聪明

它的优势不一定来自某个更高级的检索算法，而是来自 Agentic research workflow：

```text
问题
  → 判断是否需要查资料
  → 选择搜索工具
  → 设计搜索关键词
  → 并行搜索多个知识源
  → 从搜索结果中挑选页面
  → 读取完整正文
  → 信息不足时继续调用工具
  → 验证链接
  → 综合答案
```

传统 RAG 常见模式是：

```text
query
  → embedding
  → top-k chunks
  → 拼入 prompt
  → 生成答案
```

Chat LangChain 更接近：

```text
query
  → Agent 规划
  → 调用搜索工具
  → 调用阅读工具
  → 根据结果继续决策
  → 生成答案
```

它因此减少了几个传统 RAG 的问题：

- 不只依赖一次相似度搜索；
- 不把大量无关 chunks 生硬拼在一起；
- 先定位页面，再读取完整页面；
- 可以动态组合多个知识源；
- 可以根据结果决定是否继续查；
- 可以区分官方文档和客服问题；
- 可以在输出前检查引用链接。

不过，它背后仍然依赖远端官方文档服务的搜索索引。这个索引的建设和检索算法被封装在 MCP Server 后面，当前源码看不到。

## 十一、是否支持本地文档或其他文档

### 按当前源码：不支持正式的本地知识库

当前原生接入的知识源只有：

1. LangChain 官方文档 MCP；
2. Pylon Support KB；
3. Pricing 页面。

本地文件只能作为即时消息附件分析，不能作为持久化知识库使用。

### 通过改造：可以支持

Agent 并不强依赖某一种检索实现。只要给它提供类似的工具接口，就可以接入其他文档：

```text
search_local_docs(query, filters)
read_local_doc(doc_id, section_id)
list_local_collections()
```

工具背后可以使用：

- SQLite FTS5、Elasticsearch 或 OpenSearch 做 BM25/全文检索；
- FAISS、Qdrant、pgvector 等做向量检索；
- hybrid retrieval；
- Markdown、HTML、JSON 或数据库保存原文；
- PDF、DOCX、XLSX 解析器；
- 标题、章节、来源、更新时间等元数据。

也可以把这些工具封装为本地 MCP Server，Agent 仍然使用“搜索工具 + 阅读工具”的模式。

## 十二、哪些部分容易复制，哪些部分不容易复制

### 容易复制的部分

- Agent 入口和工具编排；
- “搜索 → 阅读 → 总结”的提示词；
- 多知识源并行调用；
- 工具调用结果进入消息上下文；
- LangGraph 的线程和流式执行；
- Guardrails；
- retry/fallback；
- 长上下文摘要；
- 前端对工具过程的可视化。

### 不容易直接复制的部分

- LangChain 官方文档的抓取、清洗、切分和索引；
- `docs.langchain.com/mcp` 背后的搜索实现；
- Pylon 知识库存储和后台管理；
- Managed Deep Agents 的 HTTP、checkpointer、身份和部署运行时；
- LangSmith 的托管连接器。

因此，这个仓库更像是“Agent 应用层”，不是完整的知识库基础设施。

## 十三、适合中小型知识库项目的本地化方案

可以保留它的 Agent 思路，只替换远程知识源：

```text
本地文档
  → 解析并结构化
  → 保存原文和元数据
  → 建立全文索引 + 向量索引
  → 暴露 search_docs / read_doc 工具
  → Agent 自主调用
```

推荐的工具边界是：

```text
search_docs(query, filters)
read_doc(doc_id, section_id)
```

而不是只暴露一个“返回 top-k 文本块”的函数。

这样复制的是 Chat LangChain 真正有价值的部分：

> 让 Agent 决定查什么、读什么、是否继续查，而不是固定地把 top-k chunks 塞给模型。

同时需要明确：它的“聪明”来自两部分：

1. Agent 的工具调用与研究流程设计；
2. LangChain 云端文档系统已经建设好的文档清洗、组织和索引能力。

第一部分可以复用，第二部分需要在本地自行建设。

---

## 原文二：agentic-rag-static-2.md

# LangChain、LangGraph、Deep Agents 与 Agentic RAG Pipeline

本文整理 LangChain / LangGraph / Deep Agents 的分层、知识接入、单 Agent 与多 Agent Pipeline，以及多模型协调方案。

## 一、总体判断

三者不是简单的“低级 API → 高级 API”，而是位于 Agent 系统的不同层次：

| 层次 | 主要职责 | 典型对象 |
|---|---|---|
| LangChain | 模型、工具、消息、Agent loop、Middleware 等组件 | create_agent、@tool |
| LangGraph | 状态、节点、边、循环、并行、持久化、流式执行 | StateGraph、Send |
| Deep Agents | 面向复杂任务的 Agent harness，提供规划、文件系统、子 Agent、上下文管理 | create_deep_agent |
| Managed Deep Agents | 把 Agent 的 HTTP、身份、部署、Connector、Checkpointer 等托管起来 | define_deep_agent |

官方产品分层：[Frameworks, runtimes, and harnesses](https://docs.langchain.com/oss/python/concepts/products)、[LangGraph Overview](https://docs.langchain.com/oss/python/langgraph/overview)。

~~~text
LangChain
  提供模型、工具、消息和 Agent 组件
        ↓
LangGraph
  提供状态化工作流和运行时
        ↓
Deep Agents
  在 LangGraph 上提供规划、上下文管理、文件系统和子 Agent
        ↓
Managed Deep Agents
  将 Agent 作为受管理的服务部署
~~~

## 二、职责边界

### LangChain：Agent 组件和标准循环

LangChain 当前推荐的标准 Agent API 是：

~~~python
from langchain.agents import create_agent

agent = create_agent(
    model=model,
    tools=[search, fetch],
    system_prompt="...",
)
~~~

它提供：

~~~text
LLM
+ Tools
+ Message State
+ Model → Tool → Model 循环
+ Middleware
~~~

create_agent 底层也是基于 LangGraph 构建的，只是把常见的 LLM 工具调用循环封装起来了。[LangChain Agents](https://docs.langchain.com/oss/python/langchain/agents)

因此，LangChain 不只是“底层 LLM 工作流”，更准确地说是：

> 面向 Agent 的基础框架和组件层。

### LangGraph：显式工作流和运行时

LangGraph 负责显式定义：

~~~text
节点
边
条件路由
循环
状态
并行
持久化
中断恢复
流式输出
~~~

例如：

~~~text
START
  → query_router
  → retrieve
  → judge
  → answer
  → END
~~~

LangGraph 的优势是流程和状态可控，适合有明确业务约束的系统。[Workflows and Agents](https://docs.langchain.com/oss/python/langgraph/workflows-agents)

### Deep Agents：复杂 Agent 的 harness

Deep Agents 建立在 LangChain 和 LangGraph 之上，预装了一套复杂 Agent 常用能力：

- 任务规划；
- write_todos 任务列表；
- 文件系统工具；
- 上下文卸载；
- 自动摘要；
- 子 Agent；
- Skills；
- 长期存储；
- 权限控制；
- Human-in-the-loop。

官方将 Deep Agents 称为 Agent harness，而不是新的检索框架。它的核心仍然是 LLM 调用工具，只是把复杂 Agent 常用的脚手架预装好了。[Deep Agents Overview](https://docs.langchain.com/oss/python/deepagents/overview)

~~~text
LangChain create_agent
  = 一个标准 Agent loop

Deep Agents
  = 一个带规划、上下文管理、文件系统和子 Agent 的 Agent loop
~~~

## 三、Deep Agents 的自主性和可拓展性

### 自主性的来源

Deep Agents 的自主性主要体现在：

1. 自主判断下一步做什么；
2. 自主选择工具；
3. 自主拆分子任务；
4. 自主调用子 Agent；
5. 自主保存中间结果；
6. 信息不足时自主继续调查；
7. 根据上下文动态调整计划。

但它不是无限自主。真实边界由以下因素决定：

~~~text
system_prompt
+ tools
+ model
+ middleware
+ max recursion
+ permissions
+ checkpointer
+ backend
+ human approval
~~~

因此：

> Deep Agents 提供自主行为的机制，但开发者仍然要定义自主行为的边界。

write_todos 只是一个模型可以调用的规划工具，并不等于保证最优的形式化规划器。计划质量仍然取决于模型、工具描述、上下文和约束。

### 可拓展性

create_deep_agent 可以配置：

~~~python
create_deep_agent(
    model=...,
    tools=[...],
    system_prompt=...,
    middleware=[...],
    subagents=[...],
    backend=...,
    skills=[...],
    memory=[...],
    permissions=[...],
    response_format=...,
)
~~~

可扩展点包括：

- 普通 LangChain Tools；
- 自定义 Middleware；
- 自定义 Subagents；
- 已编译的 LangGraph graph；
- 自定义文件系统 Backend；
- 自定义权限规则；
- 不同模型；
- MCP Server；
- 结构化输出。

Deep Agents 的子 Agent 可以是已经编译好的 LangGraph 子图：

~~~text
Deep Agent Supervisor
  ├─ 普通 SubAgent
  ├─ 普通 SubAgent
  └─ Compiled LangGraph Subgraph
~~~

因此：

> LangGraph 仍然是底层编排能力，Deep Agents 是一种更强的 Agent 节点实现。

参考：[Customize Deep Agents](https://docs.langchain.com/oss/python/deepagents/customization)、[Subagents](https://docs.langchain.com/oss/python/deepagents/subagents)。

## 四、managed_deepagents 与 deepagents

当前 chat-langchain 使用：

~~~python
from managed_deepagents import define_deep_agent
~~~

开源 Deep Agents SDK 常见的是：

~~~python
from deepagents import create_deep_agent
~~~

两者不应完全等同。

deepagents 主要提供：

- Agent harness；
- 规划；
- 文件系统；
- 子 Agent；
- Backend；
- Skills；
- Memory；
- Middleware。

managed_deepagents 更偏向托管运行时封装，负责：

- Agent 部署；
- HTTP ingress；
- 身份验证；
- Managed Connector；
- Checkpointer；
- LangSmith 集成；
- 线程和权限范围。

相关源码：

- [agent.py](../../third_party/chat-langchain/agent.py)；
- [README.md](../../third_party/chat-langchain/README.md)；
- [identity.py](../../third_party/chat-langchain/identity.py)。

因此：

~~~text
deepagents
  = Agent 能力和 harness

managed_deepagents
  = 把 Agent 部署成受管理的服务
~~~

后者不代表推理能力一定更强，而是让部署、身份、运行和连接器更方便。

## 五、本地文档和网页如何接入 Deep Agents

最推荐的方式不是让 Deep Agent 随意读取整个文件系统，而是把本地文档和网页封装成工具。

### 推荐的工具边界

~~~text
search_local_docs(query, filters)
read_local_doc(doc_id, section_id)

search_web(query)
fetch_web_page(url)
~~~

Agent 负责决定：

~~~text
查本地文档？
查网页？
查哪个主题？
还要不要继续查？
哪些证据足够？
~~~

工具负责确定性地完成：

~~~text
查询索引
读取正文
返回来源
返回段落
返回更新时间
~~~

返回结果最好是结构化证据：

~~~json
{
  "source_id": "doc-001",
  "source_type": "local",
  "title": "部署规范",
  "locator": "chapter-3",
  "content": "...",
  "url": null,
  "updated_at": "2026-09-01"
}
~~~

网页也应统一成类似结构，并携带 url、retrieved_at、source_id 等字段。

### 作为 LangChain Tools

~~~python
deep_agent = create_deep_agent(
    model=model,
    tools=[
        search_local_docs,
        read_local_doc,
        search_web,
        fetch_web_page,
    ],
)
~~~

### 封装为 MCP Server

~~~text
local-docs-mcp
  ├─ search_local_docs
  ├─ read_local_doc
  └─ list_collections
~~~

MCP 统一外部工具和数据源的接口，并不是知识库本身。LangChain 可以把 MCP Server 上的工具加载成普通 LangChain Tools。[LangChain MCP](https://docs.langchain.com/oss/python/langchain/mcp)

~~~text
Agent
  → Local Docs MCP
  → 本地 SQLite / PostgreSQL / 文件系统 / 向量库
~~~

### 使用文件系统 Backend

Deep Agents 可以通过 Backend 暴露：

~~~text
ls
read_file
grep
glob
write_file
edit_file
~~~

常见 Backend 包括：

- StateBackend：当前线程内的临时状态；
- FilesystemBackend：本地磁盘；
- StoreBackend：跨线程持久化；
- CompositeBackend：组合多个存储；
- 自定义 Backend。

但是文件系统工具不等于高质量知识库检索。对于几十个纯文本文件，可以使用 grep、glob 和 read_file；对于正式问答系统，建议仍然提供专门的 search_local_docs 工具。

官方提醒，直接把真实本地文件系统挂到 Web API 上存在严重安全风险。生产环境应优先使用自定义 Backend、StoreBackend 或隔离 Sandbox。[Deep Agents Backends](https://docs.langchain.com/oss/python/deepagents/backends)

## 六、static-agentc-rag-sys 的单 Agent Pipeline

假设：

- 文档和网页已经是纯文本；
- 知识库规模中小；
- 文档相对稳定；
- 暂不考虑复杂解析；
- 希望使用 LangChain、LangGraph、Deep Agents。

### 离线阶段

离线建库不应作为在线 Agent 的任务：

~~~text
本地文档 / 网页快照
  → 规范化
  → 保存原文和元数据
  → 建全文索引
  → 建向量索引
~~~

这一阶段使用普通 LangChain 文档组件和数据库即可，不需要 Deep Agent 自主决策。

### 在线问答阶段

~~~text
START
  → ingress
  → query_router
  → research_deep_agent
  → evidence_validator
  → answer_writer
  → END
~~~

证据不足时允许有限回路：

~~~text
evidence_validator
  ├─ 足够 → answer_writer
  └─ 不足 → research_deep_agent，最多补查 1～2 次
~~~

### 节点划分

| 节点 | 类型 | 作用 |
|---|---|---|
| ingress | 普通函数 | 输入清洗、鉴权、限流、长度控制 |
| query_router | 结构化 LLM 或普通函数 | 判断查本地文档、网页或两者 |
| research_deep_agent | Deep Agent | 规划、搜索、阅读、补查 |
| evidence_validator | 普通节点或小模型 Agent | 检查证据是否支持结论 |
| answer_writer | LLM 节点 | 基于证据生成答案和引用 |

推荐规模：

~~~text
1 个 Deep Agent
+ 3～4 个 LangGraph 普通节点
+ 4～6 个数据工具
~~~

不要一开始就拆成多个研究 Agent。

### Deep Agent 内部职责

~~~text
write_todos
  → search_local_docs
  → read_local_doc
  → search_web
  → fetch_web_page
  → 对比证据
  → 补充搜索
  → 输出 EvidencePacket
~~~

输出最好是结构化对象：

~~~python
class EvidencePacket(BaseModel):
    claims: list[str]
    sources: list[SourceEvidence]
    missing_information: list[str]
    confidence: float
~~~

不要让它只返回一大段自然语言报告。

## 七、多 Agent、多 LLM Agentic RAG

当本地文档和网页搜索方式明显不同，或者知识库有多个专业领域时，再考虑多 Agent。

### 推荐结构

~~~text
START
  ↓
query_planner
  ↓
fan-out
  ├─ local_researcher
  ├─ web_researcher
  ├─ domain_researcher
  └─ policy_researcher
  ↓
evidence_adjudicator
  ↓
answer_writer
  ↓
citation_checker
  ↓
END
~~~

实际运行时不需要每次启动所有 Agent：

~~~text
只问内部规范
  → local_researcher

问最新产品信息
  → web_researcher

内部规范 + 外部标准
  → 两者并行

复杂跨领域问题
  → 动态增加 domain_researcher
~~~

建议规模：

~~~text
1 个 Supervisor
+ 2～3 个 Researcher
+ 1 个 Evidence Judge
+ 1 个 Finalizer
~~~

### Deep Agents 原生 Subagents

~~~python
subagents = [
    {
        "name": "local_researcher",
        "description": "Search and read the local document corpus",
        "system_prompt": "...",
        "tools": [search_local_docs, read_local_doc],
        "model": local_model,
    },
    {
        "name": "web_researcher",
        "description": "Search and verify current web sources",
        "system_prompt": "...",
        "tools": [search_web, fetch_web_page],
        "model": web_model,
    },
]
~~~

主 Agent 通过内置 task 工具调用它们。重点不是“多几个聊天机器人”，而是隔离复杂的中间搜索过程，只把压缩后的结果返回给主 Agent。[Deep Agents Subagents](https://docs.langchain.com/oss/python/deepagents/subagents)

### 更推荐：LangGraph 作为外层协调器

~~~text
LangGraph 外层 Orchestrator
  ├─ 调度多个 Deep Agent 子图
  ├─ 控制并行
  ├─ 合并状态
  ├─ 控制重试和预算
  └─ 调用最终验证节点
~~~

使用 LangGraph 的 Send API 可以实现动态 fan-out：

~~~text
Planner 生成 research_tasks
  ↓
Send(local_researcher, task_1)
Send(web_researcher, task_2)
Send(domain_researcher, task_3)
  ↓
所有结果写入 shared state
  ↓
evidence_adjudicator
~~~

这种 Orchestrator-Worker 模式适合研究任务数量动态变化的情况。[LangGraph Workflows and Agents](https://docs.langchain.com/oss/python/langgraph/workflows-agents)

## 八、多 Agent 的协调问题

多 Agent 的难点通常不是启动多个 Agent，而是：

~~~text
Agent 之间如何交换信息？
如何避免重复搜索？
如何处理相互矛盾的结论？
如何控制成本？
如何确保最终答案有依据？
~~~

### 1. 传结构化证据，不传自由文本报告

每个研究 Agent 应返回：

~~~json
{
  "task_id": "local-search-001",
  "claims": [
    {
      "claim": "...",
      "support": "原文证据",
      "source_id": "doc-001",
      "locator": "chapter-2",
      "confidence": 0.92
    }
  ],
  "gaps": [],
  "retrieved_at": "2026-09-13"
}
~~~

主 Agent 不应直接接收几十页原文，而应接收：

~~~text
证据片段
来源 ID
定位信息
支持的 claim
置信度
缺口
~~~

### 2. LangGraph 控制流程，Agent 控制局部决策

~~~text
LangGraph 控制：
- 谁什么时候执行；
- 是否并行；
- 最多循环几次；
- 失败如何重试；
- 是否需要人工确认；
- 是否允许最终输出。

Deep Agent 控制：
- 搜索哪些关键词；
- 读哪些文档；
- 是否补查；
- 如何组织局部研究任务。
~~~

### 3. 显式处理冲突

evidence_adjudicator 不应简单多数投票，而应判断：

1. 是否来自一手来源；
2. 是否足够新；
3. 是否明确支持当前 claim；
4. 是否有多个独立来源互相印证；
5. 是否存在版本、时间或适用范围差异；
6. 是否应该同时呈现冲突观点。

### 4. 控制多 Agent 预算

必须显式设置：

~~~text
最大 Agent 数量
最大工具调用次数
最大递归深度
最大网页数量
最大返回字符数
最大 token
最大执行时间
最大补查轮次
~~~

否则“自主研究”很容易变成：

~~~text
重复搜索
重复读取
上下文膨胀
成本失控
延迟不可预测
~~~

### 5. 不要滥用 Handoff

一次性的本地文档和网页研究，推荐：

~~~text
Subagent / Orchestrator-Worker
~~~

而不是：

~~~text
Agent A 把用户交给 Agent B
Agent B 再把用户交给 Agent C
~~~

Handoff 更适合客服、销售、审批等需要在多个对话状态之间转移用户的系统；一次性 RAG 研究更适合并行子任务和最终汇总。[LangChain Handoffs](https://docs.langchain.com/oss/python/langchain/multi-agent/handoffs)

## 九、多模型如何分配

| 角色 | 模型要求 | 主要任务 |
|---|---|---|
| Planner / Supervisor | 较强，结构化输出稳定 | 任务拆解、路由 |
| Local Researcher | 中等、便宜、工具调用稳定 | 本地文档检索 |
| Web Researcher | 中等或较强 | 网页搜索、页面读取 |
| Evidence Judge | 较强、低温度 | 证据核验、冲突处理 |
| Finalizer | 较强 | 最终回答、引用编排 |

Deep Agents 支持任意具备 tool calling 能力的 LangChain Chat Model，因此多模型分配是可行的。[Deep Agents Models](https://docs.langchain.com/oss/python/deepagents/models)

但不建议为了“多模型”而多模型。只有在以下情况才值得拆分：

- 不同任务需要不同模型能力；
- 本地检索任务量大，需要便宜模型；
- 最终核验对准确率要求高；
- 网页研究和内部文档研究的工具差异明显；
- 需要降低单个 Agent 的上下文复杂度。

## 十、推荐实现路线

### 第一阶段：单 Deep Agent + LangGraph 外壳

~~~text
LangGraph
  → query_router
  → Deep Research Agent
  → evidence_validator
  → answer_writer
~~~

### 第二阶段：并行数据源研究

~~~text
LangGraph Supervisor
  ├─ local_researcher
  └─ web_researcher
       ↓
   evidence_judge
       ↓
   answer_writer
~~~

### 第三阶段：领域 Agent 和多模型

~~~text
Supervisor
  ├─ local researcher
  ├─ web researcher
  ├─ domain researcher
  ├─ policy researcher
  ├─ evidence judge
  └─ finalizer
~~~

## 十一、最终架构原则

不要把 Deep Agents 当成“自动知识库系统”，而应该把它当成：

> 能够自主规划和调用知识工具的 Agent harness。

本地文档、网页、数据库、向量索引、BM25、MCP 都属于它可以调用的数据工具。

合理的整体架构是：

~~~text
LangChain
  提供组件和工具

LangGraph
  控制系统流程、状态、并行和可靠性

Deep Agents
  负责复杂的局部研究和任务分解

MCP
  负责跨进程、跨服务的数据工具接入

LangSmith
  负责追踪、评估和调试
~~~

最终核心原则：

~~~text
Deep Agent 决定查什么、读什么、是否继续查；
LangGraph 决定什么时候停、如何协调、如何保证边界；
检索工具负责准确、可追踪地返回证据。
~~~
