"""Model Registry: load fine-tuned models from app.storage into local cache.

Phase 2 v2 uses this to load a tenant-specific column encoder fine-tune.

Storage keys (default):
  ml/models/tenant_<TENANT_ID>/phase2_column_encoder.zip

Env overrides:
  ASGL_PHASE2_MODEL_KEY (format string, supports {tenant_id})
  ASGL_MODEL_CACHE_DIR (default /tmp/asgl_models)

Behavior:
  - Downloads model zip if not cached or if etag/fingerprint changes (best-effort)
  - Extracts to a versioned folder
  - Returns local path suitable for SentenceTransformer(local_path)
"""

from __future__ import annotations

import os, json, time, hashlib, zipfile
from pathlib import Path
from typing import Optional, Tuple

from app.logging import get_logger
from app.storage import storage

log = get_logger(__name__)

def _cache_dir() -> Path:
    return Path(os.getenv("ASGL_MODEL_CACHE_DIR", "/tmp/asgl_models")).resolve()

def _default_key(tenant_id: str) -> str:
    fmt = os.getenv("ASGL_PHASE2_MODEL_KEY", "ml/models/tenant_{tenant_id}/phase2_column_encoder.zip")
    return fmt.format(tenant_id=str(tenant_id))

def _hash_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()

def get_or_load_phase2_model_path(tenant_id: str) -> Optional[str]:
    """Return local directory path to fine-tuned Phase2 model if present."""
    try:
        key = _default_key(tenant_id)
        blob = storage.read(key)
        if not blob:
            return None
        digest = _hash_bytes(blob)

        cache_root = _cache_dir()
        cache_root.mkdir(parents=True, exist_ok=True)
        meta_path = cache_root / f"phase2_{tenant_id}_meta.json"
        out_dir = cache_root / f"phase2_{tenant_id}_{digest[:12]}"

        if out_dir.exists():
            return str(out_dir)

        # If meta exists and points to existing dir, use it
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
                if meta.get("digest") == digest and meta.get("path") and Path(meta["path"]).exists():
                    return meta["path"]
            except Exception:
                pass

        # Write zip to temp and extract
        zip_path = cache_root / f"phase2_{tenant_id}_{digest[:12]}.zip"
        zip_path.write_bytes(blob)
        out_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, "r") as z:
            z.extractall(out_dir)
        # persist meta
        meta_path.write_text(json.dumps({
            "tenant_id": str(tenant_id),
            "key": key,
            "digest": digest,
            "path": str(out_dir),
            "ts": int(time.time()),
        }))
        log.info("model_loaded", tenant_id=str(tenant_id), key=key, path=str(out_dir))
        return str(out_dir)
    except Exception as e:
        log.exception("model_load_failed", error=str(e))
        return None
