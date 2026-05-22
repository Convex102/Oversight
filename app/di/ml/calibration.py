from __future__ import annotations

import math
from typing import Any, Dict, Optional


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def calibrate_confidence(
    raw_score: float,
    *,
    template_hit: bool = False,
    drift_flag: bool = False,
    prior_strength: float = 0.0,
) -> float:
    """Calibrate a raw rank score into a probability-like confidence.

    This is a lightweight, stable calibration function (Phase 1).
    Later phases can replace this with isotonic regression / Platt scaling
    trained from pilot telemetry.
    """
    x = 4.2 * (raw_score - 0.5)
    if template_hit:
        x += 0.55
    if drift_flag:
        x -= 0.65
    x += 0.4 * float(prior_strength or 0.0)
    conf = _sigmoid(x)
    # clamp
    if conf < 0.01:
        conf = 0.01
    if conf > 0.995:
        conf = 0.995
    return float(conf)
