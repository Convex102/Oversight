# Integration Map

This repository contains the runnable Oversight / ASGL v13.5 platform.

## What is live at runtime
- **FastAPI app:** `app/main.py` (monolithic entrypoint)
- **Mounted routers:** additional routers under `app/api/*.py` are mounted by `app/main.py` at startup.
- **Celery tasks:** `app/tasks.py` (invoked by the API when jobs run)

## Artifacts / patch history
Conversation patch bundles and intermediate zip outputs are kept for traceability, but are **not part of the runtime tree**.

Location:
- `docs/artifacts/conversation_patches/`

If you are packaging the runtime for deployment, this folder can be excluded.
