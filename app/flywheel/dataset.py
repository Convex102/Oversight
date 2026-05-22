from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict


def _utc() -> str:
    return datetime.utcnow().isoformat() + "Z"


class DatasetStore:
    """Append-only JSONL dataset store (tenant-isolated)."""

    def __init__(self, base_dir: str = "data/flywheel", logger=None):
        self.base_dir = base_dir
        self.logger = logger
        os.makedirs(self.base_dir, exist_ok=True)

    def _path(self, tenant_id: str, vertical: str) -> str:
        safe_t = tenant_id.replace("/", "_")
        safe_v = vertical.replace("/", "_")
        return os.path.join(self.base_dir, safe_t, f"{safe_v}.jsonl")

    def append(self, *, tenant_id: str, vertical: str, row: Dict[str, Any]) -> None:
        p = self._path(tenant_id, vertical)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")
        if self.logger:
            try:
                self.logger.info("flywheel.dataset_append", extra={"tenant_id": tenant_id, "vertical": vertical})
            except Exception:
                pass

    def snapshot_meta(self, *, tenant_id: str, vertical: str) -> Dict[str, Any]:
        p = self._path(tenant_id, vertical)
        if not os.path.exists(p):
            return {"tenant_id": tenant_id, "vertical": vertical, "rows": 0, "updated_at": None}
        rows = 0
        try:
            with open(p, "r", encoding="utf-8") as f:
                for _ in f:
                    rows += 1
        except Exception:
            rows = 0
        return {"tenant_id": tenant_id, "vertical": vertical, "rows": rows, "updated_at": _utc(), "path": p}
