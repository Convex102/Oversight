import json, hashlib
from pathlib import Path
from datetime import datetime
from jsonschema import Draft202012Validator
from asgl.utils.logging import setup_logging
from asgl.config import OUTPUT_ROOT
from asgl.utils.errors import ValidationError, NotFoundError

log = setup_logging("asgl.schema")

MIN_SCHEMA = {
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["name", "version", "fields"],
  "properties": {
    "name": {"type": "string"},
    "version": {"type": "string"},
    "description": {"type": "string"},
    "fields": {
      "type": "array",
      "minItems": 1,
      "items": {
        "type": "object",
        "required": ["key", "type"],
        "properties": {
          "key": {"type": "string"},
          "type": {"type": "string", "enum": ["number","string","date","boolean"]},
          "description": {"type": "string"},
          "required": {"type": "boolean"},
          "aliases": {"type": "array", "items":{"type":"string"}}
        }
      }
    }
  }
}
_validator = Draft202012Validator(MIN_SCHEMA)

def _tenant_dir(tenant_id:str)->Path:
    d = OUTPUT_ROOT / tenant_id / "schemas"
    d.mkdir(parents=True, exist_ok=True)
    return d

def _registry_path(tenant_id:str)->Path:
    return _tenant_dir(tenant_id) / "schemas_registry.jsonl"

def fingerprint(schema:dict)->str:
    b=json.dumps(schema, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(b).hexdigest()

def validate_schema(schema:dict):
    errs=sorted(_validator.iter_errors(schema), key=lambda e: e.path)
    if errs:
        msg="; ".join([f"{list(e.path)}: {e.message}" for e in errs[:10]])
        raise ValidationError(f"Invalid schema: {msg}")

def save_schema(tenant_id:str, schema:dict, status:str="pending")->dict:
    validate_schema(schema)
    fp=fingerprint(schema)
    now=datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    safe="".join([c if c.isalnum() or c in '-_.' else '_' for c in schema['name']])
    path=_tenant_dir(tenant_id)/f"{safe}_{schema['version']}_{now}.json"
    path.write_text(json.dumps(schema, indent=2, ensure_ascii=False), encoding="utf-8")
    entry={"ts":now,"schema_path":str(path),"fingerprint":fp,"name":schema["name"],"version":schema["version"],
           "status":status,"approved_ts":None,"is_default":False}
    with _registry_path(tenant_id).open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry)+"\n")
    log.info("schema_saved", extra={"extra":entry})
    return entry

def list_schemas(tenant_id:str)->list:
    reg=_registry_path(tenant_id)
    if not reg.exists(): return []
    rows=[]
    for line in reg.read_text(encoding="utf-8").splitlines():
        try: rows.append(json.loads(line))
        except: pass
    return rows

def _rewrite_registry(tenant_id:str, rows:list):
    reg=_registry_path(tenant_id)
    reg.write_text("\n".join([json.dumps(r) for r in rows])+"\n", encoding="utf-8")

def approve_schema(tenant_id:str, fingerprint_value:str, make_default:bool=False)->dict:
    rows=list_schemas(tenant_id)
    found=None
    for r in rows:
        if r.get("fingerprint")==fingerprint_value:
            r["status"]="approved"
            r["approved_ts"]=datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
            found=r
    if not found:
        raise NotFoundError("Schema not found")
    if make_default:
        for r in rows:
            r["is_default"]= (r.get("fingerprint")==fingerprint_value)
    _rewrite_registry(tenant_id, rows)
    log.info("schema_approved", extra={"extra":{"tenant_id":tenant_id,"fingerprint":fingerprint_value,"default":make_default}})
    return found

def get_default_schema(tenant_id:str)->dict|None:
    rows=list_schemas(tenant_id)
    for r in reversed(rows):
        if r.get("is_default") and Path(r["schema_path"]).exists():
            return json.loads(Path(r["schema_path"]).read_text(encoding="utf-8"))
    return None
