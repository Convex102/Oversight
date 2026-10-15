import os
import tempfile

os.environ.setdefault("DATABASE_URL", "sqlite:///" + tempfile.mkdtemp() + "/t.db")


def test_redact_error_removes_client_data():
    from app.hardening import redact_error
    t = redact_error("failed for jo@client.co.uk balance 1,234,567.89 password=hunter2 Bearer abcdefghijklmnop1234")
    assert "client.co.uk" not in t and "1,234,567" not in t and "hunter2" not in t and "abcdefghijklmnop1234" not in t
    assert len(redact_error("x" * 5000)) == 500


def test_storage_delete_stays_inside_root(tmp_path):
    from app import storage as mod
    import pytest
    st = mod.Storage()
    st.local_root = tmp_path.resolve()
    uri = st.put("engagements/9/corrections/a.txt", b"x")
    assert st.delete(uri) is True
    assert st.delete(uri) is False
    outside = tmp_path.parent / "keep.txt"
    outside.write_text("keep")
    with pytest.raises(ValueError):
        st.delete("file://" + str(outside))
    assert outside.exists()


def test_erase_engagement_removes_content_keeps_audit(tmp_path):
    import importlib
    importlib.import_module("app.main")
    from app import storage as mod
    from app.db.session import Base, engine, SessionLocal
    from app.db.models import AuditEvent, Engagement, EngagementRun, ReviewPoint
    from app.gdpr import erase_engagement
    Base.metadata.create_all(bind=engine)
    mod.storage.local_root = tmp_path.resolve()
    uri = mod.storage.put("engagements/5/corrections/1_a.txt", b"client data")
    db = SessionLocal()
    try:
        e = Engagement(tenant_id=3, client_name="Acme Ltd", period="FY25", preparer_name="Pat", status="reviewed")
        other = Engagement(tenant_id=4, client_name="Other Ltd", period="FY25", status="reviewed")
        db.add_all([e, other]); db.commit()
        db.add(ReviewPoint(engagement_id=e.id, title="t", description="d", correction_file_uri=uri))
        db.add(EngagementRun(engagement_id=e.id, file_a_name="tb.xlsx"))
        db.add(AuditEvent(engagement_id=e.id, action="created")); db.commit()
        key_uri = mod.storage.put(f"engagements/{e.id}/corrections/2_b.txt", b"more")
        assert erase_engagement(db, 99, e.id, "x") is None          # other tenant cannot erase
        res = erase_engagement(db, 3, e.id, "admin@firm.co", "client request"); db.commit()
        assert res["complete"] and res["files_deleted"] >= 1
        assert not mod.storage.list_uris(f"engagements/{e.id}/")
        db.refresh(e)
        assert e.client_name == "[erased]" and e.status == "void" and e.preparer_name is None
        assert db.query(ReviewPoint).filter(ReviewPoint.engagement_id == e.id).count() == 0
        assert db.query(EngagementRun).filter(EngagementRun.engagement_id == e.id).count() == 0
        acts = [a.action for a in db.query(AuditEvent).filter(AuditEvent.engagement_id == e.id)]
        assert "created" in acts and "erasure_completed" in acts
        db.refresh(other); assert other.client_name == "Other Ltd"
        assert erase_engagement(db, 3, e.id, "admin@firm.co")["already_erased"]
    finally:
        db.close()


def test_purge_old_job_artifacts(tmp_path):
    import importlib, time
    importlib.import_module("app.main")
    from app import storage as mod
    from app.gdpr import purge_old_job_artifacts
    mod.storage.backend = "local"
    mod.storage.local_root = tmp_path.resolve()
    old = tmp_path / "tenants" / "3" / "jobs" / "j1" / "error.txt"
    new = tmp_path / "tenants" / "3" / "jobs" / "j2" / "error.txt"
    other = tmp_path / "tenants" / "4" / "jobs" / "j3" / "error.txt"
    for f in (old, new, other):
        f.parent.mkdir(parents=True); f.write_text("x")
    t = time.time() - 40 * 86400
    os.utime(old, (t, t)); os.utime(other, (t, t))
    assert purge_old_job_artifacts(3, 30)["deleted"] == 1
    assert not old.exists() and new.exists() and other.exists()


def test_erase_route_needs_admin_and_confirmation():
    import importlib
    from fastapi.testclient import TestClient
    main = importlib.import_module("app.main")
    c = TestClient(main.app, follow_redirects=False)
    assert c.post("/api/v1/engagements/1/erase", json={"confirm": "ERASE 1"}).status_code == 401
    assert c.post("/api/v1/admin/retention/purge").status_code == 401
