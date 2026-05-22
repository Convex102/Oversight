from app.api.v1.routes import router
from app.api.v1.routes_addon import router as addon_router
from app.api.v1.compare_routes import router as compare_router
__all__ = ["router", "addon_router", "compare_router"]
