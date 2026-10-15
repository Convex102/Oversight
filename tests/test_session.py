import datetime as dt, hashlib
from fastapi import HTTPException
import pytest
from app.db.session import SessionLocal, Base, engine
from app.db.models import SessionToken, User
from app.web_auth import get_current_user
from app.auth import SESSION_HOURS


class Req:
    def __init__(self, raw): self.cookies = {"asgl_session": raw}


def _mk(db, raw, created, expires):
    u = db.query(User).filter(User.is_active == True).first()
    assert u, "needs a user"
    h = hashlib.sha256(raw.encode()).hexdigest()
    db.add(SessionToken(user_id=u.id, token_hash=h, created_at=created, expires_at=expires)); db.commit()
    return h


def test_session_rules():
    Base.metadata.create_all(engine)
    db = SessionLocal()
    if not db.query(User).first():
        pytest.skip("no user in test db")
    now = dt.datetime.utcnow()
    h1 = _mk(db, "long14", now, now + dt.timedelta(days=14))
    get_current_user(Req("long14"), db)
    s = db.query(SessionToken).filter_by(token_hash=h1).first()
    assert s.expires_at <= now + dt.timedelta(hours=SESSION_HOURS, minutes=1)
    _mk(db, "old", now - dt.timedelta(days=8), now + dt.timedelta(hours=5))
    with pytest.raises(HTTPException):
        get_current_user(Req("old"), db)
