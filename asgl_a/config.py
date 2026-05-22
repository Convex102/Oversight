from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


@dataclass
class ASGLAConfig:
    output_root: Path
    domain: str = "accountancy"

    # Feature toggles
    enable_llm: bool = True
    enable_ml: bool = True
    enable_shap: bool = True
    enable_lime: bool = True

    # Logging / LLM
    log_level: str = "INFO"
    llm_model: str = "gpt-4.1-mini"
    llm_temperature: float = 0.1

    # Rolling windows used by KPI/flags where relevant
    rolling_windows: tuple = (3, 6, 12)

    # ---- Template learning (FIRST-CLASS) ----
    enable_template_learning: bool = True
    template_registry_path: Path | None = None

    # Matching + drift thresholds
    template_match_min_similarity: float = 0.86   # 0-1, higher = stricter
    template_drift_new_version_threshold: float = 0.18  # 0-1, higher = more tolerant of drift
    template_require_confirmation_below: float = 0.78  # if match similarity below this, require user confirmation

    def ensure_dirs(self) -> None:
        (self.output_root / "logs").mkdir(parents=True, exist_ok=True)
        (self.output_root / "debug").mkdir(parents=True, exist_ok=True)
        (self.output_root / "templates").mkdir(parents=True, exist_ok=True)
        if self.template_registry_path is None:
            self.template_registry_path = self.output_root / "templates" / "registry.jsonl"

    @staticmethod
    def from_env(output_root: Path) -> "ASGLAConfig":
        def b(name: str, default: str = "true") -> bool:
            return os.getenv(name, default).lower() in ("1", "true", "yes", "y", "on")

        cfg = ASGLAConfig(
            output_root=output_root,
            enable_llm=b("ASGLA_ENABLE_LLM", "true"),
            enable_ml=b("ASGLA_ENABLE_ML", "true"),
            enable_shap=b("ASGLA_ENABLE_SHAP", "true"),
            enable_lime=b("ASGLA_ENABLE_LIME", "true"),
            log_level=os.getenv("ASGLA_LOG_LEVEL", "INFO"),
            llm_model=os.getenv("ASGLA_LLM_MODEL", "gpt-4.1-mini"),
            llm_temperature=float(os.getenv("ASGLA_LLM_TEMPERATURE", "0.1")),
            enable_template_learning=b("ASGLA_ENABLE_TEMPLATE_LEARNING", "true"),
            template_match_min_similarity=float(os.getenv("ASGLA_TEMPLATE_MATCH_MIN_SIMILARITY", "0.86")),
            template_drift_new_version_threshold=float(os.getenv("ASGLA_TEMPLATE_DRIFT_NEW_VERSION_THRESHOLD", "0.18")),
            template_require_confirmation_below=float(os.getenv("ASGLA_TEMPLATE_REQUIRE_CONFIRMATION_BELOW", "0.78")),
        )
        cfg.ensure_dirs()
        return cfg
