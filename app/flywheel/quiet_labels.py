from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional


def _utc() -> str:
    return datetime.utcnow().isoformat() + "Z"


@dataclass
class QuietLabel:
    """Repeatable mapping pattern for a template cluster."""
    label_id: str
    ts: str
    tenant_id: str
    vertical: str
    cluster_key: str
    source: str
    mapping: Dict[str, Any]
    confidence: float
    usage_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class QuietLabelStore:
    def __init__(self, logger=None):
        self.logger = logger
        self._items: Dict[str, QuietLabel] = {}

    def upsert(self, *, tenant_id: str, vertical: str, cluster_key: str, mapping: Dict[str, Any], confidence: float, source: str = "human_confirmed") -> QuietLabel:
        label_id = f"ql_{len(self._items)+1:06d}"
        ql = QuietLabel(
            label_id=label_id,
            ts=_utc(),
            tenant_id=tenant_id,
            vertical=vertical,
            cluster_key=cluster_key,
            source=source,
            mapping=mapping,
            confidence=float(confidence),
            usage_count=0,
        )
        self._items[label_id] = ql
        if self.logger:
            try:
                self.logger.info("flywheel.quiet_label_upsert", extra={"tenant_id": tenant_id, "vertical": vertical, "label_id": label_id})
            except Exception:
                pass
        return ql

    def find(self, *, tenant_id: str, vertical: str, cluster_key: str, min_conf: float = 0.90) -> Optional[Dict[str, Any]]:
        best = None
        for ql in self._items.values():
            if ql.tenant_id != tenant_id or ql.vertical != vertical or ql.cluster_key != cluster_key:
                continue
            if ql.confidence < min_conf:
                continue
            if not best or ql.confidence > best.confidence:
                best = ql
        if best:
            best.usage_count += 1
            return best.to_dict()
        return None

    def list(self, *, tenant_id: str = "", vertical: str = "") -> List[Dict[str, Any]]:
        out = []
        for ql in self._items.values():
            if tenant_id and ql.tenant_id != tenant_id:
                continue
            if vertical and ql.vertical != vertical:
                continue
            out.append(ql.to_dict())
        return sorted(out, key=lambda x: x["ts"], reverse=True)
