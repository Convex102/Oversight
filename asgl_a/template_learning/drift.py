from __future__ import annotations

from typing import List, Tuple
import math


def _jaccard(a: List[str], b: List[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    return len(sa & sb) / max(1, len(sa | sb))


def drift_score(tokens_old: List[str], tokens_new: List[str], shape_old: Tuple[int,int], shape_new: Tuple[int,int]) -> float:
    """Return a drift score in [0,1], where higher means *more* drift."

    Drift combines:
    - 1 - token Jaccard similarity
    - shape ratio deltas (log-scaled)
    """
    sim = _jaccard(tokens_old, tokens_new)
    token_drift = 1.0 - sim

    # shape drift
    r_old, c_old = max(1, shape_old[0]), max(1, shape_old[1])
    r_new, c_new = max(1, shape_new[0]), max(1, shape_new[1])

    def log_ratio(a, b):
        return abs(math.log((a + 1.0) / (b + 1.0)))

    shape_drift = min(1.0, 0.5 * log_ratio(r_old, r_new) + 0.5 * log_ratio(c_old, c_new))
    return min(1.0, 0.8 * token_drift + 0.2 * shape_drift)
