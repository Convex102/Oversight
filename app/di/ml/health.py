from __future__ import annotations

import time
from typing import Any, Dict, Optional

from .config import EMBED
from .logging import get_logger

log = get_logger("asgl.di.ml.health")


_STATUS: Dict[str, Any] = {
    "enabled": EMBED.enabled,
    "model": EMBED.model_name,
    "loaded": False,
    "device": EMBED.device,
    "last_error": None,
    "warmup_ms": None,
}


def set_loaded(ok: bool, *, warmup_ms: Optional[float] = None) -> None:
    _STATUS["loaded"] = bool(ok)
    if warmup_ms is not None:
        _STATUS["warmup_ms"] = float(warmup_ms)


def set_error(err: str) -> None:
    _STATUS["last_error"] = err[:4000]
    _STATUS["loaded"] = False


def get_status() -> Dict[str, Any]:
    return dict(_STATUS)


def warmup(embed_fn) -> None:
    if not EMBED.enabled or not EMBED.warmup:
        return
    try:
        t0 = time.time()
        _ = embed_fn("ASGL warmup", tenant_id="global", cache=False)
        dt = (time.time() - t0) * 1000.0
        set_loaded(True, warmup_ms=dt)
        log.info("DI ML warmup OK (%.1fms)", dt)
    except Exception as e:
        set_error(str(e))
        log.exception("DI ML warmup failed")
