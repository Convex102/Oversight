"""Oversight-A security hardening.

1. Login and reset throttling (database backed, so it holds across both workers and restarts).
2. Security response headers, including a Content Security Policy and HSTS over https.
3. Cross-origin write protection: a state changing request that carries an Origin header from another site is refused.
4. Upload size limit, enforced on the request body before any file is parsed (Content-Length and streamed bodies).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import Iterable, Optional
from urllib.parse import urlparse

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.config import settings
from app.db.session import Base

# ── throttle ────────────────────────────────────────────────────────────────
WINDOW = dt.timedelta(minutes=15)
LOCK = dt.timedelta(minutes=15)
LIMIT_PAIR = 5        # one account from one address
LIMIT_PER_ACCOUNT = 20  # one account from anywhere
LIMIT_PER_IP = 30     # one address against anything


class LoginThrottle(Base):
    __tablename__ = "login_throttle"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    fails: Mapped[int] = mapped_column(Integer, default=0)
    window_start: Mapped[dt.datetime] = mapped_column(DateTime)
    locked_until: Mapped[Optional[dt.datetime]] = mapped_column(DateTime, nullable=True)


def account_key(email: str) -> str:
    return "u:" + hashlib.sha256((email or "").strip().lower().encode()).hexdigest()[:40]


def ip_key(request) -> str:
    ip = request.client.host if request.client else "unknown"
    return "i:" + ip[:60]


def pair_key(email: str, request) -> str:
    ip = request.client.host if request.client else "unknown"
    return "p:" + hashlib.sha256(((email or "").strip().lower() + "|" + ip).encode()).hexdigest()[:40]


def login_keys(email: str, request):
    """(key, limit) for each counter that applies to a login attempt."""
    return [(pair_key(email, request), LIMIT_PAIR), (account_key(email), LIMIT_PER_ACCOUNT), (ip_key(request), LIMIT_PER_IP)]


def locked_for(db: Session, keys: Iterable[str]) -> int:
    """Seconds until the latest lock on any of the keys ends, or 0 when none is locked."""
    now = dt.datetime.utcnow()
    wait = 0
    for k in keys:
        row = db.get(LoginThrottle, k)
        if row and row.locked_until and row.locked_until > now:
            wait = max(wait, int((row.locked_until - now).total_seconds()) + 1)
    return wait


def record_failure(db: Session, counters) -> None:
    """counters: iterable of (key, limit). Counts a failure on each and locks the ones that reach their limit."""
    now = dt.datetime.utcnow()
    for k, limit in counters:
        if not k:
            continue
        row = db.get(LoginThrottle, k)
        if row is None:
            row = LoginThrottle(key=k, fails=0, window_start=now, locked_until=None)
            db.add(row)
        elif now - row.window_start > WINDOW:
            row.fails, row.window_start, row.locked_until = 0, now, None
        row.fails += 1
        if row.fails >= limit:
            row.locked_until = now + LOCK
    db.commit()


def clear(db: Session, key: str) -> None:
    row = db.get(LoginThrottle, key)
    if row:
        db.delete(row)
        db.commit()


def is_https(request) -> bool:
    """True when the browser reached us over https. X-Forwarded-Proto comes from nginx, but a client can send one
    too, so the production host name also counts: cookies for the real site are always Secure."""
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(":")[0].lower()
    if host == "app.oversightlab.ai" or host.endswith(".oversightlab.ai"):
        return True
    return request.headers.get("x-forwarded-proto", request.url.scheme) == "https"


# ── headers ─────────────────────────────────────────────────────────────────
CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.tailwindcss.com https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://cdn.sheetjs.com; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
    "font-src 'self' data: https://fonts.gstatic.com; "
    "img-src 'self' data: blob: https:; "
    "connect-src 'self' https:; "
    "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
)


def _h(scope, name: bytes) -> Optional[str]:
    for k, v in scope.get("headers", []):
        if k == name:
            return v.decode("latin-1")
    return None


class SecurityHeaders:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        https = (_h(scope, b"x-forwarded-proto") or scope.get("scheme", "")) == "https"

        async def wrapped(message):
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                have = {k.lower() for k, _ in headers}

                def add(name: bytes, value: str):
                    if name not in have:
                        headers.append((name, value.encode("latin-1")))

                add(b"x-content-type-options", "nosniff")
                # X-Frame-Options is set by nginx; frame-ancestors in the CSP below covers modern browsers.
                add(b"referrer-policy", "strict-origin-when-cross-origin")
                add(b"permissions-policy", "camera=(), microphone=(), geolocation=(), payment=()")
                add(b"content-security-policy", CSP)
                if https:
                    add(b"strict-transport-security", "max-age=31536000; includeSubDomains")
                if path.startswith(("/api/", "/auth/")):
                    add(b"cache-control", "no-store")
            await send(message)

        await self.app(scope, receive, wrapped)


# ── cross-origin writes ─────────────────────────────────────────────────────
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


def _allowed_hosts() -> set:
    hosts = {"app.oversightlab.ai"}
    try:
        h = urlparse(settings.PUBLIC_BASE_URL).netloc
        if h:
            hosts.add(h)
    except Exception:
        pass
    return hosts


class OriginCheck:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("method") in UNSAFE:
            origin = _h(scope, b"origin")
            if origin:
                host = _h(scope, b"x-forwarded-host") or _h(scope, b"host") or ""
                o = urlparse(origin).netloc
                if o and o != host and o not in _allowed_hosts():
                    return await _json(send, 403, {"detail": "Cross-origin request refused"})
        await self.app(scope, receive, send)


# ── upload limit ────────────────────────────────────────────────────────────
async def _json(send, status: int, body: dict):
    data = json.dumps(body).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(data)).encode())]})
    await send({"type": "http.response.body", "body": data})


class UploadLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") not in UNSAFE:
            return await self.app(scope, receive, send)
        limit = int(settings.max_upload_bytes) + 1024 * 1024  # multipart overhead allowance
        msg413 = {"detail": f"Upload too large. The limit is {settings.MAX_UPLOAD_MB} MB."}
        cl = _h(scope, b"content-length")
        if cl and cl.isdigit() and int(cl) > limit:
            return await _json(send, 413, msg413)
        state = {"seen": 0, "over": False, "sent": False}

        async def counting_receive():
            m = await receive()
            if m["type"] == "http.request":
                state["seen"] += len(m.get("body", b""))
                if state["seen"] > limit:
                    state["over"] = True
                    # Stop feeding the parser; whatever it answers is replaced by a 413 below.
                    return {"type": "http.request", "body": b"", "more_body": False}
            return m

        async def replacing_send(message):
            if not state["over"]:
                return await send(message)
            if state["sent"]:
                return
            state["sent"] = True
            await _json(send, 413, msg413)

        await self.app(scope, counting_receive, replacing_send)


def install_hardening(app) -> None:
    # The last middleware added is the outermost, so headers wrap every response including 403 and 413.
    app.add_middleware(UploadLimit)
    app.add_middleware(OriginCheck)
    app.add_middleware(SecurityHeaders)


# ── Upload validation shared by every upload route ────────────────────────
MAX_FILES = 20


def safe_filename(name) -> str:
    n = str(name or "").replace("\\", "/").replace("\x00", "").split("/")[-1].strip()
    return "untitled" if n in ("", ".", "..") else n[:120]


def check_uploads(*files) -> None:
    """Reject too many files or disallowed extensions (HTTPException 413 / 415)."""
    import os
    from fastapi import HTTPException
    from app.config import settings
    flat = []
    for f in files:
        if f is None:
            continue
        flat.extend(f if isinstance(f, (list, tuple)) else [f])
    if len(flat) > MAX_FILES:
        raise HTTPException(status_code=413, detail=f"Too many files (maximum {MAX_FILES})")
    ok = settings.allowed_upload_exts_set
    for f in flat:
        ext = os.path.splitext(getattr(f, "filename", "") or "")[1].lower()
        if ok and ext not in ok:
            raise HTTPException(status_code=415, detail=f"File type {ext or '(none)'} is not allowed")
