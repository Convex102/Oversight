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
    if not sess or sess.expires_at < dt.datetime.utcnow():
        raise HTTPException(status_code=401, detail="Session expired")
    user = db.query(User).filter(User.id==sess.user_id, User.is_active==True).first()
    if not user:
        raise HTTPException(status_code=401, detail="User inactive")
    return user
