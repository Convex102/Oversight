from __future__ import annotations

import datetime as dt
import json
import uuid
from typing import Any, Dict, Optional

from loguru import logger

from app.storage import storage


def _priors_key(tenant_id: int) -> str:
    return f"tenants/{tenant_id}/di/priors.json"


def _signal_key(tenant_id: int, ts: str) -> str:
    return f"tenants/{tenant_id}/di/signals/{ts}_{uuid.uuid4().hex}.json"


def default_priors() -> Dict[str, Any]:
    return {
        "version": 1,
        "updated_at": None,
        "field_bias": {},   # schema_key -> float bias (adds to confidence)
        "notes": "Lightweight active-learning priors. Updated via review queue resolutions.",
    }


def load_priors(tenant_id: int) -> Dict[str, Any]:
    key = _priors_key(int(tenant_id))
    try:
        raw = storage.get(key)
        pri = json.loads(raw.decode("utf-8"))
        if not isinstance(pri, dict):
            return default_priors()
        pri.setdefault("field_bias", {})
        pri.setdefault("version", 1)
        return pri
    except Exception:
        return default_priors()


def save_priors(tenant_id: int, priors: Dict[str, Any]) -> str:
    priors = dict(priors or {})
    priors.setdefault("field_bias", {})
    priors["updated_at"] = dt.datetime.utcnow().isoformat() + "Z"
    key = _priors_key(int(tenant_id))
    uri = storage.put(key, json.dumps(priors, ensure_ascii=False, indent=2).encode("utf-8"), "application/json")
    return uri


def record_signal(tenant_id: int, signal: Dict[str, Any]) -> str:
    ts = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    payload = dict(signal or {})
    payload.setdefault("ts", dt.datetime.utcnow().isoformat() + "Z")
    payload.setdefault("signal_id", uuid.uuid4().hex)
    key = _signal_key(int(tenant_id), ts)
    return storage.put(key, json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"), "application/json")


def apply_resolution(priors: Dict[str, Any], field: str, *, confirmed: bool, weight: float = 1.0) -> Dict[str, Any]:
    """Update priors based on a human resolution.

    confirmed=True means the proposed extraction/mapping was correct (increase bias).
    confirmed=False means it was wrong (decrease bias).
    """
    priors = dict(priors or {})
    fb = dict(priors.get("field_bias") or {})
    k = str(field or "").strip()
    if not k:
        return priors

    # bounded bias per field
    cur = float(fb.get(k, 0.0) or 0.0)
    step = 0.015 * max(0.25, min(3.0, float(weight)))

    if confirmed:
        cur = min(0.12, cur + step)
    else:
        cur = max(-0.15, cur - step)

    fb[k] = round(cur, 5)
    priors["field_bias"] = fb
    priors["updated_at"] = dt.datetime.utcnow().isoformat() + "Z"
    return priors
