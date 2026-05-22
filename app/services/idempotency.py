from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Optional

from app.config import settings


def _safe_key(k: str) -> str:
    k = (k or "").strip()
    if not k:
        return ""
    # limit to filesystem-safe subset
    k = re.sub(r"[^a-zA-Z0-9._-]", "_", k)[:180]
    return k


def _path(tenant_id: int, key: str) -> Path:
    root = Path(settings.LOCAL_STORAGE_PATH).resolve()
    p = root / "tenants" / str(tenant_id) / "idempotency"
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{_safe_key(key)}.json"


def get_existing_job_id(tenant_id: int, key: str, *, ttl_seconds: int = 24 * 3600) -> Optional[int]:
    key = _safe_key(key)
    if not key:
        return None
    p = _path(tenant_id, key)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        ts = dt.datetime.fromisoformat((data.get("ts") or "").replace("Z", ""))
        if (dt.datetime.utcnow() - ts).total_seconds() > ttl_seconds:
            p.unlink(missing_ok=True)
            return None
        job_id = data.get("job_id")
        return int(job_id) if job_id is not None else None
    except Exception:
        return None


def store_job_id(tenant_id: int, key: str, job_id: int) -> None:
    key = _safe_key(key)
    if not key:
        return
    p = _path(tenant_id, key)
    payload = {"ts": dt.datetime.utcnow().isoformat() + "Z", "job_id": int(job_id)}
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
