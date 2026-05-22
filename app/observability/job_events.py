from __future__ import annotations

import datetime as dt
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from app.config import settings


def _events_file(tenant_id: int, job_id: int) -> Path:
    # stored alongside other artifacts under /data
    root = Path(settings.LOCAL_STORAGE_PATH).resolve()
    p = root / "tenants" / str(tenant_id) / "jobs" / str(job_id) / "events.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def append_event(
    *,
    tenant_id: int,
    job_id: int,
    event: str,
    message: str = "",
    progress: Optional[float] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    rec = {
        "ts": dt.datetime.utcnow().isoformat() + "Z",
        "event": str(event),
        "message": str(message or ""),
        "progress": float(progress) if progress is not None else None,
        "meta": meta or {},
    }
    if not settings.ENABLE_JOB_EVENTS:
        return rec

    f = _events_file(tenant_id, job_id)
    with f.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def read_events(tenant_id: int, job_id: int, *, limit: int = 500) -> List[Dict[str, Any]]:
    f = _events_file(tenant_id, job_id)
    if not f.exists():
        return []
    rows: List[Dict[str, Any]] = []
    # tail read: if file is huge, only read last N lines
    try:
        lines = f.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    for line in lines[-limit:]:
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def _sse_pack(idx: int, rec: Dict[str, Any]) -> str:
    # SSE requires \n\n termination
    data = json.dumps(rec, ensure_ascii=False)
    return f"id: {idx}\nevent: job\ndata: {data}\n\n"


def stream_events(
    tenant_id: int,
    job_id: int,
    *,
    from_index: int = 0,
    heartbeat_seconds: float = 15.0,
    poll_seconds: float = None,
) -> Iterator[bytes]:
    """Tail the events.jsonl file and emit Server-Sent Events (SSE).

    - from_index: 0-based event index (line number)
    - heartbeat_seconds: keepalive comment interval
    """
    poll_seconds = float(poll_seconds if poll_seconds is not None else settings.JOB_EVENTS_POLL_SECONDS)

    last_heartbeat = time.time()
    sent = 0

    # If file doesn't exist yet, still emit heartbeats.
    while True:
        f = _events_file(tenant_id, job_id)
        if f.exists():
            try:
                lines = f.read_text(encoding="utf-8").splitlines()
            except Exception:
                lines = []
            start = max(int(from_index), 0)
            for i, line in enumerate(lines[start:], start=start):
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                yield _sse_pack(i, rec).encode("utf-8")
                sent += 1
            from_index = len(lines)

        # heartbeat
        now = time.time()
        if now - last_heartbeat >= heartbeat_seconds:
            yield b": keepalive\n\n"
            last_heartbeat = now

        time.sleep(poll_seconds)


def cleanup_old_events(*, older_than_days: int = None) -> Dict[str, Any]:
    """Best-effort cleanup of old event files under LOCAL_STORAGE_PATH.

    This is safe to run as a periodic job / cron.
    """
    root = Path(settings.LOCAL_STORAGE_PATH).resolve()
    base = root / "tenants"
    if not base.exists():
        return {"ok": True, "deleted": 0}

    days = int(older_than_days if older_than_days is not None else settings.JOB_EVENTS_RETENTION_DAYS)
    cutoff = dt.datetime.utcnow() - dt.timedelta(days=days)

    deleted = 0
    for p in base.rglob("events.jsonl"):
        try:
            mtime = dt.datetime.utcfromtimestamp(p.stat().st_mtime)
            if mtime < cutoff:
                p.unlink(missing_ok=True)
                deleted += 1
        except Exception:
            continue
    return {"ok": True, "deleted": deleted, "cutoff": cutoff.isoformat() + "Z"}
