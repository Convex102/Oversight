from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Dict, Any, List


def _sha256(obj: Any) -> str:
    raw = json.dumps(obj, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def build_evidence_bundle(
    *,
    tenant_id: str,
    client_id: str,
    run_id: str,
    canonical: Dict[str, Any],
    findings: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Phase 4: deterministic evidence bundle builder for accountancy reviews.

    Produces:
      - run metadata
      - canonical hash
      - findings hash
      - references to evidence artefacts in canonical (statutory reports, statements, notes)
      - provenance fields suitable for audit
    """
    now = datetime.utcnow().isoformat() + "Z"
    fw = str((canonical.get("flags") or {}).get("framework") or "")
    size_band = str((canonical.get("flags") or {}).get("size_band") or "")

    # Evidence references (dotted keys)
    refs = []
    for key in [
        "statements.balance_sheet",
        "statements.profit_and_loss",
        "statements.cashflow",
        "notes.accounting_policies",
        "notes.tax",
        "notes.related_parties",
        "notes.going_concern",
        "statutory_reports.directors_report",
        "statutory_reports.strategic_report",
    ]:
        refs.append({"key": key, "present": _dotted_present(canonical, key)})

    bundle = {
        "schema": "asgl.accountancy.evidence_bundle.v1",
        "generated_at": now,
        "tenant_id": tenant_id,
        "client_id": client_id,
        "run_id": run_id,
        "framework": fw,
        "size_band": size_band,
        "canonical_sha256": _sha256(canonical),
        "findings_sha256": _sha256(findings),
        "evidence_refs": refs,
        "provenance": {
            "source": "asgl.accountancy",
            "mode": "deterministic",
            "note": "No LLM-generated citations. Regulatory guidance retrieved deterministically from library.",
        },
    }
    return bundle


def _dotted_present(canonical: Dict[str, Any], dotted: str) -> bool:
    parts = dotted.split(".")
    cur: Any = canonical
    for p in parts:
        if not isinstance(cur, dict):
            return False
        cur = cur.get(p)
        if cur is None:
            return False
    if isinstance(cur, str):
        return bool(cur.strip())
    if isinstance(cur, (list, dict)):
        return len(cur) > 0
    return True
