import json, hashlib
from datetime import datetime
from pathlib import Path
from asgl.config import OUTPUT_ROOT

def _hash_file(p:Path)->str:
    h=hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()

def write_audit_pack(tenant_id:str, job_id:str, meta:dict)->Path:
    out_dir=OUTPUT_ROOT/tenant_id/"audit_packs"/job_id
    out_dir.mkdir(parents=True, exist_ok=True)
    meta["generated_ts"]=datetime.utcnow().isoformat()+"Z"
    (out_dir/"meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    # add hashes
    hashes={}
    for f in out_dir.glob("*"):
        if f.is_file():
            hashes[f.name]=_hash_file(f)
    (out_dir/"hashes.json").write_text(json.dumps(hashes, indent=2), encoding="utf-8")
    return out_dir
