"""ASGL-C debug utilities."""
from __future__ import annotations
import json
import datetime as dt
from pathlib import Path
from typing import Any, Dict, Optional


class DebugSnapshotter:
    """Saves debug snapshots to disk during schema inference."""

    def __init__(self, debug_dir: Path):
        self.debug_dir = debug_dir
        self.debug_dir.mkdir(parents=True, exist_ok=True)

    def save(self, data: Dict[str, Any], prefix: str = "debug") -> Optional[str]:
        try:
            ts = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
            path = self.debug_dir / f"{prefix}_{ts}.json"
            path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
            return str(path)
        except Exception:
            return None
