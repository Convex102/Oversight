from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any, Dict, Optional


def _utc() -> str:
    return datetime.utcnow().isoformat() + "Z"


@dataclass
class DriftSignal:
    ts: str
    tenant_id: str
    client_id: str
    run_id: str
    model_id: str
    model_version: str
    metric: str
    baseline: float
    current: float
    delta: float
    threshold: float
    status: str
    meta: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def compute_simple_drift(
    *,
    tenant_id: str,
    client_id: str,
    run_id: str,
    model_id: str,
    model_version: str,
    baseline_metric: float,
    current_metric: float,
    threshold: float,
    metric_name: str = "mapping_accuracy",
    meta: Optional[Dict[str, Any]] = None,
) -> DriftSignal:
    delta = float(current_metric) - float(baseline_metric)
    status = "OK"
    if abs(delta) >= threshold:
        status = "ALERT"
    elif abs(delta) >= threshold * 0.6:
        status = "WARN"
    return DriftSignal(
        ts=_utc(),
        tenant_id=tenant_id,
        client_id=client_id,
        run_id=run_id,
        model_id=model_id,
        model_version=model_version,
        metric=metric_name,
        baseline=float(baseline_metric),
        current=float(current_metric),
        delta=float(delta),
        threshold=float(threshold),
        status=status,
        meta=meta or {},
    )
