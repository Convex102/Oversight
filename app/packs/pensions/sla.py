from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Any, Optional


def _parse_dt(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        # sqlite returns 'YYYY-MM-DD HH:MM:SS'
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except Exception:
        return None


@dataclass
class SLAConfig:
    warn_hours: int = 48
    overdue_hours: int = 96


def sla_status(item: Dict[str, Any], cfg: SLAConfig = SLAConfig()) -> str:
    created = _parse_dt(item.get("created_at", "")) or _parse_dt(item.get("updated_at", "")) or datetime.now(timezone.utc)
    age_h = (datetime.now(timezone.utc) - created).total_seconds() / 3600.0
    if age_h >= cfg.overdue_hours:
        return "OVERDUE"
    if age_h >= cfg.warn_hours:
        return "DUE_SOON"
    return "OK"
