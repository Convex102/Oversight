from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


@dataclass
class ASGLCConfig:
    """Configuration for ASGL-C (Canonical Layer).

    ASGL-C is designed to be the *canonical* schema layer used across ASGL products.

    This config intentionally supports:
    - registry reuse (cache inferred schemas by fingerprint)
    - persistence (save newly inferred schemas)
    - debug snapshots (optional)
    """

    output_root: Path
    domain_hint: str | None = None
    min_confidence: float = 0.4
    log_level: str = "INFO"

    # Enterprise flags (used by inferencer.py)
    prefer_registry: bool = True
    persist_registry: bool = True
    debug_enabled: bool = False

    # Optional explicit registry path (defaults to <output_root>/adapters/registry.json)
    registry_path: Path | None = None

    def __post_init__(self):
        self.log_dir = self.output_root / "logs"
        self.debug_dir = self.output_root / "debug"
        self.adapter_dir = self.output_root / "adapters"
        if self.registry_path is None:
            self.registry_path = self.adapter_dir / "registry.json"
        self.ensure_dirs()

    def ensure_dirs(self):
        for d in [self.log_dir, self.debug_dir, self.adapter_dir]:
            d.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def from_env(output_root: Path) -> "ASGLCConfig":
        domain_hint = os.getenv("ASGL_DOMAIN_HINT") or None
        min_conf = float(os.getenv("ASGLC_MIN_CONFIDENCE", "0.4"))
        log_level = os.getenv("ASGLC_LOG_LEVEL", "INFO")

        prefer_registry = os.getenv("ASGLC_PREFER_REGISTRY", "1").lower() not in ("0", "false", "no")
        persist_registry = os.getenv("ASGLC_PERSIST_REGISTRY", "1").lower() not in ("0", "false", "no")
        debug_enabled = os.getenv("ASGLC_DEBUG", "0").lower() in ("1", "true", "yes")

        reg_path = os.getenv("ASGLC_REGISTRY_PATH")
        registry_path = Path(reg_path) if reg_path else None

        return ASGLCConfig(
            output_root=output_root,
            domain_hint=domain_hint,
            min_confidence=min_conf,
            log_level=log_level,
            prefer_registry=prefer_registry,
            persist_registry=persist_registry,
            debug_enabled=debug_enabled,
            registry_path=registry_path,
        )
