"""Pack loader for Multi-Vertical Pack SDK."""

from __future__ import annotations

import json, importlib
from pathlib import Path
from typing import List, Optional

from app.logging import get_logger
from .types import PackDefinition

log = get_logger(__name__)

def _packs_root() -> Path:
    return Path(__file__).resolve().parents[3] / "packs"

def list_packs() -> List[str]:
    root = _packs_root()
    if not root.exists():
        return []
    out = []
    for v in root.iterdir():
        if not v.is_dir():
            continue
        for p in v.iterdir():
            if p.is_dir():
                out.append(f"{v.name}/{p.name}")
    return sorted(out)

def load_pack(vertical: str, pack_id: str) -> Optional[PackDefinition]:
    root = _packs_root()
    pdir = root / vertical / pack_id
    try:
        if pdir.exists():
            pj = pdir / "pack.json"
            if pj.exists():
                meta = json.loads(pj.read_text())
                validators = []
                vmod = pdir / "validators.py"
                if vmod.exists():
                    spec = importlib.util.spec_from_file_location(f"pack_{vertical}_{pack_id}_validators", str(vmod))
                    mod = importlib.util.module_from_spec(spec)
                    assert spec and spec.loader
                    spec.loader.exec_module(mod)
                    if hasattr(mod, "get_pack"):
                        return mod.get_pack(meta=meta)
                    if hasattr(mod, "validators"):
                        validators = list(mod.validators)
                return PackDefinition(
                    pack_id=pack_id,
                    vertical=vertical,
                    version=str(meta.get("version", "0.0.1")),
                    title=str(meta.get("title", pack_id)),
                    description=str(meta.get("description", "")),
                    schema_fields=list(meta.get("schema_fields", [])),
                    validators=validators,
                )
    except Exception as e:
        log.exception("pack_load_failed", vertical=vertical, pack_id=pack_id, error=str(e))

    try:
        mod = importlib.import_module(f"app.packs.{vertical}.{pack_id}.pack")
        if hasattr(mod, "get_pack"):
            return mod.get_pack()
    except Exception:
        return None
    return None
