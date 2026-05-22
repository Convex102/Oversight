from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .embeddings import embed_text


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-8))


@dataclass
class Candidate:
    field_id: str
    label: str
    description: str = ""
    prior: float = 0.0


def rank_candidates(
    tenant_id: str,
    column_vec: np.ndarray,
    candidates: Sequence[Candidate],
    *,
    vertical: str = "A",
    top_k: int = 5,
) -> List[Dict[str, Any]]:
    """Rank candidate canonical fields for a column vector.

    This is a learned-representation ranker: embeddings + calibrated combination with priors.
    It remains bounded and auditable.
    """
    scored: List[Tuple[float, Candidate, float]] = []
    for cand in candidates:
        # Embed candidate schema description once per call (cached internally)
        cand_text = f"vertical={vertical};field={cand.label};desc={cand.description}"
        cand_vec = embed_text(cand_text, tenant_id="global", cache=True)
        sim = cosine(column_vec, cand_vec)
        score = 0.82 * sim + 0.18 * float(cand.prior or 0.0)
        scored.append((score, cand, sim))

    scored.sort(key=lambda x: x[0], reverse=True)
    out: List[Dict[str, Any]] = []
    for score, cand, sim in scored[:top_k]:
        out.append(
            {
                "field_id": cand.field_id,
                "label": cand.label,
                "description": cand.description,
                "similarity": round(sim, 6),
                "prior": float(cand.prior or 0.0),
                "score": round(float(score), 6),
            }
        )
    return out
