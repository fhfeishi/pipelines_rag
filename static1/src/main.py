"""FastAPI entrypoint for the personal LangChain documentation assistant."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from .agent.config import STATIC1_ROOT, get_settings
from .agent.docs import DocsIndex, SearchResult, build_context

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
settings = get_settings()
docs_index = DocsIndex(
    settings.docs_cache_path,
    timeout=settings.docs_request_timeout,
    max_pages=settings.docs_max_pages,
)


class ChatMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=12000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=20)


class RefreshResponse(BaseModel):
    documents: int
    message: str


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    logger.info("static1 personal assistant started; cached docs=%s", len(docs_index.records))
    yield


app = FastAPI(
    title="static1 Personal Chat LangChain",
    description="A single-user LangChain documentation assistant powered by DeepSeek.",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/api/health")
async def health() -> dict[str, object]:
    return {
        "status": "ok",
        "model": settings.deepseek_model,
        "docs_ready": docs_index.ready,
        "docs_count": len(docs_index.records),
        "api_key_configured": bool(settings.deepseek_api_key),
    }


@app.get("/api/docs")
async def search_docs(query: str = "", limit: int = 4) -> dict[str, object]:
    if query.strip() and not docs_index.ready:
        try:
            await asyncio.to_thread(docs_index.ensure_ready)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    results = docs_index.search(query, limit=max(1, min(limit, 8)))
    return {"ready": docs_index.ready, "count": len(docs_index.records), "results": results}


@app.post("/api/docs/refresh", response_model=RefreshResponse)
async def refresh_docs() -> RefreshResponse:
    try:
        count = await asyncio.to_thread(docs_index.refresh)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return RefreshResponse(documents=count, message="LangChain 官方文档已更新。")


def _model() -> ChatOpenAI:
    if not settings.deepseek_api_key:
        raise HTTPException(
            status_code=503,
            detail="尚未配置 DEEPSEEK_API_KEY。请复制 .env.example 为 .env 并填入 DeepSeek API Key。",
        )
    return ChatOpenAI(
        model=settings.deepseek_model,
        api_key=settings.deepseek_api_key.get_secret_value(),
        base_url=settings.deepseek_base_url,
        temperature=0.2,
        streaming=True,
    )


def _system_prompt(context: str) -> str:
    return f"""你是一个个人版 LangChain 文档助手。你只能把下面检索到的官方文档作为事实依据；如果文档不足以回答，要明确说明，不要编造 API、版本或参数。

回答要求：
- 使用中文，必要时保留英文 API 名称。
- 先给结论，再给简短步骤或代码示例。
- 涉及代码时使用 Markdown fenced code block。
- 在相关段落末尾使用 [文档标题](URL) 形式引用来源。
- 不要讨论登录、用户认证或本应用内部实现，除非用户直接询问。

检索到的 LangChain 官方文档：
{context}
"""


def _source_payload(results: list[SearchResult]) -> list[dict[str, str]]:
    return [{"title": item.title, "url": item.url, "snippet": item.snippet} for item in results]


def _sse(event: str, payload: object) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _chat_stream(request: ChatRequest) -> AsyncIterator[str]:
    query = request.messages[-1].content
    if not docs_index.ready:
        try:
            await asyncio.to_thread(docs_index.ensure_ready)
        except RuntimeError as exc:
            yield _sse("error", {"message": str(exc)})
            return

    results = docs_index.search(query)
    yield _sse("sources", _source_payload(results))
    try:
        model = _model()
        messages = [SystemMessage(content=_system_prompt(build_context(results)))]
        for item in request.messages[-12:]:
            if item.role == "user":
                messages.append(HumanMessage(content=item.content))
            else:
                messages.append(AIMessage(content=item.content))
        async for chunk in model.astream(messages):
            content = chunk.content
            if isinstance(content, str) and content:
                yield _sse("token", {"text": content})
        yield _sse("done", {"ok": True})
    except HTTPException as exc:
        yield _sse("error", {"message": exc.detail})
    except Exception as exc:  # pragma: no cover - provider errors vary by SDK version.
        logger.exception("DeepSeek request failed")
        yield _sse("error", {"message": f"DeepSeek 请求失败：{exc}"})


@app.post("/api/chat")
async def chat(request: ChatRequest) -> StreamingResponse:
    if request.messages[-1].role != "user":
        raise HTTPException(status_code=422, detail="最后一条消息必须来自用户。")
    return StreamingResponse(
        _chat_stream(request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(Path(STATIC1_ROOT / "frontend" / "index.html"))


@app.get("/{asset_path:path}")
async def frontend_asset(asset_path: str) -> FileResponse:
    frontend_root = Path(STATIC1_ROOT / "frontend").resolve()
    requested = (frontend_root / asset_path).resolve()
    if frontend_root not in requested.parents or not requested.is_file():
        raise HTTPException(status_code=404, detail="资源不存在。")
    return FileResponse(requested)


__all__ = ["app"]
