"""One FastAPI entrypoint for corpus ingestion, evidence and agent streaming."""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from .agent.config import STATIC1_ROOT, get_settings
from .agent.graph import build_graph
from .agent.models import tracing
from .knowledge import Knowledge
from .parsers import import_defaults, parse_web

logger = logging.getLogger(__name__)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=12000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=20)


class WebRequest(BaseModel):
    url: str = Field(min_length=1, max_length=4000)


def sse(event: str, data: object) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def create_app(settings=None, knowledge=None, graph_factory=build_graph):
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app):
        app.state.knowledge = knowledge or Knowledge(settings.data_dir / "knowledge.sqlite3")
        app.state.import_lock = asyncio.Lock()
        app.state.preview = None
        yield

    app = FastAPI(title="Agentic RAG Static", version="0.2.0", lifespan=lifespan)

    @app.get("/api/health")
    async def health(request: Request):
        docs = await asyncio.to_thread(request.app.state.knowledge.all)
        return {
            "status": "ok",
            "model": settings.model_name,
            "docs_count": len(docs),
            "api_key_configured": bool(settings.model_api_key),
            "web_provider": settings.web_provider,
        }

    @app.get("/api/documents")
    async def documents(request: Request):
        docs = await asyncio.to_thread(request.app.state.knowledge.all)
        return [{k: v for k, v in d.items() if k != "pages"} | {"pages": len(d["pages"])} for d in docs]

    @app.get("/api/documents/{doc_id}")
    async def read_document(
        request: Request, doc_id: str, page: int = 1, start_line: int = 1, version: str | None = None
    ):
        try:
            return await asyncio.to_thread(
                request.app.state.knowledge.read, doc_id, page, start_line, 60, version
            )
        except KeyError as exc:
            raise HTTPException(404, "文档不存在") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/ingest/local")
    async def ingest(request: Request):
        async with app.state.import_lock:
            return await asyncio.to_thread(import_defaults, app.state.knowledge, settings)

    @app.post("/api/web/preview")
    async def preview(payload: WebRequest):
        try:
            doc = await parse_web(payload.url, settings)
            import uuid

            preview_id = uuid.uuid4().hex
            # Single-user app: only latest preview retained, not arbitrary browser-supplied content.
            app.state.preview = (preview_id, doc)
            return {"preview_id": preview_id, **doc.model_dump()}
        except (ValueError, TimeoutError) as exc:
            raise HTTPException(422, str(exc) or "网页抓取超时") from exc
        except Exception as exc:
            logger.warning("web preview failed: %s", type(exc).__name__)
            raise HTTPException(502, "抓取失败，请检查解析器安装、会话或网站访问权限") from exc

    @app.post("/api/web/confirm/{preview_id}")
    async def confirm(preview_id: str):
        async with app.state.import_lock:
            current = app.state.preview
            if current is None or current[0] != preview_id:
                raise HTTPException(409, "预览已失效，请重新抓取")
            result = await asyncio.to_thread(app.state.knowledge.put, current[1])
            app.state.preview = None
            return result

    @app.post("/api/chat")
    async def chat(payload: ChatRequest, request: Request):
        if payload.messages[-1].role != "user":
            raise HTTPException(422, "最后一条消息必须来自用户")
        if sum(len(m.content) for m in payload.messages) > 40000:
            raise HTTPException(422, "对话上下文超过4万字符，请新建对话")

        async def stream():
            try:
                async with asyncio.timeout(settings.run_timeout):
                    graph = graph_factory(app.state.knowledge, settings)
                    with tracing(settings):
                        async for event in graph.astream(
                            {
                                "messages": [m.model_dump() for m in payload.messages],
                                "rounds": 0,
                                "evidence": [],
                            },
                            stream_mode="custom",
                            config={"recursion_limit": 12, "tags": ["static1", "agentic-rag"]},
                        ):
                            if await request.is_disconnected():
                                return
                            yield sse(event["event"], event["data"])
                    yield sse("done", {"ok": True})
            except asyncio.CancelledError:
                raise
            except ValueError as exc:
                yield sse("error", {"message": str(exc)})
            except TimeoutError:
                yield sse("error", {"message": "运行达到时间预算，请缩小问题范围"})
            except Exception as exc:  # noqa: BLE001 - API boundary hides provider secrets
                logger.warning("chat failed: %s", type(exc).__name__)
                yield sse("error", {"message": "问答未完成，请检查模型配置或稍后重试"})

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/{asset_path:path}")
    async def frontend(asset_path: str):
        root = (STATIC1_ROOT / "frontend/dist").resolve()
        path = (root / (asset_path or "index.html")).resolve()
        if asset_path.startswith("api/") or not path.is_relative_to(root) or not path.is_file():
            raise HTTPException(404, "页面未构建或资源不存在；请在 frontend 执行 npm run build")
        return FileResponse(path)

    return app


app = create_app()
