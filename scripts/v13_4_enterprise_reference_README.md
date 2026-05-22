# ASGL v13.4 Enterprise (Full Integrated)

This build includes:
- **Schema library** with **approval workflow** and **default selection** per tenant
- **Template learning** with **matching**, **confidence scoring**, and **drift versioning**
- **Spreadsheet intelligence** with **multi-block extraction** and **table scoring**
- **Audit packs** written per job (`meta.json` + `hashes.json`)
- **API key auth** (`X-API-Key` header)
- **Structured logging** (JSON logs when `ASGL_DEBUG=1`)

## Run
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn main:app --host 0.0.0.0 --port 8000
```

## Auth
Set `ASGL_API_KEYS` (comma-separated) in `.env`. Send `X-API-Key` header.

## Key Endpoints
- `GET /health`
- `POST /v1/schemas/upload`
- `POST /v1/schemas/approve`
- `GET /v1/schemas?tenant_id=...`
- `GET /v1/templates?tenant_id=...`
- `POST /v1/accountancy/review` (multipart file)

## Output layout
`ASGL_OUTPUT_ROOT/<tenant_id>/`
- `schemas/` (registry + approved/pending schemas)
- `templates/` (template versions + registry)
- `audit_packs/<job_id>/` (meta + hashes)
