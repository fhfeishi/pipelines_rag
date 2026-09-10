from fastapi.testclient import TestClient

from src.main import app


def test_health_endpoint_is_available() -> None:
    with TestClient(app) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_chat_requires_user_message() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/chat",
            json={"messages": [{"role": "assistant", "content": "hello"}]},
        )

    assert response.status_code == 422
