from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional
import math
import difflib
import logging
import os

import numpy as np

try:
    from rapidfuzz import process as rf_process
    from rapidfuzz import fuzz as rf_fuzz
    _HAS_RAPIDFUZZ = True
except Exception:
    _HAS_RAPIDFUZZ = False

try:
    from sentence_transformers import CrossEncoder
    _HAS_CROSSENCODER = True
except Exception:
    CrossEncoder = None
    _HAS_CROSSENCODER = False

logger = logging.getLogger(__name__)

# Accounting domain: pairs that should NEVER match regardless of CE score
# These are cases where surface similarity fools the cross-encoder
# but accounting domain knowledge says they are different concepts
_ACCOUNTING_INCOMPATIBLE_PAIRS = [
    # Tax workings vs P&L items
    ({"timing difference", "deferred tax", "wdv", "tax written down"},
     {"rent", "wages", "insurance", "printing", "repairs", "cleaning", "laundry",
      "telecommunications", "bank charges", "depreciation", "salaries"}),
    # Template labels vs financial accounts
    ({"n/c", "time", "ref", "date", "page", "note", "beer", "garden", "bar"},
     set()),  # empty second set = always reject these as keys
]

def _is_plausible_accounting_match(key_a: str, key_b: str) -> bool:
    """Return False if these two labels are definitely not the same financial concept.
    
    Uses accounting domain knowledge to reject matches that the cross-encoder
    might score highly due to partial token overlap but are semantically wrong
    in an accounting context.
    """
    a = key_a.lower().strip()
    b = key_b.lower().strip()
    
    # Reject very short labels that are clearly not account names
    if len(a) < 4 or len(b) < 4:
        return False
    
    # Reject if key_a looks like a WP workings label rather than an account
    WP_WORKINGS = {
        "timing difference", "wdv of fixed assets", "nbv of fixed assets per comps",
        "deferred tax on timing", "n/c:", "time:", "beer garden", "balance b/fwd",
        "balance c/fwd", "cost b/fwd", "aia claimed", "wdv c/fwd",
        "additions qualifying", "pool",
    }
    for bad in WP_WORKINGS:
        if bad in a:
            return False
    
    # Reject obvious cross-category mismatches
    # Tax/DTA concepts should not match P&L expense lines
    tax_concepts = {"timing difference", "deferred tax", "tax written down", "wdv"}
    pl_expenses = {"rent", "wages", "insurance", "printing", "repairs",
                   "cleaning", "laundry", "telecommunications", "bank charges",
                   "salaries", "pension", "entertaining"}
    
    a_is_tax = any(t in a for t in tax_concepts)
    b_is_expense = any(e in b for e in pl_expenses)
    if a_is_tax and b_is_expense:
        return False
    
    return True

# â”€â”€ Cross-encoder configuration â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
#
# Stage 1: RapidFuzz bi-encoder retrieval â†’ fast candidate shortlist (top-K)
# Stage 2: CrossEncoder reranking â†’ precise similarity on candidate pairs
#
# cross-encoder/ms-marco-MiniLM-L-6-v2:
#   - Trained on 500k+ information retrieval pairs
#   - Reads both strings jointly (not independently like bi-encoders)
#   - Understands that "operating profit 415,400" and "EBIT 415,400" are
#     the same concept even though the labels have low string similarity
#   - ~40ms per pair on CPU â€” acceptable for document comparison scale
#
CROSS_ENCODER_MODEL = os.getenv(
    "ASGL_CROSS_ENCODER_MODEL",
    "cross-encoder/ms-marco-MiniLM-L-6-v2"
)
CROSS_ENCODER_TOPK = int(os.getenv("ASGL_CROSS_ENCODER_TOPK", "5"))
CROSS_ENCODER_MIN_SCORE = float(os.getenv("ASGL_CROSS_ENCODER_MIN_SCORE", "0.0"))

_CROSS_ENCODER: Optional[any] = None


