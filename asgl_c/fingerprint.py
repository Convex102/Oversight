from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Tuple
import pandas as pd

def dataset_fingerprint(df: pd.DataFrame) -> str:
    """Stable fingerprint for a dataframe structure.

    Uses:
    - column names
    - pandas dtypes
    - approx missingness signature
    - sample stats (low-resolution) to reduce collisions
    """
    cols = [str(c) for c in df.columns]
    dtypes = [str(df[c].dtype) for c in df.columns]
    miss = [float(df[c].isna().mean()) for c in df.columns]

    # lightweight stats for numeric cols
    stats = []
    for c in df.columns:
        s = df[c]
        if pd.api.types.is_numeric_dtype(s):
            try:
                stats.append({
                    "c": str(c),
                    "m": float(s.dropna().astype(float).mean()) if s.dropna().shape[0] else None,
                    "sd": float(s.dropna().astype(float).std()) if s.dropna().shape[0] else None,
                })
            except Exception:
                stats.append({"c": str(c), "m": None, "sd": None})

    payload = {"cols": cols, "dtypes": dtypes, "miss": miss, "stats": stats[:50]}
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()
