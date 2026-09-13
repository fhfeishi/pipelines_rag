# 静知 · Agentic RAG Static

单用户本地知识库问答。React + TypeScript + Tailwind 前端，FastAPI 后端，Deep Agents 研究节点，LangGraph 有限循环，LangSmith 可选追踪。

## 启动（WSL / Python 3.12+）
```bash
cd /home/baheas/wslcodespace/pipelines_rag/static1
../.venv/bin/python -m pip install -e ".[web,dev]"
../.venv/bin/python -m playwright install chromium
# 如需修改配置，复制 .env.example 为 .env 后编辑；已有根目录 .env 也会读取。
../.venv/bin/python -m src.cli ingest
cd frontend
npm ci
npm run build
cd ..
../.venv/bin/python -m uvicorn src.main:app --host 127.0.0.1 --port 8000
```
打开 http://localhost:8000 。开发时另开终端在 frontend 运行 npm run dev，访问 5173；/api 自动代理到 8000。
前端只有 frontend 一份，原 web 已迁入，旧原生 JS 页面已移除。

## 默认样本
- knowledge/project_progress/texts/v4 下的 txt、md。
- knowledge 下递归找到的 PDF。
- 支持 TEXT_ROOT 和 KNOWLEDGE_ROOT 分别覆盖。
- 点击“导入 / 更新本地文本与 PDF”可重新读取；同一来源更新替换当前版本，不重复新增。
- 单文件解析失败会保留该来源旧数据，并在导入结果中报告失败。
- 当前不自动删除磁盘上已经移除的来源。

## 网页快照及登录
这里的快照是“当时提取到的文本正文 + URL + 抓取时间 + 内容版本”，不是完整网页离线归档或截图。

默认使用本地 Crawl4AI。页面输入 URL → 抓取预览 → 人工确认正文 → 入库。抓取不会自动递归全站，也不会在每轮问答时重新抓取。

需要登录的页面必须已有有效会话。可在 WSL 有图形桌面的环境里使用：
```bash
mkdir -p .sessions
../.venv/bin/python -m playwright codegen --save-storage=.sessions/site.json https://你的站点
```
在打开的窗口中自己登录、完成二次验证后关闭。然后创建 .sessions/hosts.json：
```json
{
  "你的站点域名": {
    "storage_state": "/绝对路径/static1/.sessions/site.json"
  }
}
```
配置 WEB_SESSIONS_FILE 指向 hosts.json。Crawl4AI 按请求域名加载该 storage state。
会话过期需重新登录。会话格式、设备绑定、浏览器指纹、验证码或 SSO 可能使导出状态不足以访问；工具不保证能处理所有受保护页面。预览用于识别误抓的登录页。

可切换到 Firecrawl：
```dotenv
WEB_PROVIDER=firecrawl
FIRECRAWL_API_KEY=你的密钥
FIRECRAWL_BASE_URL=https://api.firecrawl.dev
```
Firecrawl 适配 /v2/scrape。需要 Cookie 等请求头时，hosts.json 对应域名配置 headers 对象。这些请求头会交给配置的 Firecrawl 服务执行抓取；只在你愿意向该服务提供会话时使用。本地 Crawl4AI 不需要 Firecrawl Key。
会话文件不进入知识库、浏览器 API 或 Agent 上下文；.sessions 已忽略。

## PDF
使用本地 LiteParse 2.4.0 Python SDK，保存逐页文本和原始页码，默认开启 OCR（语言 eng，可通过 PDF_OCR_LANGUAGE 修改；对应语言资源需可用）。
LiteParse 解析成功不等于复杂表格、扫描件的语义一定正确。当前验证样本为三份英文 PDF，尚未量化复杂中文扫描表格质量。
没有默认上传 PDF 到 LlamaCloud。将来需要 LlamaParse 云端时，在 src/parsers.py 增加一个返回 Document 的适配即可；目前未实现云端调用。

## 切换模型
统一 OpenAI 兼容接口：
```dotenv
MODEL_NAME=deepseek-chat
MODEL_BASE_URL=https://api.deepseek.com
MODEL_API_KEY=你的密钥
```
兼容已有 DEEPSEEK_* 配置。Ollama / vLLM 等本地服务使用其 OpenAI 兼容 URL（例如 http://localhost:11434/v1），设置模型名与占位 Key。模型必须支持工具调用；本地模型本轮未实测。
不同协议的提供商只需修改 src/agent/models.py，解析与存储无需改动。

## 运行链路
```text
用户 → FastAPI → Deep Agent(search_docs → read_doc)
                     ↓
                LangGraph validate
                     ├─ 没有有效已读证据且未超预算 → 补查
                     └─ 有证据或预算耗尽 → answer → SSE
```
搜索使用成熟 rank-bm25 库（BM25Plus）和中文二元词项；按行窗口定位，read_doc 返回真实正文与页/行/版本。当前小库每次搜索重建排名器，不依赖向量服务。
行号对应规范化阅读视图：超过300字符的原始行按300字符分段；SQLite仍保存原解析正文。读取返回 next_start_line，可继续读取下一段。PDF 页码保持原始页码。
最多两轮研究，每轮图步数 24，最多6段已读证据，默认180秒超时。
validate 只确定性核验已读证据与当前版本，**不等于独立的语义充分性裁判**。回答模型负责说明不足；后续可基于评估增加语义验证。

## 存储与范围
- data/knowledge.sqlite3：原文页、标题、来源、parser、抓取时间、内容版本；不保存 Cookie。
- 来源链接带版本；来源更新后旧版本链接会明确报错，不静默展示新版作为旧证据。
- 当前仅保留每个来源的最新版本，无跨版本档案。
- 对话只保留在当前页面，未完成的回答不加入后续上下文。
- 无跨线程长期记忆；Deep Agents 默认状态文件不是知识库。
- FastAPI 是本轮运行入口。Managed Deep Agents 仍为后续可选部署适配，未声称已集成部署。
- 服务面向本机免登录使用；外网部署需另做身份与访问控制。

## LangSmith
配置 LANGSMITH_TRACING=true、LANGSMITH_API_KEY 和 LANGSMITH_PROJECT 后，框架追踪模型与工具调用。默认关闭；启用后研究上下文会发送到 LangSmith。当前未验证云端 trace 落库。

## 检查
```bash
../.venv/bin/python -m pytest tests -q
../.venv/bin/python -m ruff check src tests
cd frontend
npm run build
node --experimental-strip-types --test src/api.test.mts
```
浏览器联调脚本：tests/browser_smoke.py（需已运行的本地服务）。
登录会话机制验证：运行 python -m tests.auth_smoke，使用本地 Cookie 测试站点，不需要真实账号。
CLI 也支持 search、preview、ask；例如：
```bash
../.venv/bin/python -m src.cli search "南溪地基基础"
../.venv/bin/python -m src.cli ask "南溪地基基础施工何时完成？"
```

## 参考
- 两份笔记：../agentic-rag-static.md、../agentic-rag-static-2.md。
- 上游源码：../third_party/chat-langchain。
- [LiteParse Python](https://github.com/run-llama/liteparse/tree/main/packages/python)
- [Crawl4AI 认证](https://docs.crawl4ai.com/advanced/identity-based-crawling/)
- [Firecrawl scrape](https://docs.firecrawl.dev/api-reference/endpoint/scrape)
