from __future__ import annotations

import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

logger = logging.getLogger("asgl.pensions.pi")


@dataclass
class PIRateRow:
    effective_from: str  # YYYY-MM-DD
    effective_to: str    # YYYY-MM-DD
    rate: float          # as decimal e.g. 0.03
    category: str = "DEFAULT"
    source: str = "Trustee/Policy"


@dataclass
class PIResult:
    member_id: str
    base_pension: float
    applied_rate: float
    increased_pension: float
    category: str
    effective_date: str
    notes: str = ""
    exception: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _parse_date(s: str) -> Optional[datetime]:
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except Exception:
            continue
    return None


def _match_rate(rates: List[PIRateRow], effective_date: str, category: str = "DEFAULT") -> Optional[PIRateRow]:
    d = _parse_date(effective_date)
    if not d:
        return None
    # Prefer category-specific rows then DEFAULT.
    candidates = [x for x in rates if (x.category or "DEFAULT").upper() == (category or "DEFAULT").upper()]
    if not candidates:
        candidates = rates
    for rr in candidates:
        a = _parse_date(rr.effective_from)
        b = _parse_date(rr.effective_to)
        if a and b and a <= d <= b:
            return rr
    return None


def run_pi_exercise(*, scheme_id: str, tenant_id: str, effective_date: str, members: List[Dict[str, Any]], rate_table: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Phase 14: robust PI exercise engine.
    - deterministic
    - auditable
    - exception-aware
    member rows expected keys:
      member_id, base_pension, category(optional)
    """
    started = datetime.now(timezone.utc)
    logger.info("pi_exercise_start", extra={"tenant_id": tenant_id, "scheme_id": scheme_id, "effective_date": effective_date, "members": len(members or [])})

    rates: List[PIRateRow] = []
    for r in (rate_table or []):
        try:
            rates.append(PIRateRow(
                effective_from=str(r.get("effective_from") or r.get("from") or ""),
                effective_to=str(r.get("effective_to") or r.get("to") or ""),
                rate=float(r.get("rate") or 0.0),
                category=str(r.get("category") or "DEFAULT"),
                source=str(r.get("source") or "Trustee/Policy"),
            ))
        except Exception:
            logger.exception("pi_rate_row_parse_failed", extra={"tenant_id": tenant_id, "scheme_id": scheme_id, "row": r})

    results: List[PIResult] = []
    exceptions = 0
    for m in (members or [])[:200000]:
        mid = str(m.get("member_id") or m.get("id") or "")
        try:
            base = float(m.get("base_pension") or m.get("pension") or 0.0)
        except Exception:
            base = 0.0
        cat = str(m.get("category") or "DEFAULT")

        rr = _match_rate(rates, effective_date, category=cat)
        if not rr:
            exceptions += 1
            results.append(PIResult(
                member_id=mid,
                base_pension=base,
                applied_rate=0.0,
                increased_pension=base,
                category=cat,
                effective_date=effective_date,
                notes="No matching PI rate found for date/category",
                exception=True,
            ))
            continue

        inc = base * (1.0 + float(rr.rate))
        results.append(PIResult(
            member_id=mid,
            base_pension=base,
            applied_rate=float(rr.rate),
            increased_pension=inc,
            category=cat,
            effective_date=effective_date,
            notes=f"Matched rate row {rr.effective_from}..{rr.effective_to} ({rr.category})",
            exception=False,
        ))

    duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    logger.info("pi_exercise_complete", extra={"tenant_id": tenant_id, "scheme_id": scheme_id, "duration_ms": duration_ms, "results": len(results), "exceptions": exceptions})

    return {
        "tenant_id": tenant_id,
        "scheme_id": scheme_id,
        "effective_date": effective_date,
        "results": [r.to_dict() for r in results[:5000]],  # UI preview cap
        "stats": {"members": len(results), "exceptions": exceptions, "duration_ms": duration_ms},
        "rate_table_rows": [asdict(r) for r in rates],
    }
