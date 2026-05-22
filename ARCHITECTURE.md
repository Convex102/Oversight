# Oversight / ASGL v13.5 Architecture

## Overview
Oversight / ASGL is a FastAPI service that provides:
- public + console UI (Jinja templates)
- tenant/user authentication and billing hooks
- APIs for running packs and DI workflows
- job execution with event streaming

## Runtime entrypoints
- **FastAPI**: `app/main.py` (creates the app, mounts static files/templates, and wires routers)
- **Jobs**: `app/tasks.py` (job runner used by the API)

## Routers
Subsystem APIs and UIs live under `app/api/*.py` as `router = APIRouter(...)`.
These are mounted in `app/main.py` via defensive imports so the runtime surface matches the codebase.

## Artifacts
Conversation patch bundles and intermediate zips are stored under `docs/artifacts/` for traceability,
kept out of the runtime tree to reduce confusion and production risk.

## Testing and CI
- Tests are under `tests/`.
- GitHub Actions CI runs lint + type checks + unit tests.
