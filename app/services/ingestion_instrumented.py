"""Instrumented ingestion wrapper (v2.1).

Drop-in helpers to wrap existing ingestion functions without refactoring.

Use:
    from app.services.ingestion_instrumented import ingest_workbook
    df = ingest_workbook(..., ingest_fn=existing_ingest_fn)
"""

from __future__ import annotations

from typing import Any, Optional, Dict, Callable, Tuple

from app.services.telemetry import track_ingestion_start, track_ingestion_complete, Timer
from app.logging import get_logger

log = get_logger(__name__)

def ingest_workbook(
    *,
    tenant_id: str,
    job_id: Optional[str],
    vertical: Optional[str],
    workbook: Optional[str],
    sheet: Optional[str],
    ingest_fn: Callable[..., Any],
    **kwargs,
) -> Any:
    """Wrap an ingestion function with telemetry.

    ingest_fn: callable that ingests and returns a dataframe-like object or (data, meta).
    We try to infer n_rows/n_cols if possible.
    """
    t = Timer()
    try:
        track_ingestion_start(tenant_id=tenant_id, job_id=job_id, vertical=vertical, workbook=workbook, sheet=sheet)
    except Exception:
        pass
    ok = True
    out = None
    try:
        out = ingest_fn(workbook=workbook, sheet=sheet, **kwargs)
    except TypeError:
        out = ingest_fn(workbook, sheet, **kwargs)
    except Exception as e:
        ok = False
        log.exception("ingestion_failed", error=str(e))
        raise
    finally:
        n_rows = None
        n_cols = None
        try:
            obj = out
            if isinstance(out, tuple) and len(out) >= 1:
                obj = out[0]
            # pandas-like
            n_rows = int(getattr(obj, "shape", [None, None])[0]) if getattr(obj, "shape", None) else None
            n_cols = int(getattr(obj, "shape", [None, None])[1]) if getattr(obj, "shape", None) else None
        except Exception:
            pass
        try:
            track_ingestion_complete(
                tenant_id=tenant_id,
                job_id=job_id,
                vertical=vertical,
                n_rows=n_rows,
                n_cols=n_cols,
                elapsed_ms=t.ms(),
                ok=ok,
            )
        except Exception:
            pass
    return out
