from fastapi import FastAPI, UploadFile, File, Body, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
import json, base64, os, tempfile, uuid
from asgl.utils.logging import setup_logging
from asgl.utils.errors import ASGLError, ValidationError, AuthError, NotFoundError
from asgl.security.auth import require_api_key
from asgl.schema.store import save_schema, list_schemas, approve_schema, get_default_schema
from asgl.template.registry import best_match, save_new_version, list_templates
from asgl.ingest.spreadsheet import extract_tables_xlsx
from asgl.audit.pack import write_audit_pack

log = setup_logging("asgl.api")
app = FastAPI(title="ASGL v13.4 Enterprise - Robust Template Learning + Schema Library + Spreadsheet Intelligence")

class SchemaUpload(BaseModel):
    tenant_id: str
    schema_json_b64: str
    schema_name: Optional[str]=None
    schema_version: Optional[str]=None

class SchemaApprove(BaseModel):
    tenant_id: str
    fingerprint: str
    make_default: bool = False

@app.exception_handler(ASGLError)
def asgl_error_handler(_, exc: ASGLError):
    code=400
    if isinstance(exc, AuthError): code=401
    if isinstance(exc, NotFoundError): code=404
    return JSONResponse(status_code=code, content={"error": str(exc)})

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/v1/schemas/upload")
def upload_schema(payload: SchemaUpload, _=Depends(require_api_key)):
    schema = json.loads(base64.b64decode(payload.schema_json_b64).decode("utf-8"))
    if payload.schema_name: schema["name"]=payload.schema_name
    if payload.schema_version: schema["version"]=payload.schema_version
    entry = save_schema(payload.tenant_id, schema, status="pending")
    return {"saved": entry}

@app.post("/v1/schemas/approve")
def approve(payload: SchemaApprove, _=Depends(require_api_key)):
    return {"approved": approve_schema(payload.tenant_id, payload.fingerprint, payload.make_default)}

@app.get("/v1/schemas")
def schemas(tenant_id: str, _=Depends(require_api_key)):
    return {"tenant_id": tenant_id, "schemas": list_schemas(tenant_id)}

@app.get("/v1/templates")
def templates(tenant_id: str, _=Depends(require_api_key)):
    return {"tenant_id": tenant_id, "templates": list_templates(tenant_id)}

@app.post("/v1/accountancy/review")
async def review(
    tenant_id: str = Body(...),
    doc_type: str = Body("working_papers"),
    schema_mode: str = Body("global"),
    file: UploadFile = File(...),
    _=Depends(require_api_key)
):
    job_id=str(uuid.uuid4())
    suffix=os.path.splitext(file.filename)[1].lower()
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path=tmp.name
    try:
        tables=extract_tables_xlsx(tmp_path) if suffix in [".xlsx",".xlsm"] else []
        headers=list(tables[0].dataframe.columns) if tables else []
        match_info=best_match(tenant_id, doc_type, headers) if headers else None
        template_action=None
        if match_info is None and headers:
            saved=save_new_version(tenant_id, doc_type, headers)
            template_action={"action":"learned_new","version":saved["version"],"fingerprint":saved["fingerprint"],"confidence":1.0}
        elif match_info:
            template_action={"action":"matched","version":match_info["match"]["version"],"fingerprint":match_info["match"]["fingerprint"],
                             "confidence":match_info["confidence"],"needs_confirmation":match_info["needs_confirmation"]}
            if match_info["needs_confirmation"]:
                # drift: store new version
                saved=save_new_version(tenant_id, doc_type, headers, base_version=match_info["match"]["version"])
                template_action["action"]="drift_new_version"
                template_action["new_version"]=saved["version"]
                template_action["new_fingerprint"]=saved["fingerprint"]

        schema=None
        if schema_mode=="firm":
            schema=get_default_schema(tenant_id)

        meta={
            "tenant_id":tenant_id,
            "job_id":job_id,
            "doc_type":doc_type,
            "schema_mode":schema_mode,
            "schema_loaded": bool(schema),
            "template": template_action,
            "tables_detected": len(tables),
            "tables": [{"sheet":t.sheet,"bbox":t.bbox,"score":t.score,"signals":t.signals} for t in tables],
        }
        audit_dir=write_audit_pack(tenant_id, job_id, meta)
        meta["audit_pack_path"]=str(audit_dir)
        return meta
    finally:
        try: os.unlink(tmp_path)
        except: pass
