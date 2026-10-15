import datetime as dt, hashlib, secrets
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(pw:str)->str:
    return pwd_context.hash(pw)

def verify_password(pw:str, pw_hash:str)->bool:
    return pwd_context.verify(pw, pw_hash)

def new_session_token():
    raw = secrets.token_urlsafe(48)
    h = hashlib.sha256(raw.encode()).hexdigest()
    return raw, h

SESSION_HOURS = 12          # idle window, refreshed while the user is active
SESSION_MAX_DAYS = 7        # absolute lifetime regardless of activity

def token_expiry(hours=SESSION_HOURS):
    return dt.datetime.utcnow() + dt.timedelta(hours=hours)
