from fastapi.testclient import TestClient

from src.agent.config import Settings
from src.knowledge import Document, Knowledge, Page
from src.main import create_app


def setup(tmp_path, graph_factory=None):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    store = Knowledge(tmp_path / "db")
    args = {"settings": settings, "knowledge": store}
    if graph_factory:
        args["graph_factory"] = graph_factory
    return create_app(**args), store


def test_api_and_validation(tmp_path):
    app, store = setup(tmp_path)
    key = store.put(
        Document(
            title="test", origin="test", kind="text", parser="text", pages=[Page(number=1, text="source")]
        )
    )["doc_id"]
    with TestClient(app) as client:
        assert client.get("/api/health").json()["docs_count"] == 1
        assert client.get("/api/documents/" + key).json()["text"] == "source"
        assert client.get("/api/documents/missing").status_code == 404
        assert (
            client.post("/api/chat", json={"messages": [{"role": "assistant", "content": "x"}]}).status_code
            == 422
        )
        assert client.post("/api/web/confirm/unknown").status_code == 409


def test_preview_requires_confirmation(tmp_path, monkeypatch):
    from src import main

    async def fake(*args):
        return Document(
            title="Page",
            origin="https://example.com",
            kind="web",
            parser="fake",
            pages=[Page(number=1, text="review first")],
        )

    monkeypatch.setattr(main, "parse_web", fake)
    app, store = setup(tmp_path)
    with TestClient(app) as client:
        preview = client.post("/api/web/preview", json={"url": "https://example.com"}).json()
        assert store.all() == []
        assert client.post("/api/web/confirm/" + preview["preview_id"]).status_code == 200
        assert len(store.all()) == 1
        assert client.post("/api/web/confirm/" + preview["preview_id"]).status_code == 409


def test_sse_success_and_failure(tmp_path):
    class Graph:
        async def astream(self, *args, **kwargs):
            yield {"event": "sources", "data": []}
            yield {"event": "token", "data": {"text": "answer"}}

    app, _ = setup(tmp_path, lambda *args: Graph())
    payload = {"messages": [{"role": "user", "content": "question"}]}
    with TestClient(app) as client:
        response = client.post("/api/chat", json=payload)
        assert response.text.index("event: sources") < response.text.index("event: token")
        assert "event: done" in response.text

    class Failed:
        async def astream(self, *args, **kwargs):
            raise RuntimeError("private key details")
            yield {}

    app, _ = setup(tmp_path, lambda *args: Failed())
    with TestClient(app) as client:
        response = client.post("/api/chat", json=payload)
        assert "event: error" in response.text
        assert "event: done" not in response.text
        assert "private key" not in response.text
