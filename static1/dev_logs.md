# static1 开发执行记录

## 2026-09-13 · 统一源码与知识接入

按用户要求，用当前需求重写后端，不再维护旧固定页面检索链路。React 应用从 web 迁入 frontend；移除旧 app.js、styles.css、index.html 和 agent/docs.py。历史记录只用于解释演进，不是另一套可运行版本。

新增 knowledge.py（SQLite、Document/Page、成熟 BM25 库检索与按页/行阅读）、parsers.py（UTF-8、LiteParse 2.4.0、Crawl4AI 0.9.3、Firecrawl v2）、agent/graph.py（Deep Agents + LangGraph）、agent/models.py（统一模型与 tracing）。网页先预览后确认，同域名配置用户会话；默认不上传 PDF 到云解析服务。

实测：8份 v4 文本和3份 PDF 均导入成功。PDF 分别为13、9、17页；Crawl4AI 抓取 example.com 成功；Cookie 保护的本地测试页已验证 storage_state 复用。Firecrawl 仅做 HTTP 契约与错误测试，未使用真实云端密钥调用。

真实 DeepSeek 模型通过 Deep Agent 的 search_docs/read_doc 得到“南溪地基基础施工计划完成于2025年10月22日”，并返回实际读取的南溪文本来源。该测试不等同于完整数据集上的问答准确率评估。

后端15项测试通过；前端2项流协议测试、TypeScript与生产构建通过；Playwright桌面联调、移动端溢出检查通过，已查看页面截图。修正了PDF纯页码尾段错误排前与长单行文本不可读取的问题。

仍未验证：Firecrawl云端调用、真实网站登录及验证码、LlamaCloud云解析、本地模型、LangSmith云端trace落库。Managed Deep Agents部署、多Agent及持久线程不在本步骤实现范围。

## 2026-09-13 · Agentic RAG 分步升级：第一步

依据两份 agentic-rag-static 笔记和 chat-langchain 的工具流设计，新增 web/ 下的 React + TypeScript + Tailwind 应用，接入既有 FastAPI /api/chat，支持 Markdown、来源、过程状态、停止与错误展示。未完成的回答不进入下一轮上下文。新增 IMPLEMENTATION.md 记录目标结构、接口与后续步骤；当前后端仍为原固定检索链路。

验证：现有后端 9 项测试通过；前端流协议 2 项测试通过，覆盖逐字节中文分包、无完成事件的截断与后端错误。没有调用真实模型；浏览器视觉检查尚未执行。

前端依赖安装完成，已生成 package-lock.json；TypeScript 检查和 Vite 生产构建通过。

## 2026-09-12 · 需求与源码核对

【任务】依据 Strata 的 NOW 和停止条件完善个人文档助手。

【实现情况】读取本地 third_party/chat-langchain 的 instructions.md、connectors/mcp.py、ingress_guards_middleware.py 和 use-stream-handler.ts。上游通过托管 MCP 搜索并读取正文，要求技术回答有文档依据；前端区分执行阶段并允许中断。

【分析、评价】现有 static1 的固定页面短摘要、中文词项不匹配、历史无限增长和错误被当作成功保存影响核心体验。此次采用本地文档正文缓存与分块 BM25，保留个人版无登录架构；借鉴搜索→阅读→回答、输入预算和可中断流式流程。没有复制上游源码，不依赖其托管身份和 Pylon 凭据。

【验收】离线测试验证检索、历史预算、错误流、页面接口；真实文档抓取验证 Markdown 正文来源。模型付费调用与浏览器视觉检查单独记录，不以离线测试替代。

【非目标与停止条件】本阶段先稳定 LangChain 文档核心链路；Strata NEXT 的私有文件导入、通用网页导入保持待办。
