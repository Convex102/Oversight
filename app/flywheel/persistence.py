from __future__ import annotations

import json
import os
from typing import Any, Dict


class JSONFileStore:
    def __init__(self, path: str, logger=None):
        self.path = path
        self.logger = logger
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def load(self) -> Dict[str, Any]:
        if not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f) or {}
        except Exception:
            return {}

    def save(self, doc: Dict[str, Any]) -> None:
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(doc, f, indent=2, default=str)
            os.replace(tmp, self.path)
        except Exception:
            if self.logger:
                try:
                    self.logger.exception("flywheel.persist_failed", extra={"path": self.path})
                except Exception:
                    pass
