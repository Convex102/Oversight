from __future__ import annotations
from typing import Dict, Any, Optional
import os, json, time
from pathlib import Path

def events_path(tenant_id:str, job_id:str)->Path:
    base = Path(os.getenv("ASGL_EVENT_DIR","data/tenants")) / tenant_id / "jobs" / job_id
    base.mkdir(parents=True, exist_ok=True)
    return base / "events.jsonl"

def emit_event(tenant_id:str, job_id:str, stage:str, progress:float, message:str="", level:str="info", meta:Optional[Dict[str,Any]]=None):
    rec={"ts":int(time.time()),"stage":stage,"progress":progress,"message":message,"level":level,"meta":meta or {}}
    p=events_path(tenant_id, job_id)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec)+"\n")
    return rec