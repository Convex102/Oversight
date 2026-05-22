"""Instrumented export wrapper (v2.1)."""

from __future__ import annotations

from typing import Any, Optional, Callable

from app.services.telemetry import track_export_completed, Timer
from app.logging import get_logger

log = get_logger(__name__)

def export_artifact(
    *,
    tenant_id: str,
    job_id: Optional[str],
    vertical: Optional[str],
    artifact: Optional[str],
    export_fn: Callable[..., Any],
    **kwargs,
) -> Any:
    t = Timer()
    ok = True
    out = None
    try:
        out = export_fn(artifact=artifact, **kwargs)
    except TypeError:
        out = export_fn(artifact, **kwargs)
    except Exception as e:
        ok = False
        log.exception("export_failed", error=str(e))
        raise
    finally:
        try:
            track_export_completed(
                tenant_id=tenant_id,
                job_id=job_id,
                vertical=vertical,
                artifact=artifact,
                elapsed_ms=t.ms(),
            )
        except Exception:
            pass
    return out
