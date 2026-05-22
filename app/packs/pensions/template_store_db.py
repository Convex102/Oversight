from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import dataclass
from typing import Dict, Any, Optional, Tuple, List

from .templates import PTemplateFingerprint, similarity

logger = logging.getLogger("asgl.pensions.template_store_db")


@dataclass
class TemplateMatch:
    key: str
    score: float
    fingerprint: Optional[PTemplateFingerprint] = None


def _default_db_path() -> str:
    # Safe default under cwd; allow override by env var.
    return os.environ.get("ASGL_TEMPLATE_DB", "asgl_templates.sqlite")


class TemplateStoreDB:
    """
    Phase 7: sqlite-backed template store.
    - fast
    - portable
    - no infra required
    """

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or _default_db_path()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        return c

    def _init_db(self) -> None:
        with self._conn() as c:
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS template_fingerprints (
                    tenant_id TEXT NOT NULL,
                    fp_key TEXT NOT NULL,
                    pack_id TEXT NOT NULL,
                    template_name TEXT,
                    sheet_hint TEXT,
                    column_count INTEGER,
                    header_sig_json TEXT,
                    version_tag TEXT,
                    extra_json TEXT,
                    created_at TEXT DEFAULT (datetime('now')),
                    updated_at TEXT DEFAULT (datetime('now')),
                    PRIMARY KEY (tenant_id, fp_key)
                )
                """
            )
            c.execute("CREATE INDEX IF NOT EXISTS idx_template_pack ON template_fingerprints(tenant_id, pack_id)")

    def upsert(self, fp: PTemplateFingerprint) -> str:
        extra_json = json.dumps(fp.extra or {}, default=str)
        with self._conn() as c:
            c.execute(
                """
                INSERT INTO template_fingerprints
                    (tenant_id, fp_key, pack_id, template_name, sheet_hint, column_count, header_sig_json, version_tag, extra_json)
                VALUES
                    (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, fp_key) DO UPDATE SET
                    template_name=excluded.template_name,
                    sheet_hint=excluded.sheet_hint,
                    column_count=excluded.column_count,
                    header_sig_json=excluded.header_sig_json,
                    version_tag=excluded.version_tag,
                    extra_json=excluded.extra_json,
                    updated_at=datetime('now')
                """,
                (
                    fp.tenant_id,
                    fp.key(),
                    fp.pack_id,
                    fp.template_name,
                    fp.sheet_hint,
                    int(fp.column_count),
                    json.dumps(fp.header_text_signature or []),
                    fp.version_tag,
                    extra_json,
                ),
            )
        logger.info("template_store_db.upsert", extra={"tenant_id": fp.tenant_id, "key": fp.key(), "template": fp.template_name})
        return fp.key()

    def _row_to_fp(self, row: sqlite3.Row) -> PTemplateFingerprint:
        return PTemplateFingerprint(
            tenant_id=row["tenant_id"],
            pack_id=row["pack_id"],
            template_name=row["template_name"] or "unknown",
            sheet_hint=row["sheet_hint"],
            header_text_signature=json.loads(row["header_sig_json"] or "[]"),
            column_count=int(row["column_count"] or 0),
            version_tag=row["version_tag"] or "v1",
            extra=json.loads(row["extra_json"] or "{}"),
        )

    def best_match(self, tenant_id: str, fp: PTemplateFingerprint) -> Optional[TemplateMatch]:
        best: Optional[TemplateMatch] = None
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM template_fingerprints WHERE tenant_id=? AND pack_id=?",
                (tenant_id, fp.pack_id),
            ).fetchall()
        for row in rows:
            cand = self._row_to_fp(row)
            s = similarity(fp, cand)
            if best is None or s > best.score:
                best = TemplateMatch(key=row["fp_key"], score=float(s), fingerprint=cand)
        return best

    def count(self, tenant_id: Optional[str] = None) -> int:
        with self._conn() as c:
            if tenant_id:
                r = c.execute("SELECT COUNT(1) AS n FROM template_fingerprints WHERE tenant_id=?", (tenant_id,)).fetchone()
            else:
                r = c.execute("SELECT COUNT(1) AS n FROM template_fingerprints").fetchone()
        return int(r["n"] if r else 0)

    def list(self, tenant_id: str, limit: int = 200) -> List[Dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT tenant_id, fp_key, pack_id, template_name, sheet_hint, column_count, version_tag, updated_at FROM template_fingerprints WHERE tenant_id=? ORDER BY updated_at DESC LIMIT ?",
                (tenant_id, int(limit)),
            ).fetchall()
        return [dict(r) for r in rows]
