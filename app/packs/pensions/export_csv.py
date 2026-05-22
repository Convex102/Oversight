from __future__ import annotations

import csv
import io
from typing import List, Dict, Any


def export_queue_to_csv(items: List[Dict[str, Any]]) -> bytes:
    buf = io.StringIO()
    cols = ["tenant_id", "scheme_id", "item_id", "run_id", "severity", "status", "member_id", "code", "message", "created_at", "updated_at"]
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    for it in items:
        row = {c: it.get(c, "") for c in cols}
        w.writerow(row)
    return buf.getvalue().encode("utf-8")
