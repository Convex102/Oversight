"""
Oversight ASGL – Application Configuration
==========================================
Loads environment variables from .env before Pydantic Settings
validation so the process works whether the app is launched via
uvicorn directly, via the LAUNCH.ps1 script, or from a container.

Errors are logged clearly at startup rather than crashing silently.
"""
from __future__ import annotations

# ── Must come before ANY pydantic import so env vars are in os.environ ──
import os
import sys
import logging
from pathlib import Path

from dotenv import load_dotenv

# Resolve .env relative to THIS file (app/config.py → repo root/.env)
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
if _ENV_PATH.exists():
    load_dotenv(dotenv_path=_ENV_PATH, override=True)
else:
    # Fallback: try cwd
    load_dotenv(override=True)

# ── Pydantic imports AFTER load_dotenv ──────────────────────────────────
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings
from typing import Optional, Set

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    # ------------------------------------------------------------------ #
    # App                                                                  #
    # ------------------------------------------------------------------ #
    APP_NAME: str = "ASGL-A Cloud"
    ENV: str = "dev"

    SECRET_KEY: str = Field(
        default="change_me_in_production",
        env="SECRET_KEY",
        description="JWT / session signing secret. MUST be overridden in production.",
    )

    PUBLIC_BASE_URL: str = "http://localhost:8000"
    FRONTEND_URL: str = "http://localhost:8000"

    # ------------------------------------------------------------------ #
    # Data stores                                                          #
    # ------------------------------------------------------------------ #
    DATABASE_URL: str = Field(
        default="sqlite:///./data/asgl_local.db",
        env="DATABASE_URL",
        description="SQLAlchemy database URL.",
    )

    REDIS_URL: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL.",
    )

    # ------------------------------------------------------------------ #
    # Billing (Stripe)                                                     #
    # ------------------------------------------------------------------ #
    STRIPE_SECRET_KEY: str = Field(
        default="sk_test_disabled_local",
        description="Stripe secret key – use live key in production.",
    )
    STRIPE_WEBHOOK_SECRET: str = Field(
        default="whsec_disabled_local",
        description="Stripe webhook signing secret.",
    )
    STRIPE_PRICE_BASIC: Optional[str] = None
    STRIPE_PRICE_PRO: Optional[str] = None

    # ------------------------------------------------------------------ #
    # Storage                                                              #
    # ------------------------------------------------------------------ #
    STORAGE_BACKEND: str = Field(
        default="local",
        description="Storage backend: 'local' or 's3'.",
    )
    LOCAL_STORAGE_PATH: str = Field(
        default="./data",
        description="Absolute or relative path for local file storage.",
    )

    # S3 (only required when STORAGE_BACKEND=s3)
    S3_BUCKET: Optional[str] = None
    AWS_REGION: Optional[str] = None

    # Upload hardening
    MAX_UPLOAD_MB: int = Field(default=75, ge=1, le=500)
    ALLOWED_UPLOAD_EXTS: str = ".xlsx,.xlsm,.xls,.pdf,.csv,.zip"

    # ------------------------------------------------------------------ #
    # Usage & retention                                                    #
    # ------------------------------------------------------------------ #
    DEFAULT_RETENTION_DAYS: int = Field(default=30, ge=1)
    BASIC_MONTHLY_PACK_LIMIT: int = Field(default=20, ge=0)
    PRO_MONTHLY_PACK_LIMIT: int = Field(default=200, ge=0)
    OVERAGE_PRICE_PER_PACK_GBP: int = Field(default=25, ge=0)

    # ------------------------------------------------------------------ #
    # Observability / events                                               #
    # ------------------------------------------------------------------ #
    ENABLE_JOB_EVENTS: bool = True
    JOB_EVENTS_RETENTION_DAYS: int = Field(default=14, ge=1)
    JOB_EVENTS_POLL_SECONDS: float = Field(default=0.75, ge=0.1)

    # ------------------------------------------------------------------ #
    # LLM / Phase flags                                                    #
    # ------------------------------------------------------------------ #
    ASGL_ENABLE_LLM: int = Field(default=0, description="Set to 1 to enable LLM routes.")
    ASGL_PHASE2_ENABLE: int = Field(default=1, description="Set to 1 to enable Phase 2 features.")

    # ------------------------------------------------------------------ #
    # Auth compatibility                                                   #
    # ------------------------------------------------------------------ #
    AUTH_ACCEPT_X_API_KEY: bool = True
    AUTH_ACCEPT_BEARER: bool = True  # Authorization: Bearer <api_key>

    # ------------------------------------------------------------------ #
    # Pydantic v2 config                                                   #
    # ------------------------------------------------------------------ #
    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
        "case_sensitive": True,
    }

    # ------------------------------------------------------------------ #
    # Validators                                                           #
    # ------------------------------------------------------------------ #
    @field_validator("SECRET_KEY")
    @classmethod
    def secret_key_must_not_be_default_in_prod(cls, v: str) -> str:
        env = os.getenv("ENV", "dev")
        if env == "prod" and v in ("change_me", "change_me_in_production"):
            raise ValueError(
                "SECRET_KEY must be a secure random value in production. "
                "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(48))\""
            )
        return v

    @field_validator("STORAGE_BACKEND")
    @classmethod
    def validate_storage_backend(cls, v: str) -> str:
        allowed = {"local", "s3"}
        if v.lower() not in allowed:
            raise ValueError(f"STORAGE_BACKEND must be one of {allowed}, got '{v}'")
        return v.lower()

    @model_validator(mode="after")
    def warn_if_s3_fields_missing(self) -> "Settings":
        if self.STORAGE_BACKEND == "s3":
            missing = [f for f in ("S3_BUCKET", "AWS_REGION") if not getattr(self, f)]
            if missing:
                logger.warning(
                    "STORAGE_BACKEND=s3 but these fields are not set: %s", missing
                )
        return self

    # ------------------------------------------------------------------ #
    # Computed helpers                                                     #
    # ------------------------------------------------------------------ #
    @property
    def max_upload_bytes(self) -> int:
        return int(self.MAX_UPLOAD_MB) * 1024 * 1024

    @property
    def allowed_upload_exts_set(self) -> Set[str]:
        return {
            e.strip().lower()
            for e in (self.ALLOWED_UPLOAD_EXTS or "").split(",")
            if e.strip()
        }

    @property
    def is_production(self) -> bool:
        return self.ENV.lower() == "prod"

    @property
    def llm_enabled(self) -> bool:
        return bool(self.ASGL_ENABLE_LLM)

    @property
    def phase2_enabled(self) -> bool:
        return bool(self.ASGL_PHASE2_ENABLE)


# ── Instantiate once; import `settings` from here everywhere else ────────
try:
    settings = Settings()
except Exception as exc:
    logger.critical(
        "\n\n  OVERSIGHT CONFIG ERROR\n"
        "  %s\n"
        "  Check your .env file at: %s\n",
        str(exc),
        _ENV_PATH,
    )
    sys.exit(1)
