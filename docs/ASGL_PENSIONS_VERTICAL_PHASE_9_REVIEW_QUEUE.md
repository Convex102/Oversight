# ASGL-P Phase 9 — Persisted Runs + Real Review Queue (UI Workbench)

This phase upgrades Phase 8 UI from scaffold to real persisted workflow.

## Pack Run Store (SQLite)
New module: `app/packs/pensions/run_store.py`

Tables:
- `pack_runs`: canonical snapshot + findings + evidence bundle
- `review_queue`: one row per finding (OPEN/RESOLVED/REJECTED)

Env:
- `ASGL_RUN_DB=asgl_runs.sqlite`

## Pipeline wiring
`run_pensions_pack()` now:
- generates `run_id`
- saves run record
- enqueues findings into queue
- emits telemetry: `pack.pensions.review.enqueued`

## UI workbench
`/ui/pensions/review` lists queue items.

New routes:
- `/ui/pensions/review/item`
- `/ui/pensions/review/item/action`

Telemetry:
- `pack.pensions.review.item.updated`

## API additions
- `GET /api/v1/pensions/queue?tenant_id=...`

