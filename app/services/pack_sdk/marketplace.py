"""Phase 2 v6: Pack Marketplace hooks (optional).

By default remote install is disabled; this just adds the hook layer.
"""

from __future__ import annotations

import os, shutil, zipfile, tempfile
from pathlib import Path
from typing import List, Optional

from app.logging import get_logger
from .loader import list_packs

log = get_logger(__name__)

def enabled() -> bool:
    return os.getenv("ASGL_PACK_MARKETPLACE_ENABLE", "0") not in ("0","false","FALSE","no","NO")

def installed() -> List[str]:
    return list_packs()

def packs_root() -> Path:
    return Path(__file__).resolve().parents[3] / "packs"

def install_pack_zip(zip_path: str) -> Optional[str]:
    if not enabled():
        return None
    root = packs_root()
    root.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path, 'r') as z:
            with tempfile.TemporaryDirectory() as td:
                z.extractall(td)
                tdp = Path(td)
                pj = list(tdp.rglob('pack.json'))
                if not pj:
                    return None
                pdir = pj[0].parent
                vertical = pdir.parent.name
                pack_id = pdir.name
                dest = root / vertical / pack_id
                dest.parent.mkdir(parents=True, exist_ok=True)
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(pdir, dest)
                log.info("pack_installed", vertical=vertical, pack_id=pack_id)
                return f"{vertical}/{pack_id}"
    except Exception as e:
        log.exception("pack_install_failed", error=str(e))
        return None
