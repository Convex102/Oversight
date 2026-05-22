from fastapi.testclient import TestClient

from app.main import app


def test_optional_routers_dont_break_startup():
    # Creating a client triggers startup events; if optional routers broke startup, this would fail.
    client = TestClient(app)
    # Just assert app responds to root page.
    r = client.get("/")
    assert r.status_code in (200, 307)
