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

- [agent.py](third_party/chat-langchain/agent.py)；
- [README.md](third_party/chat-langchain/README.md)；
- [identity.py](third_party/chat-langchain/identity.py)。

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

