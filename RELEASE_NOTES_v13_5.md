# ASGL v13.5 — Fully Merged Enterprise Build

This repository is a **fully merged** build that integrates all modules delivered across the v13.x packages and the cloud repo.

## What is included

### Cloud / SaaS app
- FastAPI web app (marketing site + console + API)
- Multi-tenant API key auth (X-API-Key)
- Uploads, templates, jobs, artifacts storage
- Celery worker pipeline (jobs executed async)
- Stripe checkout + billing portal + webhook handlers (feature-gated by .env)

### Engines and packages
- `asgl` (core): ingest, schema store, audit pack, security, validation
- `asgl_a` (ASGL‑A): analysis engine, template learning, explainability + popup runner modules
- `asgl_c` (ASGL‑C): semantic compilation + explainability registry + governance hooks
- `asgl_di` + `asgl_v13`: Decision Intelligence + v13 hub aggregation
- `asgla_compare`: Excel↔PDF compare engine + report generation utilities

### Data packs
- `packs/schemas` — enterprise schema pack
- `packs/validation_rules` — rules_v1 validation pack
- `packs/providers`, `packs/mappings`, `packs/regulations`, `packs/versioning` — reference provider data

### Tools
- `excel_vba/` + `excel_addin/` (macro runner foundations)
- `tools/local_template_picker/` (desktop picker scaffold)
- `tools/optionA_excel_runner/` (Option A local UI runner) — optional dependency set

## What was wired together
- `app/asgl_engine/pipeline.py` orchestrates:
  1) advanced spreadsheet ingestion
  2) ASGL‑A analysis
  3) validation pack execution
  4) ASGL‑C semantic features (when available)
  5) DI + v13 hub summarization
  6) compare engine (Excel vs PDF) when both inputs provided
  7) explainability driver summary
  8) PDF report assembly (`build_pdf`)

- `app/tasks.py` stores:
  - report PDF
  - result JSON
  - optional compare PDF + CSV (when produced)

- Template picker:
  - `/template-picker` (page)
  - `/api/templates/upload`, `/api/templates/sheet`, `/api/templates/save` (API key protected)
  - Saved templates are persisted to the DB and usable by jobs.

## Dependency sets
- `requirements.txt` — cloud runtime deps
- `requirements-ml.txt` — optional ML/SHAP explainability deps
- `requirements-desktop.txt` — optional desktop UI deps

## Running (local)
1. `cp .env.example .env`
2. `docker compose up --build`
3. Open:
   - Marketing: `http://localhost:8000/`
   - Console: `http://localhost:8000/console`
   - Template picker: `http://localhost:8000/template-picker`

