from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional


def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def _sha(obj: Any) -> str:
    raw = json.dumps(obj, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@dataclass
class SchemaFingerprint:
    """Privacy-safe schema signature for template/library learning."""
    fingerprint_id: str
    vertical: str
    n_cols: int
    headers_hash: str
    types_hash: str
    meta: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def make_schema_fingerprint(
    *,
    vertical: str,
    headers: List[str],
    inferred_types: Optional[List[str]] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> SchemaFingerprint:
    norm_headers = [_norm(h) for h in (headers or [])]
    inferred_types = inferred_types or ["unknown"] * len(norm_headers)
    types = [_norm(t) for t in inferred_types]

    headers_hash = _sha({"headers": norm_headers})
    types_hash = _sha({"types": types})
    fid = _sha({"vertical": vertical, "headers_hash": headers_hash, "types_hash": types_hash, "n_cols": len(norm_headers)})[:24]

    return SchemaFingerprint(
        fingerprint_id=fid,
        vertical=vertical,
        n_cols=len(norm_headers),
        headers_hash=headers_hash,
        types_hash=types_hash,
        meta=meta or {},
    )


def cluster_key(headers: List[str], *, k: int = 12) -> str:
    """Cheap cluster key for quiet labeling (top-k headers hashed)."""
    h = sorted(set([_norm(x) for x in headers or []]))[:k]
    return _sha({"h": h})[:16]
