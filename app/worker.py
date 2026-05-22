from celery import Celery
from app.config import settings
import os

# Use SQLite as broker/backend if Redis is not available (dev mode)
# This requires no external services - works out of the box on Windows
_data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
os.makedirs(_data_dir, exist_ok=True)

_broker = f"sqla+sqlite:///{_data_dir}/celery_broker.db"
_backend = f"db+sqlite:///{_data_dir}/celery_results.db"

# Use Redis if available, fall back to SQLite
try:
    import redis as _redis
    _r = _redis.StrictRedis.from_url(settings.REDIS_URL, socket_connect_timeout=1)
    _r.ping()
    _broker = settings.REDIS_URL
    _backend = settings.REDIS_URL
except Exception:
    pass  # Redis not available - using SQLite broker

celery_app = Celery("asgl", broker=_broker, backend=_backend)

celery_app.conf.task_routes = {"app.tasks.*": {"queue": "asgl"}}
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_always_eager=False,
    broker_connection_retry_on_startup=True,
)
