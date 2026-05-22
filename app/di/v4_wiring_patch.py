from fastapi import FastAPI
from app.api.di_admin_api import router as di_admin_router

def apply_di_v4_wiring(app: FastAPI) -> FastAPI:
    app.include_router(di_admin_router)
    return app