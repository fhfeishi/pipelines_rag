# static1 方向与进度

当前目标：面向小型本地文本、PDF 与指定网页快照的单用户 Agentic RAG。
以两份笔记和 chat-langchain 源码为参考，保留一套源码与运行入口。

已实现：
- React + TypeScript + Tailwind，FastAPI。
- 指定 v4 文本、knowledge 中 PDF，统一导入和检索。
- LiteParse 本地 PDF 解析。
- Crawl4AI/Firecrawl 网页预览确认与会话配置入口。
- Deep Agent search/read，LangGraph 有限补查与证据来源核验。
- 模型 OpenAI 兼容接口与可选 LangSmith tracing。

后续：
- 使用已有 retrieval_v4/reliability_v4 测试集评估检索和回答。
- 基于失败案例决定是否加入向量、重排或语义核验。
- 持久会话与长期运行管理。
- 有实际需求后再接多 Agent、多模型和 Managed Deep Agents 部署。

历史执行记录见 dev_logs.md；启动和当前范围见 README.md。

