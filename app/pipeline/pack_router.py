from __future__ import annotations

from typing import Dict, Any

from app.packs import PACKS


def get_pack(pack_id: str) -> Dict[str, Any]:
    if pack_id not in PACKS:
        raise KeyError(f"Unknown pack_id: {pack_id}")
    return PACKS[pack_id]


def list_packs() -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in PACKS.items():
        spec = v.get("spec")
        out[k] = spec.to_dict() if hasattr(spec, "to_dict") else spec
    return out
