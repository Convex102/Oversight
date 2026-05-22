from fastapi import FastAPI
from app.api.di_learning_api import router as di_learning_router

def apply_di_v5_wiring(app: FastAPI) -> FastAPI:
    app.include_router(di_learning_router)
    return app