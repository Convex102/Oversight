from __future__ import annotations

import heapq
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


def _utc() -> str:
    return datetime.utcnow().isoformat() + "Z"


@dataclass
class QueueItem:
    item_id: str
    ts: str
    tenant_id: str
    client_id: str
    run_id: str
    vertical: str
    kind: str
    priority: float
    confidence: float
    payload: Dict[str, Any]
    status: str = "OPEN"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ActiveLearningQueue:
    def __init__(self, logger=None):
        self.logger = logger
        self._items: Dict[str, QueueItem] = {}
        self._heap: List[Tuple[float, str]] = []

    def add(self, *, tenant_id: str, client_id: str, run_id: str, vertical: str, kind: str, priority: float, confidence: float, payload: Dict[str, Any]) -> QueueItem:
        item_id = f"q_{len(self._items)+1:06d}"
        qi = QueueItem(
            item_id=item_id,
            ts=_utc(),
            tenant_id=tenant_id,
            client_id=client_id,
            run_id=run_id,
            vertical=vertical,
            kind=kind,
            priority=float(priority),
            confidence=float(confidence),
            payload=payload,
        )
        self._items[item_id] = qi
        heapq.heappush(self._heap, (-qi.priority, item_id))
        if self.logger:
            try:
                self.logger.info("flywheel.queue_add", extra={"tenant_id": tenant_id, "item_id": item_id, "kind": kind, "priority": priority})
            except Exception:
                pass
        return qi

    def pop(self, *, tenant_id: str = "", vertical: str = "") -> Optional[Dict[str, Any]]:
        while self._heap:
            _, item_id = heapq.heappop(self._heap)
            qi = self._items.get(item_id)
            if not qi or qi.status != "OPEN":
                continue
            if tenant_id and qi.tenant_id != tenant_id:
                continue
            if vertical and qi.vertical != vertical:
                continue
            qi.status = "ASSIGNED"
            return qi.to_dict()
        return None

    def resolve(self, item_id: str, *, note: str = "") -> Optional[Dict[str, Any]]:
        qi = self._items.get(item_id)
        if not qi:
            return None
        qi.status = "RESOLVED"
        qi.payload.setdefault("resolution_note", note)
        qi.payload.setdefault("resolved_at", _utc())
        return qi.to_dict()

    def stats(self, *, tenant_id: str = "", vertical: str = "") -> Dict[str, Any]:
        open_n = assigned_n = resolved_n = 0
        for qi in self._items.values():
            if tenant_id and qi.tenant_id != tenant_id:
                continue
            if vertical and qi.vertical != vertical:
                continue
            if qi.status == "OPEN":
                open_n += 1
            elif qi.status == "ASSIGNED":
                assigned_n += 1
            else:
                resolved_n += 1
        return {"tenant_id": tenant_id or "ALL", "vertical": vertical or "ALL", "open": open_n, "assigned": assigned_n, "resolved": resolved_n, "total": open_n + assigned_n + resolved_n}
