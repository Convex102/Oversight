from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Any, List, Tuple
import hashlib
import re
import pandas as pd


_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def _norm(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def _tokenize(text: str) -> List[str]:
    if not text:
        return []
    return [_norm(t) for t in _TOKEN_RE.findall(str(text)) if t]


@dataclass(frozen=True)
class TemplateSignature:
    """A stable-ish signature for matching templates.

    - `fingerprint` is a SHA256 hash of the signature payload.
    - `tokens` are used for fuzzy matching (Jaccard similarity).
    """
    fingerprint: str
    tokens: List[str]
    shape: Tuple[int, int]
    header_tokens: List[str]
    sample_schema: Dict[str, str]


def build_signature(df: pd.DataFrame, asglc_schema: Dict[str, Any] | None = None) -> TemplateSignature:
    if df is None:
        raise ValueError("df is required")

    asglc_schema = asglc_schema or {}
    cols = [str(c) for c in df.columns]
    header_text = " ".join(cols)
    header_tokens = _tokenize(header_text)

    # Sample non-null pattern + inferred dtypes
    sample_schema: Dict[str, str] = {}
    for c in df.columns[:200]:
        s = df[c]
        if pd.api.types.is_numeric_dtype(s):
            sample_schema[str(c)] = "numeric"
        elif pd.api.types.is_datetime64_any_dtype(s):
            sample_schema[str(c)] = "datetime"
        else:
            # try coerce time-ish
            try:
                t = pd.to_datetime(s, errors="coerce")
                if t.notna().mean() > 0.7:
                    sample_schema[str(c)] = "datetime"
                else:
                    sample_schema[str(c)] = "text"
            except Exception:
                sample_schema[str(c)] = "text"

    # include a small content sketch for robustness
    sketch_rows = min(30, len(df))
    sketch = []
    for i in range(sketch_rows):
        row = df.iloc[i]
        nn = int(row.notna().sum())
        sketch.append(str(nn))
    sketch_str = ",".join(sketch)

    roles = asglc_schema.get("roles", {}) if isinstance(asglc_schema, dict) else {}
    role_pairs = []
    if isinstance(roles, dict):
        for k, v in list(roles.items())[:200]:
            role_pairs.append(f"{_norm(str(k))}:{_norm(str(v))}")
    roles_str = "|".join(sorted(role_pairs))

    payload = f"shape={df.shape};headers={_norm(header_text)};sketch={sketch_str};roles={roles_str}"
    fp = hashlib.sha256(payload.encode("utf-8")).hexdigest()

    tokens = sorted(set(header_tokens + _tokenize(sketch_str) + _tokenize(roles_str)))
    return TemplateSignature(
        fingerprint=fp,
        tokens=tokens,
        shape=(int(df.shape[0]), int(df.shape[1])),
        header_tokens=header_tokens,
        sample_schema=sample_schema,
    )
