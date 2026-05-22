from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, Any, List

logger = logging.getLogger("asgl.accountancy.regs")

LIB_PATH = Path(__file__).resolve().parent / "resources" / "accountancy_regulations_library.json"


def load_regs_library() -> Dict[str, Any]:
    try:
        return json.loads(LIB_PATH.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("accountancy_regs_library_load_failed", extra={"path": str(LIB_PATH)})
        return {"version": "0.0.0", "clauses": []}


def query_clauses_by_tags(tags: List[str], limit: int = 8) -> List[Dict[str, Any]]:
    lib = load_regs_library()
    clauses = lib.get("clauses") or []
    tset = {str(t).lower() for t in (tags or [])}
    out = []
    for c in clauses:
        ctags = {str(t).lower() for t in (c.get("tags") or [])}
        if tset & ctags:
            out.append(c)
        if len(out) >= int(limit):
            break
    return out
