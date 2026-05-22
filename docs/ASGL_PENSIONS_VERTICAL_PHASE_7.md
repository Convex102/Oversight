# ASGL-P Phase 7 — Template DB, Drift Detection, API Runner (State-of-the-Art)

Phase 7 makes ASGL-P **enterprise-grade**:

## 1) DB-backed Template Store
- New module: `app/packs/pensions/template_store_db.py`
- Uses SQLite by default (no infra)
- Env override:
  - `ASGL_TEMPLATE_DB=/path/to/db.sqlite`

Features:
- upsert fingerprints
- list templates by tenant
- best-match retrieval

## 2) Deterministic Drift Detection
- New module: `app/packs/pensions/drift.py`
- Compares current vs previously matched fingerprint:
  - column count delta
  - header additions/removals
  - overlap ratio
- Emits `pack.pensions.drift.detected` when drift present

## 3) API Runner for UI
  - `POST /api/v1/pensions/run` (run pack end-to-end)
  - `GET  /api/v1/pensions/templates?tenant_id=...` (list template library)

## 4) Robustness + Observability
- structured logging
- `pack.run.error` telemetry for internal stages
- `api.pensions.run.error` telemetry for API failures

## Recommended Phase 8
- wire drift + findings into reviewer UI
- add PDF rendering for trustee evidence bundles
- add per-template policy (autopilot threshold / lock template / require approval)

