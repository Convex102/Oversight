from __future__ import annotations
from typing import Dict, Any, Optional, List
from pathlib import Path
import os, json, time

BASE = Path(os.getenv("ASGL_DI_STORE","data/di_store"))
BASE.mkdir(parents=True, exist_ok=True)

def _job_dir(tenant_id:str, job_id:str)->Path:
    p = BASE / tenant_id / "jobs" / job_id
    p.mkdir(parents=True, exist_ok=True)
    return p

def save_factbundle(tenant_id:str, job_id:str, factbundle: Dict[str,Any])->Path:
    p=_job_dir(tenant_id, job_id) / "factbundle.json"
    p.write_text(json.dumps(factbundle, indent=2), encoding="utf-8")
    return p

def save_di_trace(tenant_id:str, job_id:str, di_out: Dict[str,Any])->Path:
    p=_job_dir(tenant_id, job_id) / "di_trace.json"
    p.write_text(json.dumps(di_out, indent=2), encoding="utf-8")
    return p

def load_factbundle(tenant_id:str, job_id:str)->Optional[Dict[str,Any]]:
    p=_job_dir(tenant_id, job_id) / "factbundle.json"
    if not p.exists(): return None
    return json.loads(p.read_text(encoding="utf-8"))

def list_jobs(tenant_id:str, limit:int=50)->List[str]:
    p = BASE / tenant_id / "jobs"
    if not p.exists(): return []
    jobs = sorted([d.name for d in p.iterdir() if d.is_dir()], reverse=True)
    return jobs[:limit]