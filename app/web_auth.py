import hashlib, datetime as dt
from fastapi import Request, HTTPException, Depends
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.db.models import SessionToken, User

COOKIE_NAME="asgl_session"

def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    raw = request.cookies.get(COOKIE_NAME)
    if not raw:
        raise HTTPException(status_code=401, detail="Not authenticated")
    th = hashlib.sha256(raw.encode()).hexdigest()
    sess = db.query(SessionToken).filter(SessionToken.token_hash==th).first()
    now = dt.datetime.utcnow()
    if not sess or sess.expires_at < now:
        raise HTTPException(status_code=401, detail="Session expired")
    # Absolute lifetime cap, then sliding idle window (refreshed at most hourly;
    # older long-lived sessions are clamped down to the idle window).
    from app.auth import SESSION_HOURS, SESSION_MAX_DAYS
    if sess.created_at and sess.created_at + dt.timedelta(days=SESSION_MAX_DAYS) < now:
        raise HTTPException(status_code=401, detail="Session expired")
    target = now + dt.timedelta(hours=SESSION_HOURS)
    if sess.expires_at > target or target - sess.expires_at > dt.timedelta(hours=1):
        try:
            sess.expires_at = target
            db.commit()
        except Exception:
            db.rollback()
    user = db.query(User).filter(User.id==sess.user_id, User.is_active==True).first()
    if not user:
        raise HTTPException(status_code=401, detail="User inactive")
    return user
