from __future__ import annotations

import os
import sqlite3
import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any

logger = logging.getLogger("asgl.pensions.policy")


@dataclass
class TemplatePolicy:
    tenant_id: str
    fp_key: str
    autopilot_enabled: bool = False
    autopilot_min_score: float = 0.92
    lock_template: bool = False
    require_manual_review: bool = True
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "fp_key": self.fp_key,
            "autopilot_enabled": bool(self.autopilot_enabled),
            "autopilot_min_score": float(self.autopilot_min_score),
            "lock_template": bool(self.lock_template),
            "require_manual_review": bool(self.require_manual_review),
            "notes": self.notes,
        }


def _default_policy_db_path() -> str:
    return os.environ.get("ASGL_POLICY_DB", "asgl_policies.sqlite")


class TemplatePolicyStore:
    """
    Phase 8: SQLite policy store for template behaviour gates.
    Works locally without infra and is safe for pilots.
    """
    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or _default_policy_db_path()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        return c

    def _init_db(self) -> None:
        with self._conn() as c:
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS template_policies (
                    tenant_id TEXT NOT NULL,
                    fp_key TEXT NOT NULL,
                    autopilot_enabled INTEGER,
                    autopilot_min_score REAL,
                    lock_template INTEGER,
                    require_manual_review INTEGER,
                    notes TEXT,
                    created_at TEXT DEFAULT (datetime('now')),
                    updated_at TEXT DEFAULT (datetime('now')),
                    PRIMARY KEY (tenant_id, fp_key)
                )
                """
            )
            c.execute("CREATE INDEX IF NOT EXISTS idx_policy_tenant ON template_policies(tenant_id)")

    def upsert(self, pol: TemplatePolicy) -> None:
        with self._conn() as c:
            c.execute(
                """
                INSERT INTO template_policies
                    (tenant_id, fp_key, autopilot_enabled, autopilot_min_score, lock_template, require_manual_review, notes)
                VALUES
                    (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, fp_key) DO UPDATE SET
                    autopilot_enabled=excluded.autopilot_enabled,
                    autopilot_min_score=excluded.autopilot_min_score,
                    lock_template=excluded.lock_template,
                    require_manual_review=excluded.require_manual_review,
                    notes=excluded.notes,
                    updated_at=datetime('now')
                """,
                (
                    pol.tenant_id,
                    pol.fp_key,
                    1 if pol.autopilot_enabled else 0,
                    float(pol.autopilot_min_score),
                    1 if pol.lock_template else 0,
                    1 if pol.require_manual_review else 0,
                    pol.notes or "",
                ),
            )
        logger.info("policy.upsert", extra={"tenant_id": pol.tenant_id, "fp_key": pol.fp_key})

    def get(self, *, tenant_id: str, fp_key: Optional[str]) -> Optional[TemplatePolicy]:
        if not fp_key:
            return None
        with self._conn() as c:
            row = c.execute("SELECT * FROM template_policies WHERE tenant_id=? AND fp_key=?", (tenant_id, fp_key)).fetchone()
        if not row:
            return None
        return TemplatePolicy(
            tenant_id=row["tenant_id"],
            fp_key=row["fp_key"],
            autopilot_enabled=bool(row["autopilot_enabled"]),
            autopilot_min_score=float(row["autopilot_min_score"] or 0.92),
            lock_template=bool(row["lock_template"]),
            require_manual_review=bool(row["require_manual_review"]),
            notes=row["notes"] or "",
        )
