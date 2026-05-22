from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional


def _utc() -> str:
    return datetime.utcnow().isoformat() + "Z"


@dataclass
class Incident:
    incident_id: str
    ts: str
    tenant_id: str
    client_id: str
    run_id: str
    title: str
    severity: str
    status: str
    category: str
    meta: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class IncidentManager:
    def __init__(self, logger=None):
        self.logger = logger
        self._items: Dict[str, Incident] = {}

    def create(
        self,
        *,
        tenant_id: str,
        client_id: str,
        run_id: str,
        title: str,
        severity: str,
        category: str,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Incident:
        incident_id = f"inc_{len(self._items)+1:06d}"
        inc = Incident(
            incident_id=incident_id,
            ts=_utc(),
            tenant_id=tenant_id,
            client_id=client_id,
            run_id=run_id,
            title=title,
            severity=severity,
            status="OPEN",
            category=category,
            meta=meta or {},
        )
        self._items[incident_id] = inc
        if self.logger:
            try:
                self.logger.error("governance.incident_created", extra={"incident_id": incident_id, "severity": severity, "category": category})
            except Exception:
                pass
        return inc

    def resolve(self, incident_id: str, *, note: str = "") -> Optional[Incident]:
        inc = self._items.get(incident_id)
        if not inc:
            return None
        inc.status = "RESOLVED"
        inc.meta["resolution_note"] = note
        inc.meta["resolved_at"] = _utc()
        if self.logger:
            try:
                self.logger.info("governance.incident_resolved", extra={"incident_id": incident_id})
            except Exception:
                pass
        return inc

    def list(self, tenant_id: str = "") -> List[Dict[str, Any]]:
        out = []
        for inc in self._items.values():
            if tenant_id and inc.tenant_id != tenant_id:
                continue
            out.append(inc.to_dict())
        return sorted(out, key=lambda x: x["ts"], reverse=True)
