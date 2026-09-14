# static1 方向与进度

当前目标：面向小型本地文本、PDF 与指定网页快照的单用户 Agentic RAG。下一里程碑参照chat-langchain，在本地显式实现官方文档研究并与网页端对照。
以两份笔记和 chat-langchain 源码为参考，保留一套源码与运行入口。

已实现：
- Python LangChain/LangGraph/Deep Agents官方Markdown目录发现与批量更新，页面进度/错误展示、官方原文链接、对话与证据版本导出。
- 研究中复用搜索/阅读结果，中文技术问题引导英文概念检索，先搜索再读取的工具约束。
- launch.sh 自动复用或创建虚拟环境；可选本地 HuggingFace embedding、Chroma持久化与dense+BM25 RRF混合检索（空路径仅BM25）。
- React + TypeScript + Tailwind，FastAPI。
- 指定 v4 文本、knowledge 中 PDF，统一导入和检索。
- LiteParse 本地 PDF 解析。
- Crawl4AI/Firecrawl 网页预览确认与会话配置入口。
- Deep Agent search/read，LangGraph 有限补查与证据来源核验。
- 模型 OpenAI 兼容接口与可选 LangSmith tracing。

后续：
- 按 API_PIPELINE.md 的顶层架构、上层功能、下层API、底层选型四层设计持续迭代；保持Python/React/TypeScript/Tailwind及现有Agent与检索技术栈。
- 建立LangChain/LangGraph/Deep Agents官方文档URL清单、版本快照和对照问题集。
- 使用已有 retrieval_v4/reliability_v4 测试集评估检索和回答。
  - retrieval_v4 已完成当前库基线：8例，来源 Recall@6、正文词项覆盖率及 MRR 均为1.0；报告见 reports/retrieval_v4.json。
  - reliability_v4 的模型回答评估仍待执行，不以检索通过代替答案可靠性验证。
- 向量已接入；基于失败案例评估混合检索收益，再决定重排或语义核验。
- 持久会话与长期运行管理。
- 有实际需求后再接多 Agent、多模型和 Managed Deep Agents 部署。

历史执行记录见 dev_logs.md；启动和当前范围见 README.md。
