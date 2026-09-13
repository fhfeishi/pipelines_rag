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

入口文件是 [`agent.py`](third_party/chat-langchain/agent.py)。核心配置如下：

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

官方文档工具不是在 `agent.py` 中实现的，而是通过 [`connectors/mcp.py`](third_party/chat-langchain/connectors/mcp.py) 声明远程 MCP Server：

```python
{
    "transport": "http",
    "url": "https://docs.langchain.com/mcp",
}
```

Managed Deep Agents 在编译 Agent 时，会把这个 MCP 服务暴露的工具自动添加到 Agent 中。

系统提示词主要位于 [`instructions.md`](third_party/chat-langchain/instructions.md)。`src/prompts/docs_agent_prompt.py` 是它的 Hub push / eval 镜像，供 [`scripts/push_docs_agent_prompt.py`](third_party/chat-langchain/scripts/push_docs_agent_prompt.py) 推送到 LangSmith Prompt Hub；它不是 `agent.py` 中显式传入的本地 prompt 参数。

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

代码位于 [`src/tools/pylon_tools.py`](third_party/chat-langchain/src/tools/pylon_tools.py)。支持的 collection 包括：

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

[`agent.py`](third_party/chat-langchain/agent.py) 中配置了以下中间件：

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

前端使用 `@langchain/langgraph-sdk` 创建 Client。代码位于 [`frontend/lib/api/langgraph-client.ts`](third_party/chat-langchain/frontend/lib/api/langgraph-client.ts)。

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

核心代码位于 [`frontend/lib/hooks/chat/use-stream-handler.ts`](third_party/chat-langchain/frontend/lib/hooks/chat/use-stream-handler.ts)。发送结构大致是：

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

[`identity.py`](third_party/chat-langchain/identity.py) 配置了：

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
