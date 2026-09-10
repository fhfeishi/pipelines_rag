# static1：个人版 Chat LangChain

这是一个面向个人使用的 LangChain 文档问答工具：打开浏览器即可使用，不包含注册、登录、Supabase 或多用户权限。后端使用 FastAPI，模型使用 DeepSeek，文档检索使用 LangChain 官方文档的本地缓存。

## 快速开始

在 `static1` 目录执行：

```powershell
Copy-Item .env.example .env
# 编辑 .env，填入 DEEPSEEK_API_KEY
python -m pip install -e .
python -m uvicorn src.main:app --host 127.0.0.1 --port 8000
```

然后打开 <http://127.0.0.1:8000>。第一次发送问题时，程序会抓取 LangChain 官方文档并保存到 `data/langchain_docs.json`；之后优先使用本地缓存。也可以点击页面里的“更新文档”。

## 功能范围

- 单用户本地使用，不做复杂认证。
- DeepSeek `deepseek-chat` 流式回答。
- LangChain / LangGraph / LangSmith 官方文档检索。
- 浏览器本地保存当前会话，后端不保存用户身份和聊天记录。
- 回答展示文档来源链接，便于核对。

## 目录

```text
static1/
├── frontend/          # FastAPI 直接托管的聊天界面
├── src/agent/config.py
├── src/agent/docs.py   # 文档抓取、缓存和检索
├── src/main.py         # API 和 SSE 流式对话
├── .env.example
└── pyproject.toml
```

## 说明

当前检索器为了保持个人版简单，使用本地缓存加关键词检索，不需要额外的 embedding 服务。后续如果接入本地私有文档，可以复用 `DocsIndex` 的缓存接口，再替换为向量检索或 BM25。
