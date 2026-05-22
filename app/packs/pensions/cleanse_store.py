from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, asdict
from typing import Dict, Any, List, Optional

logger = logging.getLogger("asgl.pensions.cleanse")


@dataclass
class CleanseChange:
    tenant_id: str
    scheme_id: str
    change_id: str
    member_id: str
    field: str
    old_value: str
    new_value: str
    reason: str
    approved_by: str
    approved_at: str
    source: str = "ASGL"
    meta_json: Dict[str, Any] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class CleanseStore:
    def __init__(self, db_path: str = "data/asgl_pack_runs.db") -> None:
        self.db_path = db_path
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        return c

    def _init_db(self) -> None:
        try:
            with self._conn() as c:
                c.execute(
                    """
                    CREATE TABLE IF NOT EXISTS data_cleanse_changes (
                      tenant_id TEXT NOT NULL,
                      scheme_id TEXT NOT NULL,
                      change_id TEXT NOT NULL,
                      member_id TEXT DEFAULT '',
                      field TEXT DEFAULT '',
                      old_value TEXT DEFAULT '',
                      new_value TEXT DEFAULT '',
                      reason TEXT DEFAULT '',
                      approved_by TEXT DEFAULT '',
                      approved_at TEXT DEFAULT '',
                      source TEXT DEFAULT 'ASGL',
                      meta_json TEXT DEFAULT '{}',
                      created_at TEXT DEFAULT (datetime('now')),
                      PRIMARY KEY (tenant_id, scheme_id, change_id)
                    )
                    """
                )
        except Exception:
            logger.exception("cleanse_store_init_failed", extra={"db_path": self.db_path})

    def upsert(self, ch: CleanseChange) -> None:
        try:
            with self._conn() as c:
                c.execute(
                    """
                    INSERT OR REPLACE INTO data_cleanse_changes
                      (tenant_id, scheme_id, change_id, member_id, field, old_value, new_value, reason, approved_by, approved_at, source, meta_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ch.tenant_id, ch.scheme_id, ch.change_id, ch.member_id, ch.field,
                        ch.old_value, ch.new_value, ch.reason, ch.approved_by, ch.approved_at, ch.source,
                        json.dumps(ch.meta_json or {}),
                    ),
                )
        except Exception:
            logger.exception("cleanse_store_upsert_failed", extra={"tenant_id": ch.tenant_id, "scheme_id": ch.scheme_id, "change_id": ch.change_id})

    def list(self, *, tenant_id: str, scheme_id: str, limit: int = 2000) -> List[Dict[str, Any]]:
        try:
            with self._conn() as c:
                rows = c.execute(
                    "SELECT * FROM data_cleanse_changes WHERE tenant_id=? AND scheme_id=? ORDER BY created_at DESC LIMIT ?",
                    (tenant_id, scheme_id, int(limit)),
                ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                try:
                    d["meta_json"] = json.loads(d.get("meta_json") or "{}")
                except Exception:
                    d["meta_json"] = {}
                out.append(d)
            return out
        except Exception:
            logger.exception("cleanse_store_list_failed", extra={"tenant_id": tenant_id, "scheme_id": scheme_id})
            return []
