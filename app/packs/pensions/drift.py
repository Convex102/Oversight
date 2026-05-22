from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Set

from .templates import PTemplateFingerprint

logger = logging.getLogger("asgl.pensions.drift")


@dataclass
class DriftReport:
    has_drift: bool
    severity: str = "NONE"  # LOW/MED/HIGH
    column_count_delta: int = 0
    added_headers: List[str] = field(default_factory=list)
    removed_headers: List[str] = field(default_factory=list)
    overlap_ratio: float = 1.0
    notes: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "has_drift": self.has_drift,
            "severity": self.severity,
            "column_count_delta": self.column_count_delta,
            "added_headers": list(self.added_headers),
            "removed_headers": list(self.removed_headers),
            "overlap_ratio": float(self.overlap_ratio),
            "notes": dict(self.notes),
        }


def _hdr_set(fp: PTemplateFingerprint) -> Set[str]:
    return {h.strip().lower() for h in (fp.header_text_signature or []) if h and str(h).strip()}


def detect_drift(*, current: PTemplateFingerprint, previous: PTemplateFingerprint) -> DriftReport:
    """
    Phase 7: deterministic drift detection between template versions.
    Emits a drift report used by reviewer UI + telemetry.
    """
    a = _hdr_set(current)
    b = _hdr_set(previous)
    if not a or not b:
        return DriftReport(has_drift=False, severity="LOW", notes={"reason": "missing_headers"})

    inter = len(a & b)
    union = max(1, len(a | b))
    overlap = inter / union

    added = sorted(list(a - b))[:50]
    removed = sorted(list(b - a))[:50]

    col_delta = int(current.column_count) - int(previous.column_count)

    has_drift = False
    severity = "NONE"
    # simple thresholds; tune per tenant later
    if abs(col_delta) >= 3 or overlap < 0.75 or len(added) >= 3 or len(removed) >= 3:
        has_drift = True
        if overlap < 0.55 or abs(col_delta) >= 8:
            severity = "HIGH"
        elif overlap < 0.70 or abs(col_delta) >= 5:
            severity = "MED"
        else:
            severity = "LOW"

    rep = DriftReport(
        has_drift=has_drift,
        severity=severity,
        column_count_delta=col_delta,
        added_headers=added,
        removed_headers=removed,
        overlap_ratio=overlap,
        notes={"current_template": current.template_name, "previous_template": previous.template_name},
    )
    logger.info("pensions.drift.detected" if has_drift else "pensions.drift.none", extra=rep.to_dict())
    return rep
