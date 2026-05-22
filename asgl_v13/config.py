from dataclasses import dataclass
from pathlib import Path
import os

@dataclass
class ASGLV13Config:
    enabled: bool = True
    ruleset: str = "universal_v1"
    output_root: Path = Path("outputs")
    summary_filename: str = "asgl_v13_decision_summary.json"

    @classmethod
    def from_env(cls, output_root: Path):
        enabled = os.getenv("ASGL_V13_ENABLED", "1").strip() not in ("0","false","False","no","NO")
        ruleset = os.getenv("ASGL_V13_RULESET", "universal_v1").strip()
        return cls(enabled=enabled, ruleset=ruleset, output_root=output_root)
