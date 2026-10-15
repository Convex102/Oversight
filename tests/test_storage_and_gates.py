import importlib
import os
import tempfile

os.environ.setdefault("DATABASE_URL", "sqlite:///" + tempfile.mkdtemp() + "/t.db")


def test_storage_key_helpers(tmp_path, monkeypatch):
    from app import storage as mod
    st = mod.Storage()
    st.local_root = tmp_path.resolve()
    st.append_jsonl("t/a.jsonl", {"x": 1})
    st.append_jsonl("t/a.jsonl", {"x": 2})
    assert st.read("t/a.jsonl").count(b"\n") == 2
    st.write_json("t/r.json", {"k": 1})
    assert st.read_json("t/r.json") == {"k": 1}
    assert st.read_json("t/missing.json") is None
    assert len(st.list_uris("t")) == 2


def test_optional_routers_are_license_gated():
    main = importlib.import_module("app.main")
    gated = {}
    for r in main.app.routes:
        ep = getattr(r, "endpoint", None)
        if not ep or not hasattr(r, "dependant"):
            continue
        mod = ep.__module__
        if any(x in mod for x in ("pension_batch_task", "active_learning_routes", "telemetry_routes", "di_admin_api")):
            names = [getattr(d.call, "__name__", "") for d in r.dependant.dependencies]
            gated[r.path] = "_dep" in names
    assert gated and all(gated.values()), gated


def test_api_docs_need_login():
    from fastapi.testclient import TestClient
    main = importlib.import_module("app.main")
    c = TestClient(main.app, follow_redirects=False)
    assert c.get("/docs").status_code == 401
    assert c.get("/openapi.json").status_code == 401
