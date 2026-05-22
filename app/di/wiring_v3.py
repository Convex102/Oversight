from __future__ import annotations
from typing import Dict, Any
from app.di.job_events import emit_event
from app.di.orchestrator_v3 import run_di_v3

def di_progress_cb_factory(tenant_id:str, job_id:str):
    def _cb(stage:str, progress:float, message:str="", level:str="info", **kw):
        emit_event(tenant_id, job_id, stage=stage, progress=progress, message=message, level=level, meta=kw)
    return _cb

def run_di_v3_wired(ctx: Dict[str,Any])->Dict[str,Any]:
    cb = di_progress_cb_factory(ctx.get("tenant_id",""), ctx.get("job_id",""))
    emit_event(ctx.get("tenant_id",""), ctx.get("job_id",""), "di.start", 0.0, "DI v3 started")
    out = run_di_v3(ctx, progress_cb=cb)
    emit_event(ctx.get("tenant_id",""), ctx.get("job_id",""), "di.end", 1.0, "DI v3 finished", meta={"ok": out.get("ok")})
    return out