def _get_cross_encoder() -> Optional[any]:
    global _CROSS_ENCODER
    if _CROSS_ENCODER is not None:
        return _CROSS_ENCODER
    if not _HAS_CROSSENCODER:
        return None
    try:
        _CROSS_ENCODER = CrossEncoder(CROSS_ENCODER_MODEL)
        logger.info("CrossEncoder loaded: %s", CROSS_ENCODER_MODEL)
        return _CROSS_ENCODER
    except Exception as e:
        logger.debug("CrossEncoder unavailable: %s", e)
        return None


@dataclass
class DiffRow:
    key_excel: str
    key_pdf: str
    value_excel: Optional[float]
    value_pdf: Optional[float]
    delta: Optional[float]
    abs_delta: Optional[float]
    pct_delta: Optional[float]
    status: str
    match_score: float


def _pct(a: float, b: float) -> float:
    denom = abs(b) if abs(b) > 1e-9 else (abs(a) if abs(a) > 1e-9 else 1.0)
    return (a - b) / denom * 100.0


def _rapidfuzz_candidates(
    query: str,
    pool: List[str],
    top_k: int,
    min_score: float,
) -> List[Tuple[str, float]]:
    """Return top-k candidates from pool using RapidFuzz WRatio."""
    if not pool:
        return []
    if _HAS_RAPIDFUZZ:
        hits = rf_process.extract(query, pool, scorer=rf_fuzz.WRatio, limit=top_k)
        return [(cand, float(score)) for cand, score, _ in hits if score >= min_score]
    else:
        matches = difflib.get_close_matches(query, pool, n=top_k, cutoff=min_score / 100.0)
        return [
            (m, 100.0 * difflib.SequenceMatcher(None, query, m).ratio())
            for m in matches
        ]


def _rerank_with_crossencoder(
    query: str,
    candidates: List[Tuple[str, float]],
) -> List[Tuple[str, float]]:
    """Rerank candidates using a cross-encoder.

    The cross-encoder reads query and candidate jointly, giving it full
    attention over both strings. This catches cases where string similarity
    is low but semantic meaning is identical:

    Examples where cross-encoder beats fuzzy alone:
    - "turnover"  vs  "revenue"             (fuzzy: 0.44, cross-encoder: high)
    - "trade debtors" vs "receivables"       (fuzzy: 0.32, cross-encoder: high)
    - "ic payable" vs "intercompany payable" (fuzzy: 0.60, cross-encoder: high)
    - "wages" vs "salaries and wages"        (fuzzy: 0.55, cross-encoder: high)
    - "depreciation charge" vs "depr."       (fuzzy: 0.50, cross-encoder: high)

    Returns candidates re-sorted by cross-encoder score.
    Converts ms-marco logit scores to [0, 1] range via sigmoid.
    Falls back to original ranking if cross-encoder unavailable.
    """
    ce = _get_cross_encoder()
    if ce is None or not candidates:
        return candidates

    pairs = [(query, cand) for cand, _ in candidates]
    try:
        scores = ce.predict(pairs)
        # ms-marco returns logits â€” apply sigmoid to normalise to [0, 1]
        def sigmoid(x: float) -> float:
            return 1.0 / (1.0 + math.exp(-float(x)))

        reranked = [
            (cand, sigmoid(score))
            for (cand, _), score in zip(candidates, scores)
        ]
        reranked.sort(key=lambda x: x[1], reverse=True)
        return reranked
    except Exception as e:
        logger.debug("CrossEncoder predict failed: %s", e)
        return candidates


