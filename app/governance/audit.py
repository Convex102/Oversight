from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any, Dict, Optional, List


def _utc() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _sha256(obj: Any) -> str:
    raw = json.dumps(obj, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@dataclass
class AuditEvent:
    """
    Single audit event designed for:
      - deterministic provenance
      - human readability
      - telemetry emission
      - storage in external SIEM/warehouse
    """

    ts: str
    tenant_id: str
    client_id: str
    run_id: str
    event_type: str
    actor: str  # "system" / user id
    severity: str  # INFO/WARN/ERROR/CRITICAL
    message: str
    meta: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["sha256"] = _sha256(d)
        return d


class AuditLog:
    """
    In-memory audit log accumulator for a single pipeline run.
    You can persist the .events list into:
      - telemetry event stream
      - file storage / db
      - customer evidence bundle
    """

    def __init__(self, tenant_id: str, client_id: str, run_id: str, logger=None):
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.run_id = run_id
        self.logger = logger
        self.events: List[Dict[str, Any]] = []

    def emit(
        self,
        event_type: str,
        message: str,
        *,
        severity: str = "INFO",
        actor: str = "system",
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        evt = AuditEvent(
            ts=_utc(),
            tenant_id=self.tenant_id,
            client_id=self.client_id,
            run_id=self.run_id,
            event_type=event_type,
            actor=actor,
            severity=severity,
            message=message,
            meta=meta or {},
        ).to_dict()
        self.events.append(evt)
        if self.logger:
            try:
                self.logger.info(
                    "governance.audit_event",
                    extra={
                        "tenant_id": self.tenant_id,
                        "client_id": self.client_id,
                        "run_id": self.run_id,
                        "event_type": event_type,
                        "severity": severity,
                        "actor": actor,
                    },
                )
            except Exception:
                pass
        return evt

    def summary(self) -> Dict[str, Any]:
        sev_count: Dict[str, int] = {}
        for e in self.events:
            sev = e.get("severity", "INFO")
            sev_count[sev] = sev_count.get(sev, 0) + 1
        return {
            "schema": "asgl.governance.audit_log.v1",
            "tenant_id": self.tenant_id,
            "client_id": self.client_id,
            "run_id": self.run_id,
            "generated_at": _utc(),
            "event_count": len(self.events),
            "severity_counts": sev_count,
            "events_sha256": _sha256(self.events),
        }
