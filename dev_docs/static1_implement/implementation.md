# 实现边界

> 文档归属：static1。2026-09-16 从 `static1/IMPLEMENTATION.md` 迁入。本文件是此主题的唯一维护入口。正文中的源码路径以仓库根目录或明确标注的 static1 目录为基准，不以本文目录为基准。


四层设计、当前HTTP/内部契约、混合检索pipeline及上游映射见 [API_PIPELINE.md](../static1_design/API_PIPELINE.md)。该文档明确区分已实现与后续分层计划。

依据两份 Agentic RAG 笔记及 chat-langchain 源码；SDK 行为以实际安装版为准。

## 唯一源码结构
```text
frontend/           React + TS + Tailwind（唯一前端）
src/
  main.py           FastAPI、SSE、预览确认、构建页面托管
  knowledge.py      Document/Page、SQLite、BM25 search/read
  dense.py          可选本地HuggingFace + Chroma、增量同步、RRF融合
  parsers.py        UTF-8、LiteParse、Crawl4AI、Firecrawl
  cli.py            本地导入、检索、预览、问答验证
  agent/
    config.py       环境配置
    models.py       模型与追踪适配
    graph.py        Deep Agent 研究 + LangGraph 核验与有界补查
tests/
```
用简单函数作为解析器边界，用 Document/Page 作为数据契约。暂不引入插件注册中心、服务容器或多层 repository。
本地纯文本、PDF 与网页共享同一知识存储及工具，换解析器无需改图。
OpenAI 兼容本地/远程模型共享 models.py；LangSmith 开关也在此控制。
MCP、Managed Deep Agents、多 Agent 是可选后续步骤，当前不引入空适配或未验证部署入口。

## 当前能力

- 答案Markdown复制、最新一轮重新生成与旧版本保留；浏览器首正文等待/完成总耗时、停止失败耗时及导出。状态契约见LLD第7节；尚无模型usage或内部推理耗时。
- 文档导入与更新；LiteParse 本地解析。
- Crawl4AI 或 Firecrawl 网页预览确认入库、按域名加载用户会话。
- Deep Agents 默认四个业务工具：search_docs、read_doc、check_corpus_page、finish_research；EVIDENCE_ROUTING=false保留原两工具路径。
- LangGraph 研究 → 验证 → 补查/回答。
- SSE 过程、来源与文本；断开请求取消当前请求任务。
- React 统一入口；FastAPI 可直接托管生产构建。
- LangSmith 可配置 tracing。

## 当前验证的边界
验证节点核对文档存在、版本匹配、正文非空和报告证据ID，并按结构化覆盖报告做有限路由。报告的语义支持判断仍来自研究模型，不等于独立事实裁判。缺页硬停止只信任本地目录核验，单次未命中不是缺页证明。
结构化证据来自实际工具读取，包含 doc_id、version、page、start_line、text；最终答案不能凭搜索摘要建立来源。
仍有改进空间：检索召回评估、独立语义判定、引用编号准确率、多用户与持久运行取消。
新增功能要用现有小库评估收益，避免先拆分多个 Agent。

## 接口
- GET /api/health、GET /api/documents。
- GET /api/documents/{doc_id}?page=1&start_line=1&version=...。
- POST /api/ingest/local：导入配置中的文本目录和 PDF。
- POST /api/web/preview：url，返回 preview_id、正文；不入库。
- POST /api/web/confirm/{preview_id}：确认最新预览入库。
- POST /api/chat：messages → SSE(status/sources/token/done/error)。
