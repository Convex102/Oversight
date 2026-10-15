import importlib
import os
import tempfile

os.environ.setdefault("DATABASE_URL", "sqlite:///" + tempfile.mkdtemp() + "/t.db")
os.environ["ASGL_ALLOW_SIGNUP"] = "1"

from fastapi.testclient import TestClient  # noqa: E402


def _app():
    main = importlib.import_module("app.main")
    from app.db.session import Base, engine
    Base.metadata.create_all(bind=engine)
    return main.app


def test_security_headers_present():
    c = TestClient(_app(), follow_redirects=False)
    r = c.get("/signin", headers={"x-forwarded-proto": "https"})
    for h in ("content-security-policy", "x-content-type-options",
              "referrer-policy", "strict-transport-security"):
        assert h in r.headers, h


def test_cross_origin_write_refused():
    c = TestClient(_app(), follow_redirects=False)
    r = c.post("/auth/logout", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_oversize_upload_refused():
    c = TestClient(_app(), follow_redirects=False)
    big = b"x" * (80 * 1024 * 1024)
    r = c.post("/api/v1/pipeline/run", files=[("files", ("b.xlsx", big, "application/octet-stream"))])
    assert r.status_code == 413


def test_login_throttle_locks_after_five_failures():
    c = TestClient(_app(), follow_redirects=False)
    c.post("/auth/register", data={"tenant_name": "T", "email": "t@x.com", "password": "password123"})
    codes = [TestClient(_app(), follow_redirects=False).post(
        "/auth/login", data={"email": "t@x.com", "password": "bad%d" % i}).status_code for i in range(7)]
    assert codes[:5] == [401] * 5 and codes[5:] == [429, 429]
    right = TestClient(_app(), follow_redirects=False).post(
        "/auth/login", data={"email": "t@x.com", "password": "password123"})
    assert right.status_code == 429
