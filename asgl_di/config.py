from dataclasses import dataclass
from pathlib import Path
import os

@dataclass
class DecisionIntelligenceConfig:
    enabled: bool = True
    output_root: Path = Path("outputs")
    ruleset: str = "universal_v1"
    severity_threshold: float = 0.65

    @classmethod
    def from_env(cls, output_root: Path):
        enabled = os.getenv("ASGL_DI_ENABLED", "1").strip() not in ("0","false","False","no","NO")
        ruleset = os.getenv("ASGL_DI_RULESET", "universal_v1").strip()
        try:
            thr = float(os.getenv("ASGL_DI_SEVERITY_THRESHOLD", "0.65"))
        except Exception:
            thr = 0.65
        return cls(enabled=enabled, output_root=output_root, ruleset=ruleset, severity_threshold=thr)

    def ensure_dirs(self):
        self.output_root.mkdir(parents=True, exist_ok=True)
