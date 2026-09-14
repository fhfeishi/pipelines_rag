# 实现边界

四层设计、当前HTTP/内部契约、混合检索pipeline及上游映射见 [API_PIPELINE.md](API_PIPELINE.md)。该文档明确区分已实现与后续分层计划。

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
- 文档导入与更新；LiteParse 本地解析。
- Crawl4AI 或 Firecrawl 网页预览确认入库、按域名加载用户会话。
- Deep Agents 两个业务工具：search_docs 和 read_doc。
- LangGraph 研究 → 验证 → 补查/回答。
- SSE 过程、来源与文本；断开请求取消当前请求任务。
- React 统一入口；FastAPI 可直接托管生产构建。
- LangSmith 可配置 tracing。

## 当前验证的边界
验证节点核对的是文档存在、版本匹配和正文非空。它不会用模型自报 confidence 代替事实核验。
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
