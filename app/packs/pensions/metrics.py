from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Dict, Any, List

logger = logging.getLogger("asgl.pensions.metrics")


def _safe_div(a: float, b: float) -> float:
    return float(a) / float(b) if b else 0.0


@dataclass
class QueueStats:
    open_items: int
    resolved_items: int
    rejected_items: int
    high_severity_open: int
    schemes_active: int
    templates_active: int
    avg_findings_per_run: float
    auto_map_rate_estimate: float
    drift_rate_estimate: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "open_items": int(self.open_items),
            "resolved_items": int(self.resolved_items),
            "rejected_items": int(self.rejected_items),
            "high_severity_open": int(self.high_severity_open),
            "schemes_active": int(self.schemes_active),
            "templates_active": int(self.templates_active),
            "avg_findings_per_run": float(self.avg_findings_per_run),
            "auto_map_rate_estimate": float(self.auto_map_rate_estimate),
            "drift_rate_estimate": float(self.drift_rate_estimate),
        }


def compute_queue_stats(*, runs: List[Dict[str, Any]], queue_items: List[Dict[str, Any]], drift_events: List[Dict[str, Any]] | None = None) -> QueueStats:
    drift_events = drift_events or []
    open_items = sum(1 for q in queue_items if (q.get("status") or "").upper() == "OPEN")
    resolved_items = sum(1 for q in queue_items if (q.get("status") or "").upper() == "RESOLVED")
    rejected_items = sum(1 for q in queue_items if (q.get("status") or "").upper() == "REJECTED")
    high_sev_open = sum(1 for q in queue_items if (q.get("status") or "").upper() == "OPEN" and (q.get("severity") or "").upper() in ("ERROR", "HIGH"))

    schemes = {r.get("scheme_id") for r in runs if r.get("scheme_id")}
    templates = {r.get("template_name") for r in runs if r.get("template_name")}

    avg_findings = _safe_div(sum(len((r.get("findings_json") or [])) for r in runs), max(1, len(runs)))

    # estimates: we treat high match score as "auto map succeeded" proxy.
    auto_map = sum(1 for r in runs if float(r.get("match_score") or 0.0) >= 0.92)
    auto_map_rate = _safe_div(auto_map, max(1, len(runs)))

    drift_rate = _safe_div(len(drift_events), max(1, len(runs)))

    return QueueStats(
        open_items=open_items,
        resolved_items=resolved_items,
        rejected_items=rejected_items,
        high_severity_open=high_sev_open,
        schemes_active=len(schemes),
        templates_active=len(templates),
        avg_findings_per_run=float(avg_findings),
        auto_map_rate_estimate=float(auto_map_rate),
        drift_rate_estimate=float(drift_rate),
    )
