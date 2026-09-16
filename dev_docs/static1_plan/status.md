# static1 方向与进度

> 文档归属：static1。2026-09-16 从 `static1/Strata.md` 迁入。本文件是此主题的唯一维护入口。正文中的源码路径以仓库根目录或明确标注的 static1 目录为基准，不以本文目录为基准。


当前目标：面向小型本地文本、PDF 与指定网页快照的单用户 Agentic RAG。下一里程碑参照chat-langchain，在本地显式实现官方文档研究并与网页端对照。
以合并后的 [Agentic RAG 提升计划](roadmap.md) 和 chat-langchain 源码映射为参考，保留一套源码与运行入口。

## 2026-09-16 答案操作与耗时（已实现）

已完成答案Markdown复制；最新一轮重新生成并保留旧版本；浏览器端首token/Think等待和完成总耗时（停止/失败仅记录已耗时）；导出含版本、状态和可空耗时。TypeScript/生产构建、6项前端测试与离线浏览器交互验证通过。没有运行真实模型评测。

本次计时是用户端等待，尚无模型内部Think计时、usage/cache账本或完整步骤历史。审核反馈后的具体执行pipeline和unknown统计规则见 [roadmap第11节](roadmap.md#11-2026-09-16-实践反馈后的执行-pipeline)。

## 2026-09-16 研究路由第一批（代码完成，质量待评测）

已实现：重新生成从生效版本重建历史，API拒绝附加研究状态，每轮新建状态；finish_research结构化交接；按子问题覆盖与缺口补查；输出问题不触发搜索；无进展/预算停止；可核验官方URL缺页且local-only时硬停止。单次未命中仍为unknown。默认EVIDENCE_ROUTING=true，可关闭比较旧路径。

最终43项后端测试及ruff通过。6项前端测试、生产构建和离线浏览器交互通过。实际Deep Agents内部工具终止行为用替身模型验证，未调用真实模型。独立语义裁判、完整manifest、自动补页、章节阅读与usage未实现。

## 下一阶段优先级

P0：先建立run级模型用量/阶段耗时账本与官方文档人工核对题集，分别测量质量、延迟、token。

P1：基础缺口路由已实现；下一步用人工核对小题集验证子问题覆盖、定向补查、短排查回答与审计模式，完成真实模型质量验收。

P2—P3：按失败样例优化章节阅读、代码完整性、上下文去重与按需核验，再实现共享预算及快/标准/深入策略。

P4—P5：持久会话按需实施；重排、多Agent、多模型和并行只做单项有收益证据的实验，不默认扩张架构。详细状态、验收阈值和回退规则以合并计划为准。

已实现：
- 启动与检索就绪分离、前端加载提示、初始化期间写入保护；HLD.md / LLD.md 维护系统边界、模块契约与状态流程。
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
