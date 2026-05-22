from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class DIEmbeddingConfig:
    enabled: bool = _env_bool("ASGL_DI_ML_ENABLED", True)
    model_name: str = os.getenv("ASGL_EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
    cache_dir: Path = Path(os.getenv("ASGL_EMBED_CACHE_DIR", ".asgl_cache/embeddings"))
    warmup: bool = _env_bool("ASGL_DI_ML_WARMUP", True)
    device: str = os.getenv("ASGL_EMBED_DEVICE", "auto")  # auto|cpu|cuda
    batch_size: int = int(os.getenv("ASGL_EMBED_BATCH_SIZE", "32"))
    max_text_len: int = int(os.getenv("ASGL_EMBED_MAX_TEXT_LEN", "4096"))


@dataclass(frozen=True)
class DIRankerConfig:
    top_k_default: int = int(os.getenv("ASGL_RANK_TOPK", "5"))
    weight_similarity: float = float(os.getenv("ASGL_RANK_W_SIM", "0.82"))
    weight_prior: float = float(os.getenv("ASGL_RANK_W_PRIOR", "0.18"))


@dataclass(frozen=True)
class DICalibrationConfig:
    min_conf: float = float(os.getenv("ASGL_CONF_MIN", "0.01"))
    max_conf: float = float(os.getenv("ASGL_CONF_MAX", "0.995"))
    template_bonus: float = float(os.getenv("ASGL_CONF_TEMPLATE_BONUS", "0.55"))
    drift_penalty: float = float(os.getenv("ASGL_CONF_DRIFT_PENALTY", "0.65"))


EMBED = DIEmbeddingConfig()
RANK = DIRankerConfig()
CAL = DICalibrationConfig()
