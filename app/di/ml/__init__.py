"""DI ML Phase 1

This package upgrades the DI layer to use real embedding models + a learned-style
ranking pipeline with confidence calibration, while preserving strict,
abstain-first decisioning and auditability.

Key guarantees:
- No free-form generation is used for canonical outputs.
- All decisions are bounded + evidence-linked.
- If ML is unavailable, deterministic fallbacks are used.
"""

from .embeddings import embed_text, embed_column
from .ranker import rank_candidates
from .calibration import calibrate_confidence
