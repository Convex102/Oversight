from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, asdict
from typing import Dict, Any, List, Optional

logger = logging.getLogger("asgl.pensions.comms")


@dataclass
class CommsRecord:
    tenant_id: str
    scheme_id: str
    comm_id: str
    member_id: str
    comm_type: str
    channel: str
    status: str
    template_id: str
    sent_at: str
    meta_json: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class CommsStore:
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
                    CREATE TABLE IF NOT EXISTS member_comms (
                      tenant_id TEXT NOT NULL,
                      scheme_id TEXT NOT NULL,
                      comm_id TEXT NOT NULL,
                      member_id TEXT DEFAULT '',
                      comm_type TEXT DEFAULT '',
                      channel TEXT DEFAULT '',
                      status TEXT DEFAULT '',
                      template_id TEXT DEFAULT '',
                      sent_at TEXT DEFAULT '',
                      meta_json TEXT DEFAULT '{}',
                      created_at TEXT DEFAULT (datetime('now')),
                      PRIMARY KEY (tenant_id, scheme_id, comm_id)
                    )
                    """
                )
        except Exception:
            logger.exception("comms_store_init_failed", extra={"db_path": self.db_path})

    def upsert(self, rec: CommsRecord) -> None:
        try:
            with self._conn() as c:
                c.execute(
                    """
                    INSERT OR REPLACE INTO member_comms
                      (tenant_id, scheme_id, comm_id, member_id, comm_type, channel, status, template_id, sent_at, meta_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        rec.tenant_id,
                        rec.scheme_id,
                        rec.comm_id,
                        rec.member_id,
                        rec.comm_type,
                        rec.channel,
                        rec.status,
                        rec.template_id,
                        rec.sent_at,
                        json.dumps(rec.meta_json or {}),
                    ),
                )
        except Exception:
            logger.exception("comms_store_upsert_failed", extra={"tenant_id": rec.tenant_id, "scheme_id": rec.scheme_id, "comm_id": rec.comm_id})

    def list(self, *, tenant_id: str, scheme_id: str, limit: int = 1000) -> List[Dict[str, Any]]:
        try:
            with self._conn() as c:
                rows = c.execute(
                    "SELECT * FROM member_comms WHERE tenant_id=? AND scheme_id=? ORDER BY created_at DESC LIMIT ?",
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
            logger.exception("comms_store_list_failed", extra={"tenant_id": tenant_id, "scheme_id": scheme_id})
            return []
