from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass, asdict
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional


def _utc() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _sha256(obj: Any) -> str:
    raw = json.dumps(obj, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class FlywheelEventType(str, Enum):
    MAPPING_ACCEPTED = "mapping.accepted"
    MAPPING_REJECTED = "mapping.rejected"
    MAPPING_CORRECTED = "mapping.corrected"
    EXCEPTION_CONFIRMED = "exception.confirmed"
    EXCEPTION_OVERRIDDEN = "exception.overridden"
    NOTE_ADDED = "review.note_added"

    INGESTION_COMPLETE = "ingestion.complete"
    REVIEW_COMPLETE = "review.complete"
    EXPORT_COMPLETE = "export.complete"

    QUEUE_ITEM_CREATED = "flywheel.queue_item_created"
    TRAINING_TRIGGERED = "flywheel.training_triggered"
    TRAINING_COMPLETED = "flywheel.training_completed"


@dataclass
class FlywheelEvent:
    ts: str
    tenant_id: str
    client_id: str
    run_id: str
    type: str
    actor: str
    vertical: str
    confidence: float
    payload: Dict[str, Any]
    meta: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["sha256"] = _sha256(d)
        return d


def make_event(
    *,
    tenant_id: str,
    client_id: str,
    run_id: str,
    type: FlywheelEventType,
    actor: str,
    vertical: str,
    confidence: float = 1.0,
    payload: Optional[Dict[str, Any]] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    evt = FlywheelEvent(
        ts=_utc(),
        tenant_id=tenant_id,
        client_id=client_id,
        run_id=run_id,
        type=str(type),
        actor=actor,
        vertical=vertical,
        confidence=float(confidence),
        payload=payload or {},
        meta=meta or {},
    ).to_dict()
    return evt
