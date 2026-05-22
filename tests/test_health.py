from fastapi.testclient import TestClient

from app.main import app


def test_health_ok():
    client = TestClient(app)
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json().get("status") == "ok"
