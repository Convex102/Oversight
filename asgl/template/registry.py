import json, hashlib
from pathlib import Path
from datetime import datetime
from asgl.config import OUTPUT_ROOT
from asgl.utils.logging import setup_logging
from rapidfuzz.distance import Jaccard

log = setup_logging("asgl.template")

def _dir(tenant_id:str)->Path:
    d=OUTPUT_ROOT/tenant_id/"templates"
    d.mkdir(parents=True, exist_ok=True)
    return d

def _reg(tenant_id:str)->Path:
    return _dir(tenant_id)/"templates_registry.jsonl"

def _normalize(headers:list)->list:
    return [str(h).strip().lower() for h in headers if h is not None and str(h).strip()]

def fingerprint(headers:list, doc_type:str, version:int)->str:
    payload={"doc_type":doc_type,"version":version,"headers":_normalize(headers)}
    b=json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(b).hexdigest()

def similarity(a_headers:list, b_headers:list)->float:
    a=set(_normalize(a_headers)); b=set(_normalize(b_headers))
    if not a or not b: return 0.0
    return 1.0 - Jaccard.normalized_distance(a, b)

def list_templates(tenant_id:str)->list:
    if not _reg(tenant_id).exists(): return []
    rows=[]
    for line in _reg(tenant_id).read_text(encoding="utf-8").splitlines():
        try: rows.append(json.loads(line))
        except: pass
    return rows

def best_match(tenant_id:str, doc_type:str, headers:list, threshold:float=0.82)->dict|None:
    candidates=[r for r in list_templates(tenant_id) if r.get("doc_type")==doc_type]
    best=None; best_s=0.0
    for r in candidates:
        s=similarity(headers, r.get("headers",[]))
        if s>best_s:
            best_s=s; best=r
    if best and best_s>=threshold:
        return {"match":best, "confidence":best_s, "needs_confirmation": best_s<0.90}
    return None

def save_new_version(tenant_id:str, doc_type:str, headers:list, base_version:int=1)->dict:
    existing=[r for r in list_templates(tenant_id) if r.get("doc_type")==doc_type]
    version=max([r.get("version",1) for r in existing] + [base_version])
    # if drift, increment version
    version = version + 1 if existing else version
    fp=fingerprint(headers, doc_type, version)
    now=datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    path=_dir(tenant_id)/f"{doc_type}_v{version}_{fp}_{now}.json"
    payload={"tenant_id":tenant_id,"doc_type":doc_type,"version":version,"headers":headers,"fingerprint":fp,"created":now}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    rec={"ts":now,"doc_type":doc_type,"version":version,"fingerprint":fp,"path":str(path),"headers":headers}
    with _reg(tenant_id).open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec)+"\n")
    log.info("template_saved", extra={"extra":{"tenant_id":tenant_id,"doc_type":doc_type,"version":version,"fingerprint":fp}})
    return rec
