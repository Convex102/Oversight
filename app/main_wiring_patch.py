"""
Drop-in wiring patch.

If your repo already has `app/main.py` or `app/app.py`, copy the `apply_wiring(app)` call
into your factory AFTER existing routers are included.
"""

from fastapi import FastAPI
from app.api.billing_api import router as billing_router
from app.api.schema_registry_api import router as schema_router

def apply_wiring(app: FastAPI) -> FastAPI:
    app.include_router(billing_router)
    app.include_router(schema_router)
    return app