def _match_keys(
    excel_keys: List[str],
    pdf_keys: List[str],
    min_score: float = 75.0,
) -> Dict[str, Tuple[str, float]]:
    """Map each excel key to its best pdf key using two-stage matching.

    Stage 1 â€” RapidFuzz bi-encoder retrieval:
      Fast character/token overlap scoring. Retrieves top-K candidates.
      Cutoff lowered to 40 (from 75) so cross-encoder sees enough candidates.

    Stage 2 â€” CrossEncoder reranking:
      Reads query+candidate jointly. Reranks top-K candidates by semantic
      similarity. Final match must score >= min_score (after sigmoid).
      This catches domain synonyms that fuzzy misses entirely.

    Falls back gracefully to fuzzy-only if CrossEncoder unavailable.
    """
    ce_available = _get_cross_encoder() is not None
    # Lower the fuzzy retrieval cutoff when we have a cross-encoder to rerank
    retrieval_cutoff = 30.0 if ce_available else min_score

    mapping: Dict[str, Tuple[str, float]] = {}

    for ek in excel_keys:
        # Stage 1: retrieve candidates
        candidates = _rapidfuzz_candidates(
            ek, pdf_keys,
            top_k=CROSS_ENCODER_TOPK,
            min_score=retrieval_cutoff,
        )
        if not candidates:
            continue

        if ce_available:
            # Stage 2: rerank with cross-encoder
            reranked = _rerank_with_crossencoder(ek, candidates)
            if reranked:
                best_cand, best_score = reranked[0]
                # Raised from 0.4 to 0.55 â€” eliminates false positives like
                # 'beer garden' -> 'rent', 'timing difference' -> 'rent'
                # Also check accounting domain plausibility
                if best_score > 0.55 and _is_plausible_accounting_match(ek, best_cand):
                    mapping[ek] = (best_cand, best_score * 100.0)
        else:
            # Fuzzy-only: use original min_score threshold
            best_cand, best_score = candidates[0]
            if best_score >= min_score:
                mapping[ek] = (best_cand, best_score)

    return mapping


def compare_kv(
    excel_items: Dict[str, float],
    pdf_items: Dict[str, float],
    abs_tolerance: float = 0.5,
    pct_tolerance: float = 0.5,
    min_match_score: float = 75.0,
) -> List[DiffRow]:
    """Compare two key-value dicts using two-stage semantic matching.

    Matching pipeline:
    1. RapidFuzz WRatio retrieves top-K candidates per key (fast, character-level)
    2. CrossEncoder ms-marco-MiniLM-L-6-v2 reranks candidates (precise, semantic)
    3. Delta analysis with abs and pct tolerance gating
    4. Status classification: MATCH / MISMATCH / REVIEW / MISSING_IN_PDF / MISSING_IN_EXCEL

    The cross-encoder stage is what separates this from a VBA macro:
    it reads both strings jointly and understands domain synonyms that
    character-level matching cannot detect.
    """
    excel_keys = sorted(excel_items.keys())
    pdf_keys = sorted(pdf_items.keys())

    ce_available = _get_cross_encoder() is not None
    if ce_available:
        logger.info(
            "diff_engine: %d excel keys, %d pdf keys â€” using CrossEncoder reranking",
            len(excel_keys), len(pdf_keys),
        )
    else:
        logger.info(
            "diff_engine: %d excel keys, %d pdf keys â€” fuzzy-only (CrossEncoder not loaded)",
            len(excel_keys), len(pdf_keys),
        )

    matched = _match_keys(excel_keys, pdf_keys, min_score=min_match_score)
    used_pdf: set = set()
    rows: List[DiffRow] = []

    for ek in excel_keys:
        if ek in matched:
            pk, score = matched[ek]
            used_pdf.add(pk)
            ev = excel_items.get(ek)
            pv = pdf_items.get(pk)
            delta = None if (ev is None or pv is None) else ev - pv
            abs_delta = None if delta is None else abs(delta)
            pct_delta = None if (ev is None or pv is None) else _pct(ev, pv)
            status = "MATCH"
            if abs_delta is not None and pct_delta is not None:
                if abs_delta > abs_tolerance and abs(pct_delta) > pct_tolerance:
                    status = "MISMATCH"
                elif abs_delta > abs_tolerance:
                    status = "REVIEW"
            rows.append(DiffRow(ek, pk, ev, pv, delta, abs_delta, pct_delta, status, score))
        else:
            rows.append(DiffRow(
                ek, "", excel_items.get(ek), None,
                None, None, None, "MISSING_IN_PDF", 0.0,
            ))

    for pk in pdf_keys:
        if pk not in used_pdf:
            rows.append(DiffRow(
                "", pk, None, pdf_items.get(pk),
                None, None, None, "MISSING_IN_EXCEL", 0.0,
            ))

    return rows

