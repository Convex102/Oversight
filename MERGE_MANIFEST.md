# ASGL v13.5 — Conversation Full Integrated Package

This package is a **single “everything-in-one” repo snapshot** created by merging:
1) the **ASGL v13.5 enterprise canonical DI v5 repo** (as the runnable base), and  
2) **all additional code/artifacts** generated throughout this conversation (patch files + zip patch packs).

## What’s inside

### Base (runnable)
- Baseline source: `ASGL_v13_5_enterprise_canonical_DI_v5_repo.zip`
- Includes the working FastAPI web app + Celery worker + ASGL v13.5 orchestration:
  - `app/main.py`, `app/tasks.py`, `app/asgl_engine/pipeline.py`
  - `asgl`, `asgl_a`, `asgl_c`, `asgl_di`, `asgla_compare` packages
  - `packs/` (schemas, mappings, regulations, providers, validation rules)
  - `excel_addin/` + `excel_vba/` (existing v13.5 runner assets)

### Conversation additions (included in-tree)
From the generated **.patch** files, all *new-file* sections were extracted into the repo:
- `app/services/*`  
  - Phase 1 semantic mapper scaffold
  - Vector store scaffold
  - Phase 2 column encoder / ranker scaffolds
  - Provenance helpers, ingestion/export instrumentation, template retrieval, etc.
- `app/flywheel/*`  
  - Fingerprinting, quiet labels, dataset/queue scaffolds, trainer/engine scaffolds
- `app/governance/*`  
  - Governance suite scaffolds (RBAC, approvals, drift, export gating, notifications, runtime)
- `app/packs/*`  
  - Accountancy regs library scaffolds (ACA-style library JSON + helpers)
  - Pensions vertical scaffolds (schemas / regs libraries / review workbench scaffolds)
- `app/api/*` and `app/web/ui_routes.py`  
  - API/UI scaffolds referenced by the patches (not all are wired into `app/main.py` by default)
- `scripts/*`  
  - Training/eval helper scripts scaffolds

### Zip patch packs imported (selectively)
- `asgl_excel_vba_excel_addin_enterprise_patch_v13_5_v3.zip`
  - Imported: `excel_vba/src/*`, `excel_addin/src/ui/App.tsx`
- `asgl_di_v5_patch_v13_5.zip`
  - Imported **excluding** `app/tasks.py` (to avoid overwriting the canonical runnable worker)
  - Imported: `app/di/*`, `app/api/di_*`

### Archive of original artifacts
All original patch + zip artifacts from this conversation are preserved under:
- `conversation_patches/`

## Important note about “integration”

Many conversation patches were produced as **git-diff style patches with corrupt hunk headers**
(line counts did not match), which makes them impossible to apply automatically in a deterministic way.

To ensure you still receive a usable integrated package:
- The **canonical v13.5 repo is kept runnable**.
- All *new modules/files* described by patches are present in-tree (so nothing is “lost”).
- Some wiring edits to existing files (e.g. `app/main.py`, `app/tasks.py`, pipeline hooks)
  are **not applied automatically** unless they were already part of the canonical base.

If you want, we can do a follow-up step to:
- decide the “source of truth” router layout (monolithic `app/main.py` vs `app/api/*` routers),
- and then **wire in** the new subsystems (telemetry v2.x, flywheel, governance UI routes)
with explicit, testable changes.

## Quick run (local)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# start web
uvicorn app.main:app --reload --port 8000

# start worker (optional)
celery -A app.worker.celery_app worker -l info
```

Then open: `http://localhost:8000/`

