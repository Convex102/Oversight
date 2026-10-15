import os
import tempfile

os.environ.setdefault("DATABASE_URL", "sqlite:///" + tempfile.mkdtemp() + "/t.db")


def test_jobs_survive_across_workers_and_isolate_tenants():
    import importlib
    importlib.import_module("app.main")
    from app import jobstore
    from app.db.session import Base, engine
    Base.metadata.create_all(bind=engine)

    jid = jobstore.create("7")
    jobstore.update(jid, status="running", started_at="2026-10-15T00:00:00Z")
    jobstore.update(jid, status="completed", results={"asgl_a": {"x": 1}, "weird": object()})
    # a second worker has no shared memory: it only has the database
    job = jobstore.get(jid)
    assert job["status"] == "completed" and job["results"]["asgl_a"] == {"x": 1}
    assert [j["job_id"] for j in jobstore.list_for_tenant("7")] == [jid]
    assert jobstore.list_for_tenant("8") == []
    assert "results" not in jobstore.list_for_tenant("7")[0]


def test_stalled_job_is_marked_failed():
    import datetime as dt
    import importlib
    importlib.import_module("app.main")
    from app import jobstore
    from app.db.session import SessionLocal
    jid = jobstore.create("7")
    db = SessionLocal()
    row = db.get(jobstore.PipelineJobRow, jid)
    row.updated_at = dt.datetime.utcnow() - dt.timedelta(hours=2)
    db.commit(); db.close()
    assert jobstore.get(jid)["status"] == "failed"
