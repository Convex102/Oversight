from __future__ import annotations
from typing import Dict, Any, List, Optional
from pathlib import Path
import os, json, time, uuid
from collections import defaultdict

BASE = Path(os.getenv("ASGL_DI_LEARNING_STORE","data/di_learning"))
BASE.mkdir(parents=True, exist_ok=True)

def _tenant_path(tenant_id:str)->Path:
    p = BASE / tenant_id
    p.mkdir(parents=True, exist_ok=True)
    return p

def record_signal(tenant_id:str, signal: Dict[str,Any]):
    p=_tenant_path(tenant_id)/"signals.jsonl"
    signal=dict(signal)
    signal.setdefault("ts", int(time.time()))
    signal.setdefault("id", str(uuid.uuid4()))
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(signal)+"\n")
    return signal

def load_signals(tenant_id:str, limit:int=2000)->List[Dict[str,Any]]:
    p=_tenant_path(tenant_id)/"signals.jsonl"
    if not p.exists(): return []
    lines=p.read_text(encoding="utf-8").splitlines()[-limit:]
    out=[]
    for ln in lines:
        try: out.append(json.loads(ln))
        except Exception: pass
    return out

def _priors_path(tenant_id:str)->Path:
    return _tenant_path(tenant_id)/"priors.json"

def load_priors(tenant_id:str)->Dict[str,Any]:
    p=_priors_path(tenant_id)
    if not p.exists():
        return {"field_bias": {}, "field_scale": {}, "updated_at": 0}
    return json.loads(p.read_text(encoding="utf-8"))

def save_priors(tenant_id:str, priors: Dict[str,Any]):
    priors=dict(priors); priors["updated_at"]=int(time.time())
    _priors_path(tenant_id).write_text(json.dumps(priors, indent=2), encoding="utf-8")
    return priors

def update_priors_from_resolution(tenant_id:str, field:str, was_correct: bool):
    pri=load_priors(tenant_id)
    bias=pri["field_bias"].get(field, 0.0)
    # simple online update: bias increases if correct, decreases if incorrect
    lr=float(os.getenv("ASGL_DI_LEARN_LR","0.05"))
    bias += lr*(1.0 if was_correct else -1.0)
    bias = max(-0.25, min(0.25, bias))
    pri["field_bias"][field]=bias
    return save_priors(tenant_id, pri)

def calibrate_confidence(tenant_id:str, field:str, base_conf: float)->float:
    pri=load_priors(tenant_id)
    b=float(pri["field_bias"].get(field, 0.0))
    conf = base_conf + b
    return max(0.05, min(0.98, conf))