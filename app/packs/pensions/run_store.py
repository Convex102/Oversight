from __future__ import annotations

import json
import os
import sqlite3
import logging
from dataclasses import dataclass
from typing import Dict, Any, Optional, List

logger = logging.getLogger("asgl.pensions.run_store")


def _default_run_db_path() -> str:
    return os.environ.get("ASGL_RUN_DB", "asgl_runs.sqlite")


@dataclass
class PackRunRecord:
    tenant_id: str
    scheme_id: str
    run_id: str
    pack_id: str
    template_name: str
    fingerprint_key: str
    match_score: float
    drift_json: Dict[str, Any]
    findings_json: List[Dict[str, Any]]
    canonical_json: Dict[str, Any]
    evidence_json: Dict[str, Any]
    created_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "scheme_id": self.scheme_id,
            "run_id": self.run_id,
            "pack_id": self.pack_id,
            "template_name": self.template_name,
            "fingerprint_key": self.fingerprint_key,
            "match_score": float(self.match_score),
            "drift": self.drift_json,
            "findings": self.findings_json,
            "canonical": self.canonical_json,
            "evidence_bundle": self.evidence_json,
            "created_at": self.created_at,
        }


class PackRunStore:
    """Phase 9: Persist each pack run and power a real review queue."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or _default_run_db_path()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        return c

    def _init_db(self) -> None:
        with self._conn() as c:
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS pack_runs (
                    tenant_id TEXT NOT NULL,
                    scheme_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    pack_id TEXT NOT NULL,
                    template_name TEXT,
                    fingerprint_key TEXT,
                    match_score REAL,
                    drift_json TEXT,
                    findings_json TEXT,
                    canonical_json TEXT,
                    evidence_json TEXT,
                    created_at TEXT DEFAULT (datetime('now')),
                    PRIMARY KEY (tenant_id, scheme_id, run_id)
                )
                """
            )
            c.execute("CREATE INDEX IF NOT EXISTS idx_pack_runs_tenant ON pack_runs(tenant_id, created_at DESC)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_pack_runs_scheme ON pack_runs(tenant_id, scheme_id, created_at DESC)")

            c.execute(
                """
                CREATE TABLE IF NOT EXISTS review_queue (
                    tenant_id TEXT NOT NULL,
                    scheme_id TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    pack_id TEXT NOT NULL,
                    template_name TEXT,
                    fingerprint_key TEXT,
                    severity TEXT,
                    status TEXT,
                    assigned_to TEXT,
                    member_id TEXT,
                    code TEXT,
                    message TEXT,
                    meta_json TEXT,
                    created_at TEXT DEFAULT (datetime('now')),
                    updated_at TEXT DEFAULT (datetime('now')),
                    PRIMARY KEY (tenant_id, scheme_id, item_id)
                )
                """
            )
            c.execute("CREATE INDEX IF NOT EXISTS idx_queue_status ON review_queue(tenant_id, status, updated_at DESC)")

    def save_run(self, record: PackRunRecord) -> None:
        with self._conn() as c:
            c.execute(
                """
                INSERT INTO pack_runs
                    (tenant_id, scheme_id, run_id, pack_id, template_name, fingerprint_key, match_score,
                     drift_json, findings_json, canonical_json, evidence_json)
                VALUES
                    (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.tenant_id,
                    record.scheme_id,
                    record.run_id,
                    record.pack_id,
                    record.template_name,
                    record.fingerprint_key,
                    float(record.match_score),
                    json.dumps(record.drift_json or {}),
                    json.dumps(record.findings_json or []),
                    json.dumps(record.canonical_json or {}),
                    json.dumps(record.evidence_json or {}),
                ),
            )
        logger.info("run_store.save_run", extra={"tenant_id": record.tenant_id, "scheme_id": record.scheme_id, "run_id": record.run_id})

    def enqueue_findings(self, *, tenant_id: str, scheme_id: str, run_id: str, pack_id: str, template_name: str, fingerprint_key: str, findings: List[Dict[str, Any]]) -> int:
        inserted = 0
        with self._conn() as c:
            for i, f in enumerate(findings):
                item_id = f"{run_id}:{i}"
                c.execute(
                    """
                    INSERT OR REPLACE INTO review_queue
                        (tenant_id, scheme_id, item_id, run_id, pack_id, template_name, fingerprint_key,
                         severity, status, assigned_to, member_id, code, message, meta_json, updated_at)
                    VALUES
                        (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                    """,
                    (
                        tenant_id,
                        scheme_id,
                        item_id,
                        run_id,
                        pack_id,
                        template_name,
                        fingerprint_key,
                        f.get("severity") or "INFO",
                        "OPEN",
                        "",
                        f.get("member_id") or "",
                        f.get("code") or "",
                        f.get("message") or "",
                        json.dumps({k: v for k, v in f.items() if k not in ("severity", "member_id", "code", "message")}),
                    ),
                )
                inserted += 1
        logger.info("run_store.enqueue_findings", extra={"tenant_id": tenant_id, "scheme_id": scheme_id, "run_id": run_id, "count": inserted})
        return inserted

    def list_queue(self, *, tenant_id: str, status: str = "OPEN", limit: int = 200) -> List[Dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM review_queue WHERE tenant_id=? AND status=? ORDER BY updated_at DESC LIMIT ?",
                (tenant_id, status, int(limit)),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_queue_item(self, *, tenant_id: str, scheme_id: str, item_id: str) -> Optional[Dict[str, Any]]:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM review_queue WHERE tenant_id=? AND scheme_id=? AND item_id=?",
                (tenant_id, scheme_id, item_id),
            ).fetchone()
        return dict(row) if row else None

    def update_queue_item(self, *, tenant_id: str, scheme_id: str, item_id: str, status: str, assigned_to: str = "") -> None:
        with self._conn() as c:
            c.execute(
                """
                UPDATE review_queue
                SET status=?, assigned_to=?, updated_at=datetime('now')
                WHERE tenant_id=? AND scheme_id=? AND item_id=?
                """,
                (status, assigned_to or "", tenant_id, scheme_id, item_id),
            )
        logger.info("run_store.queue.update", extra={"tenant_id": tenant_id, "scheme_id": scheme_id, "item_id": item_id, "status": status})

    def latest_run(self, *, tenant_id: str, scheme_id: str) -> Optional[Dict[str, Any]]:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM pack_runs WHERE tenant_id=? AND scheme_id=? ORDER BY created_at DESC LIMIT 1",
                (tenant_id, scheme_id),
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        for k in ("drift_json", "findings_json", "canonical_json", "evidence_json"):
            try:
                d[k] = json.loads(d[k] or "{}")
            except Exception:
                d[k] = {}
        return d
