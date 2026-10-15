"""Oversight-A sign in extensions: forced password change on first sign in, and Google sign in.

Forced change: users flagged in `user_security.must_change_password` can only reach the change password
page, sign out and /auth/*. Everything else redirects (pages) or answers 403 password_change_required (API).
Google sign in: authorisation code flow. It only signs in an EXISTING active user whose Google email is
verified and matches. It never creates accounts. Enabled when GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are set.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import os
import secrets
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import Boolean, ForeignKey, Integer
from sqlalchemy.orm import Mapped, Session, mapped_column
from starlette.concurrency import run_in_threadpool

from app.auth import hash_password, new_session_token, token_expiry, verify_password
from app.db.models import SessionToken, User
from app.db.session import Base, SessionLocal, get_db
from app.rbac import COOKIE_NAME, Principal, get_principal

router = APIRouter()

MIN_LEN = 10
STATE_COOKIE = "asgl_oauth_state"
EXEMPT_PREFIXES = ("/change-password", "/auth/", "/signin", "/static", "/health", "/favicon")


class UserSecurity(Base):
    __tablename__ = "user_security"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)


def set_must_change(db: Session, user_id: int, value: bool = True) -> None:
    row = db.get(UserSecurity, user_id)
    if row is None:
        db.add(UserSecurity(user_id=user_id, must_change_password=value))
    else:
        row.must_change_password = value
    db.commit()


def _flagged(raw: str) -> bool:
    th = hashlib.sha256(raw.encode()).hexdigest()
    db = SessionLocal()
    try:
        s = db.query(SessionToken).filter(SessionToken.token_hash == th, SessionToken.expires_at >= dt.datetime.utcnow()).first()
        if not s:
            return False
        row = db.get(UserSecurity, s.user_id)
        return bool(row and row.must_change_password)
    finally:
        db.close()


def _https(request: Request) -> bool:
    return request.headers.get("x-forwarded-proto", request.url.scheme) == "https"


def _start_session(request: Request, db: Session, user: User, to: str = "/") -> RedirectResponse:
    raw, th = new_session_token()
    db.add(SessionToken(user_id=user.id, token_hash=th, expires_at=token_expiry()))
    db.commit()
    resp = RedirectResponse(url=to, status_code=303)
    resp.set_cookie(COOKIE_NAME, raw, httponly=True, samesite="lax", secure=_https(request))
    return resp


# ── forced change enforcement ───────────────────────────────────────────────
async def _enforce(request: Request, call_next):
    path = request.url.path
    raw = request.cookies.get(COOKIE_NAME)
    if raw and not path.startswith(EXEMPT_PREFIXES) and await run_in_threadpool(_flagged, raw):
        if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
            return RedirectResponse(url="/change-password", status_code=303)
        return JSONResponse({"detail": "password_change_required"}, status_code=403)
    return await call_next(request)


# ── change password ─────────────────────────────────────────────────────────
_PAGE = """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Change password · Oversight</title><style>
*{box-sizing:border-box}body{font-family:Inter,system-ui,sans-serif;background:#F1F5F9;color:#1E293B;margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:16px}
.c{background:#fff;border:1px solid #E2E8F0;border-radius:16px;padding:24px;width:100%;max-width:420px}
h2{margin:0 0 6px;font-size:1.3rem}p{color:#64748B;font-size:13px;margin:0 0 8px}
label{display:block;font-size:12px;font-weight:600;color:#64748B;text-transform:uppercase;margin-top:14px}
input{width:100%;padding:10px 14px;border:1px solid #E2E8F0;border-radius:10px;font-size:14px;margin-top:4px}
button{width:100%;margin-top:18px;padding:12px;border:0;border-radius:10px;background:#1E293B;color:#fff;font-weight:600;font-size:14px;cursor:pointer}
.e{background:#FEE2E2;color:#991B1B;border-radius:8px;padding:8px 12px;font-size:13px;margin-top:12px}
</style></head><body><div class="c"><h2>Choose a new password</h2>
<p>__WHY__ At least __MIN__ characters, different from your current one.</p>__ERR__
<form method="post" action="/auth/change-password">
<label>Current password</label><input name="current_password" type="password" autocomplete="current-password" required>
<label>New password</label><input name="new_password" type="password" autocomplete="new-password" minlength="__MIN__" required>
<label>Repeat new password</label><input name="confirm_password" type="password" autocomplete="new-password" minlength="__MIN__" required>
<button type="submit">Save password</button></form>
<form method="post" action="/auth/logout" style="margin-top:10px"><button type="submit" style="background:#fff;color:#475569;border:1px solid #E2E8F0">Sign out</button></form>
</div></body></html>"""

_ERRS = {
    "current": "Your current password is not correct.",
    "mismatch": "The two new passwords do not match.",
    "short": "The new password is too short.",
    "same": "The new password must be different from the current one.",
}


@router.get("/change-password", response_class=HTMLResponse, include_in_schema=False)
def change_password_page(request: Request, db: Session = Depends(get_db)):
    try:
        p = get_principal(request, db)
    except HTTPException:
        return RedirectResponse(url="/signin", status_code=303)
    row = db.get(UserSecurity, p.user.id) if p.user else None
    forced = bool(row and row.must_change_password)
    err = request.query_params.get("err", "")
    why = "You must set your own password before you continue." if forced else "Update your password."
    html = _PAGE.replace("__MIN__", str(MIN_LEN)).replace("__WHY__", why)
    html = html.replace("__ERR__", '<div class="e">%s</div>' % _ERRS[err] if err in _ERRS else "")
    return HTMLResponse(html)


@router.post("/auth/change-password", include_in_schema=False)
def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db),
):
    p: Principal = get_principal(request, db)
    if p.user is None:
        raise HTTPException(403, "Sign in as a user to change a password")
    u = p.user
    if not verify_password(current_password, u.password_hash):
        return RedirectResponse(url="/change-password?err=current", status_code=303)
    if new_password != confirm_password:
        return RedirectResponse(url="/change-password?err=mismatch", status_code=303)
    if len(new_password) < MIN_LEN:
        return RedirectResponse(url="/change-password?err=short", status_code=303)
    if verify_password(new_password, u.password_hash):
        return RedirectResponse(url="/change-password?err=same", status_code=303)
    u.password_hash = hash_password(new_password)
    keep = hashlib.sha256(request.cookies.get(COOKIE_NAME, "").encode()).hexdigest()
    db.query(SessionToken).filter(SessionToken.user_id == u.id, SessionToken.token_hash != keep).delete()
    db.commit()
    set_must_change(db, u.id, False)
    return RedirectResponse(url="/", status_code=303)


# ── Google sign in ──────────────────────────────────────────────────────────
def _google_cfg():
    cid = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    sec = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
    redir = os.getenv("GOOGLE_REDIRECT_URI", "https://app.oversightlab.ai/auth/google/callback").strip()
    return (cid, sec, redir) if cid and sec else None


@router.get("/auth/providers", include_in_schema=False)
def providers():
    return {"google": _google_cfg() is not None}


@router.get("/auth/google/start", include_in_schema=False)
def google_start(request: Request):
    cfg = _google_cfg()
    if not cfg:
        return RedirectResponse(url="/signin?err=google_off", status_code=303)
    cid, _, redir = cfg
    state = secrets.token_urlsafe(24)
    q = urlencode({"client_id": cid, "redirect_uri": redir, "response_type": "code",
                   "scope": "openid email profile", "state": state, "prompt": "select_account"})
    resp = RedirectResponse(url="https://accounts.google.com/o/oauth2/v2/auth?" + q, status_code=303)
    resp.set_cookie(STATE_COOKIE, state, httponly=True, samesite="lax", secure=_https(request), max_age=600)
    return resp


@router.get("/auth/google/callback", include_in_schema=False)
def google_callback(request: Request, code: str = "", state: str = "", db: Session = Depends(get_db)):
    cfg = _google_cfg()
    if not cfg:
        return RedirectResponse(url="/signin?err=google_off", status_code=303)
    cid, sec, redir = cfg
    cookie = request.cookies.get(STATE_COOKIE, "")
    if not code or not state or not cookie or not hmac.compare_digest(state, cookie):
        return RedirectResponse(url="/signin?err=google_state", status_code=303)
    try:
        with httpx.Client(timeout=10) as c:
            t = c.post("https://oauth2.googleapis.com/token", data={
                "code": code, "client_id": cid, "client_secret": sec,
                "redirect_uri": redir, "grant_type": "authorization_code"})
            t.raise_for_status()
            info = c.get("https://openidconnect.googleapis.com/v1/userinfo",
                         headers={"Authorization": "Bearer " + t.json()["access_token"]})
            info.raise_for_status()
            info = info.json()
    except Exception:
        return RedirectResponse(url="/signin?err=google_fail", status_code=303)
    email = (info.get("email") or "").strip().lower()
    if not email or info.get("email_verified") is not True:
        return RedirectResponse(url="/signin?err=google_fail", status_code=303)
    user = db.query(User).filter(User.email == email, User.is_active == True).first()  # noqa: E712
    if not user:
        return RedirectResponse(url="/signin?err=google_noaccount", status_code=303)
    resp = _start_session(request, db, user)
    resp.delete_cookie(STATE_COOKIE)
    return resp


def install_auth_ext(app) -> None:
    app.include_router(router)
    app.middleware("http")(_enforce)
