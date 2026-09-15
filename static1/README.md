# 静知 · Agentic RAG Static

设计导航：[HLD 系统架构与工作流程](HLD.md) · [LLD 模块与状态契约](LLD.md) · [完整 API 与上游映射](API_PIPELINE.md)。

### 启动与加载状态

启动前检查端口：已运行的static1会提示复用地址并退出，不重复安装或启动；其他程序占用会明确报错，不终止其他程序。依赖按虚拟环境、项目路径、Python版本、pyproject.toml及extras记录安装标记，未变化时跳过uv安装；需要修复/升级依赖时使用`UPDATE_DEPS=1 bash launch.sh`。第一次生成标记仍会安装一次。

前端加载期间展示状态卡片与向量片段进度，可以先输入问题；就绪且配置密钥后发送按钮自动启用。失败与断连分别提示，不清空输入。索引没有可用总数时不显示虚构百分比。浏览器无法在端口被其他程序占用时展示本应用错误页，因为请求尚未到达本应用；此类错误在启动终端说明。

## 使用官方文档助手

1. 配置模型密钥，运行 `bash launch.sh` 或 `zsh launch.sh`。
2. 服务先提供网页，再后台准备Python官方文档和可选向量索引。侧栏显示准备状态，ready后可问答；已有本地文档复用。这样CPU向量化不再阻止网页打开。
3. 浏览器打开 http://localhost:8000，直接在页面输入框提问，例如“LangGraph 的短期记忆和长期记忆有什么区别？”或“Deep Agents 的 subagents 如何配置？”。不需要在命令行输入问答。侧栏的“导入 / 更新官方文档”用于之后手动刷新语料。
4. 在“已读证据”核对本地版本快照，用“官方原文”查看在线文档；点击“导出对话与证据版本”保存JSON用于对照。

端口被其他程序占用时，在终端运行 `PORT=18765 bash launch.sh`（zsh同样支持），访问 http://127.0.0.1:18765。启动采用asyncio事件循环。

官方更新失败会保留该来源已有正文，不会删除其他本地文档。当前对已有混合知识库统一检索，没有独立的官方文档过滤开关。导入不调用聊天模型；开启embedding后首次搜索会同步向量，较大语料可增加首次响应时间。

这一版本实现公开文档交流链路，不包含上游Pylon私有知识库、托管线程、登录系统或完整LangSmith反馈服务。官方文档仅覆盖上述Python三个分区，尚不覆盖全部JavaScript、LangSmith和集成参考页面。

实际问答、答案质量和chat-langchain网页端对照由你进行；本轮只执行离线自动化检查与前端构建。重启会终止正在运行的导入任务，重启后可再次更新；已入库页面保留。

设计与接口：[API_PIPELINE.md](API_PIPELINE.md)。`bash launch.sh` 与 `zsh launch.sh` 均可；zsh入口自动转交Bash执行，系统需已安装Bash。

单用户本地知识库问答。React + TypeScript + Tailwind 前端，FastAPI 后端，Deep Agents 研究节点，LangGraph 有限循环，LangSmith 可选追踪。

## 启动（WSL / Python 3.12+）
推荐在 static1 目录执行 `bash launch.sh`。优先复用终端已激活的 `VIRTUAL_ENV`，其次使用 `STATIC1_VENV` 指定的已有环境，再依次检查 static1/.venv、仓库 .venv。均不存在时执行 `uv venv --seed --python=3.12 .venv`。需要系统已有 uv 和 Node/npm；uv可按需获取Python。指定或激活的环境无效、Python低于3.12时明确报错，不另建环境掩盖问题。
依赖统一通过 `uv pip install --python <选中的解释器>` 安装；运行服务也使用同一个解释器，无需在static1重复创建环境。
启动时安装项目依赖，前端尚未构建时自动安装并构建；前端修改后使用 `REBUILD_FRONTEND=1 bash launch.sh`。脚本不覆盖已有 .env，也不自动导入文档；启动后可在页面导入。

## 可选本地 embedding

在 static1/.env 中设置，变量名大小写均可：
```dotenv
EMBEDDING_PATH=E:/local_models/embedding/iic--nlp_gte_sentence-embedding_chinese-base
EMBEDDING_DEVICE=cpu
EMBEDDING_QUERY_PROMPT=
```
路径指向具体模型目录，必须含 config.json。WSL 自动把 E:/ 转为 /mnt/e/；也可直接填写 Linux 路径。可以切换至 Qwen--Qwen3-Embedding-0.6B 或你的 GTE large 目录，模型需兼容 Sentence Transformers。Qwen 的检索指令可通过 EMBEDDING_QUERY_PROMPT 显式设置，例如 `Instruct: Retrieve relevant passages for the query. Query: `。本轮真实验证了本地 GTE base，Qwen/large 未运行。

EMBEDDING_PATH 留空或不设置时只使用 BM25，不导入或加载 embedding 依赖。指定路径时 launch.sh 自动安装 embedding 可选依赖；在已激活环境手动安装使用 `uv pip install -e '.[embedding]'`。

启用后使用 langchain-huggingface 加载本地模型（local_files_only），langchain-chroma 持久化到 data/chroma；第一次搜索同步向量，后续仅补新增/变化片段并清理旧版本。dense 与 BM25 使用相同的页/行窗口，RRF 融合排序，原有 Deep Agents search/read 和版本核验接口保持一致。模型路径、文件版本或查询提示变更后使用独立集合，首次重新编码；旧模型集合保留在磁盘。指定模型后加载失败会报错，不静默切回 BM25。

## 手动启动
```bash
cd /home/baheas/wslcodespace/pipelines_rag/static1
uv pip install --python ../.venv/bin/python -e ".[web,dev]"
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

## 可重复检索评估

在 static1 目录运行 `../.venv/bin/python -m src.evaluate --output reports/retrieval_v4.json`。
评估使用当前已导入的知识库和 retrieval_v4 数据集，分别报告来源召回、正文预期词项覆盖和首个正确来源排名。不会调用模型。
报告中的 evidence_recall 要求预期词项出现在正确来源的实际阅读正文中，不以其他来源的同名词项充数。该指标不是答案准确率，reliability_v4 中歧义和缺失问题仍需独立的真实模型回答评估。

## 参考资料
- 两份笔记：../agentic-rag-static.md、../agentic-rag-static-2.md。
- 上游源码：../third_party/chat-langchain。
- [LiteParse Python](https://github.com/run-llama/liteparse/tree/main/packages/python)
- [Crawl4AI 认证](https://docs.crawl4ai.com/advanced/identity-based-crawling/)
- [Firecrawl scrape](https://docs.firecrawl.dev/api-reference/endpoint/scrape)
