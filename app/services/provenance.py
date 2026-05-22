"""Enterprise audit-grade provenance utilities (Telemetry v2.3)."""

from __future__ import annotations

from typing import Optional, Dict, Any, List

from app.services.telemetry import (
    Timer,
    new_trace_id,
    file_fingerprint,
    track_stage_start,
    track_stage_complete,
    track_error,
)

class StageTrace:
    def __init__(self, *, tenant_id: str, job_id: Optional[str], vertical: Optional[str], stage: str, trace_id: str, meta: Optional[Dict[str, Any]] = None):
        self.tenant_id = tenant_id
        self.job_id = job_id
        self.vertical = vertical
        self.stage = stage
        self.trace_id = trace_id
        self.meta = meta or {}
        self.t = Timer()
        track_stage_start(tenant_id=tenant_id, job_id=job_id, vertical=vertical, stage=stage, trace_id=trace_id, meta=self.meta)

    def complete(self, ok: bool = True, meta: Optional[Dict[str, Any]] = None):
        track_stage_complete(
            tenant_id=self.tenant_id,
            job_id=self.job_id,
            vertical=self.vertical,
            stage=self.stage,
            trace_id=self.trace_id,
            elapsed_ms=self.t.ms(),
            ok=ok,
            meta={**self.meta, **(meta or {})},
        )

def fingerprint_inputs(paths: List[str]) -> List[str]:
    return [file_fingerprint(p) for p in paths if p]
