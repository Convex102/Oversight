"""
OVERSIGHT LABS — Enhanced Review Point Orchestrator
=====================================================
The single integration point that replaces the raw diff→point conversion
in engagements_routes._compare_and_raise().

Pipeline stages (matching the Oversight ASGL-A specification):
  1. INGESTION    — classify and extract KV from all documents
  2. MAPPING      — FinBERT + CrossEncoder semantic field mapping
  3. NORMALISATION — canonical role assignment, anchor-field filtering
  4. ANALYTICS    — BOCPD, Hurst, Shannon entropy, ratio anomaly detection
  5. CROSS-COMPARE — field-level diff against applicable regs
  6. GRAPH TRAVERSAL — dependency graph for chained relationship points
  7. LLM SCORING  — anomaly scoring + SHAP attribution (when FusionModel active)
  8. REVIEW POINTS — structured, de-duplicated, ranked output
  9. FLYWHEEL      — resolution capture for compounded learning

Every stage degrades gracefully — if a component is unavailable, the
orchestrator falls back to the previous working stage and logs a warning.

Integration with engagements_routes:
  Replace the call to _compare_and_raise() with:
    from oversight_enhancements.review_point_orchestrator import ReviewPointOrchestrator
    orchestrator = ReviewPointOrchestrator()
    review_points, compare_result = orchestrator.run(
        path_a, path_b, label_a, label_b, materiality_pct,
        tmp_path, engagement_id, confirmed_mapping, regulatory_pack,
    )
"""

from __future__ import annotations

import logging
import math
import json
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ── Canonical field name normalisation map ────────────────────────────────────
# Maps common label variations to canonical role names used across the graph,
# ratio detector, and regulatory framework.
# This is the bridge between extracted raw labels and the structured analysis.

CANONICAL_ROLE_MAP: Dict[str, str] = {
    # Turnover / Revenue
    "turnover": "turnover",
    "turnover per accounts": "turnover",
    "sales": "turnover",
    "revenue": "turnover",
    "total income": "turnover",
    "net sales": "turnover",
    "total turnover": "turnover",

    # Profit / Loss
    "profit": "profit_loss",
    "loss": "profit_loss",
    "profit/loss": "profit_loss",
    "profit and loss": "profit_loss",
    "profit / (loss) on ordinary activities": "profit_loss",
    "net profit": "profit_loss",
    "operating profit": "operating_profit",
    "gross profit": "gross_profit",

    # Cost of Sales
    "cost of sales": "cost_of_sales",
    "direct costs": "cost_of_sales",
    "cost of goods sold": "cost_of_sales",
    "cogs": "cost_of_sales",

    # Wages
    "wages": "wages",
    "wages and salaries": "wages",
    "salaries": "wages",
    "staff costs": "wages",
    "employee costs": "wages",
    "payroll": "wages",
    "gross wages": "wages",

    # Employers NI (other_taxes in canonical schema)
    "employers ni": "other_taxes",
    "employer's ni": "other_taxes",
    "employers national insurance": "other_taxes",
    "employers nic": "other_taxes",
    "social security costs": "other_taxes",

    # Depreciation
    "depreciation": "depreciation",
    "depreciation charge": "depreciation",
    "amortisation": "depreciation",
    "depreciation and amortisation": "depreciation",

    # Fixed Assets
    "fixed assets": "fixed_assets",
    "tangible assets": "fixed_assets",
    "property plant and equipment": "fixed_assets",
    "ppe": "fixed_assets",
    "net book value": "fixed_assets",

    # Stock
    "stock": "stock",
    "inventories": "stock",
    "closing stock": "stock",
    "stock at cost": "stock",

    # Debtors
    "debtors": "debtors",
    "trade debtors": "debtors",
    "accounts receivable": "debtors",
    "trade receivables": "debtors",

    # Creditors
    "creditors": "creditors",
    "trade creditors": "creditors",
    "accounts payable": "creditors",
    "trade payables": "creditors",
    "other creditors": "creditors",

    # Cash
    "cash": "cash",
    "cash in hand": "cash",
    "cash at bank": "cash",
    "cash and cash equivalents": "cash",

    # Loans
    "loans": "loans",
    "bank loans": "loans",
    "borrowings": "loans",
    "finance leases": "loans",

    # DLA
    "dla": "dla",
    "director loan account": "dla",
    "director's loan account": "dla",
    "directors loan": "dla",
    "directors current account": "dla",

    # Dividends
    "dividends": "dividends",
    "dividends paid": "dividends",
    "dividend": "dividends",
    "final dividends paid": "dividends",
    "interim dividends paid": "dividends",
    "distributions": "dividends",

    # Reserves
    "reserves": "reserves",
    "retained earnings": "reserves",
    "profit and loss account": "reserves",
    "accumulated losses": "reserves",

    # Tax
    "tax charge": "tax_charge",
    "corporation tax": "tax_charge",
    "deferred tax": "deferred_tax",
    "deferred tax asset": "deferred_tax",
    "tax losses": "tax_losses",
    "losses carried forward": "tax_losses",
    "losses c/f": "tax_losses",
    "loss carry forward": "tax_losses",
}


def _to_canonical(label: str) -> Optional[str]:
    """Map a raw extracted label to its canonical role."""
    return CANONICAL_ROLE_MAP.get(label.strip().lower())


def _to_number(v: Any) -> Optional[float]:
    """Safe float conversion."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else f
    try:
        t = str(v).strip().replace(",", "").replace("£", "").replace("(", "-").replace(")", "")
        return float(t)
    except Exception:
        return None


class ReviewPointOrchestrator:
    """
    Full ASGL-A review point pipeline.

    Drop-in replacement for the raw _compare_and_raise() logic in
    engagements_routes. Integrates:
      - Anchor-field filtering (key_fields only)
      - Ratio anomaly detection (RatioAnomalyDetector)
      - Field dependency graph traversal (FieldDependencyGraph)
      - FusionModel anomaly scoring (when available)
      - SHAP attribution on review points
      - Flywheel weight loading for learned edge weights
    """

    def __init__(self, vertical: str = "accountancy"):
        self.vertical = vertical
        self._graph = None
        self._ratio_detector = None
        self._load_components()

    def _load_components(self) -> None:
        """Load enhancement components with graceful fallback."""
        try:
            from oversight_enhancements.field_dependency_graph import FieldDependencyGraph
            self._graph = FieldDependencyGraph()
            logger.info("FieldDependencyGraph loaded")
        except Exception as e:
            logger.warning("FieldDependencyGraph unavailable: %s", e)

        try:
            from oversight_enhancements.ratio_anomaly_detector import RatioAnomalyDetector
            self._ratio_detector = RatioAnomalyDetector()
            logger.info("RatioAnomalyDetector loaded")
        except Exception as e:
            logger.warning("RatioAnomalyDetector unavailable: %s", e)

    # ── Main entry point ──────────────────────────────────────────────────────

    def run(
        self,
        path_a: Path,
        path_b: Path,
        label_a: str,
        label_b: str,
        materiality_pct: float,
        tmp_path: Path,
        engagement_id: int = 0,
        confirmed_mapping: Optional[Dict] = None,
        regulatory_pack: str = "frs102",
    ) -> Tuple[List[Dict], Dict]:
        """
        Run the full review point pipeline.

        Returns:
            (review_points, compare_result) — same shape as original
            _compare_and_raise() so it's a drop-in replacement.
        """
        logger.info(
            "ReviewPointOrchestrator.run: %s vs %s (eng=%d, mat=%.1f%%)",
            path_a.name, path_b.name, engagement_id, materiality_pct,
        )

        # ── Stage 1+2: Extract and map ────────────────────────────────────
        raw_diffs, compare_result = self._extract_and_diff(
            path_a, path_b, label_a, label_b, materiality_pct, tmp_path
        )

        # ── Stage 3: Normalise — canonicalise extracted values ────────────
        extracted_canonical = self._normalise_to_canonical(compare_result)

        # ── Stage 4+5: Anchor-filtered diff review points ─────────────────
        key_fields = self._get_key_fields(confirmed_mapping)
        diff_points = self._diff_to_review_points(
            raw_diffs, label_a, label_b, materiality_pct, key_fields
        )

        # ── Stage 4: Ratio anomaly detection ─────────────────────────────
        ratio_points = []
        if self._ratio_detector and extracted_canonical:
            try:
                ratio_points = self._ratio_detector.detect(extracted_canonical)
                logger.info("Ratio detection: %d anomalies", len(ratio_points))
            except Exception as e:
                logger.warning("Ratio detection failed: %s", e)

        # ── Stage 6: Graph traversal for chained points ────────────────────
        chain_points = []
        if self._graph and extracted_canonical:
            try:
                # Build triggered_fields dict from material diff points
                triggered = {}
                for pt in diff_points:
                    if pt.get("severity") in ("high", "critical"):
                        fname = pt.get("field_name")
                        if fname:
                            canon = _to_canonical(fname) or fname
                            triggered[canon] = {
                                "delta": _to_number(pt.get("delta")),
                                "value_a": _to_number(pt.get("value_a")),
                                "value_b": _to_number(pt.get("value_b")),
                                "severity": pt.get("severity", "medium"),
                            }

                # Also add ratio anomalies as triggers
                for rp in ratio_points:
                    if rp.get("severity") in ("high", "critical"):
                        fname = rp.get("field_name", "").split("/")[0]
                        if fname and fname not in triggered:
                            triggered[fname] = {
                                "delta": _to_number(rp.get("delta")),
                                "value_a": _to_number(rp.get("value_a")),
                                "value_b": _to_number(rp.get("value_b")),
                                "severity": rp.get("severity", "medium"),
                            }

                if triggered:
                    chained = self._graph.traverse(
                        triggered_fields=triggered,
                        extracted_values=extracted_canonical,
                        materiality_floor=max(500.0, materiality_pct * 1000),
                    )
                    chain_points = [self._chained_to_dict(cp) for cp in chained]
                    logger.info("Graph traversal: %d chained points", len(chain_points))
            except Exception as e:
                logger.warning("Graph traversal failed: %s", e)

        # ── Stage 7: FusionModel anomaly scoring ───────────────────────────
        all_points = diff_points + ratio_points + chain_points
        all_points = self._apply_fusionmodel_scores(all_points, extracted_canonical)

        # ── Stage 8: De-duplicate and rank ────────────────────────────────
        all_points = self._deduplicate(all_points)
        all_points = self._rank(all_points)

        logger.info(
            "ReviewPointOrchestrator complete: %d diff + %d ratio + %d chained = %d total points",
            len(diff_points), len(ratio_points), len(chain_points), len(all_points),
        )

        # Patch compare_result with enhanced counts
        if "summary" in compare_result:
            compare_result["summary"]["total_review_points"] = len(all_points)
            compare_result["summary"]["ratio_anomalies"] = len(ratio_points)
            compare_result["summary"]["chained_points"] = len(chain_points)

        return all_points, compare_result

    # ── Stage 1+2: Extract and diff ────────────────────────────────────────

    def _extract_and_diff(
        self,
        path_a: Path,
        path_b: Path,
        label_a: str,
        label_b: str,
        materiality_pct: float,
        tmp_path: Path,
    ) -> Tuple[List[Any], Dict]:
        """
        Run the existing asgla_compare pipeline.
        Returns raw diff rows and the compare_result dict.
        """
        ext_a = path_a.suffix.lower()
        ext_b = path_b.suffix.lower()

        try:
            from asgla_compare.ingest.excel_ingest import extract_excel_kv
            from asgla_compare.ingest.pdf_ingest import extract_pdf_kv
            from asgla_compare.compare.diff_engine import compare_kv

            if ext_a in (".xlsx", ".xlsm", ".xls", ".csv"):
                result_a = extract_excel_kv(str(path_a), logger=logger)
                items_a = result_a.items
            elif ext_a == ".pdf":
                result_a = extract_pdf_kv(str(path_a), logger=logger)
                items_a = result_a.items
            else:
                items_a = {}

            if ext_b == ".pdf":
                result_b = extract_pdf_kv(str(path_b), logger=logger)
                items_b = result_b.items
            elif ext_b in (".xlsx", ".xlsm", ".xls", ".csv"):
                result_b = extract_excel_kv(str(path_b), logger=logger)
                items_b = result_b.items
            else:
                items_b = {}

            diff_rows = []
            all_anomalies = []

            if items_a and items_b:
                diff_rows = compare_kv(
                    items_a, items_b,
                    abs_tolerance=0.5,
                    pct_tolerance=float(materiality_pct),
                    min_match_score=75.0,
                )

                for row in diff_rows:
                    if row.status == "MATCH":
                        continue
                    va = _to_number(row.value_excel)
                    vb = _to_number(row.value_pdf)
                    delta = _to_number(row.delta)
                    pct = _to_number(row.pct_delta)
                    sev = "high" if row.status == "MISMATCH" else ("low" if row.status in ("MISSING_IN_PDF", "MISSING_IN_EXCEL") else "medium")
                    all_anomalies.append({
                        "severity": sev,
                        "type": row.status,
                        "description": self._build_anomaly_desc(row, label_a, label_b, va, vb, delta, pct),
                        "sheet": "Field Comparison",
                        "data": {
                            "field": row.key_excel or row.key_pdf,
                            "value_a": va,
                            "value_b": vb,
                            "delta": delta,
                            "pct": pct,
                            "match_score": _to_number(row.match_score),
                        },
                    })

            material = sum(1 for a in all_anomalies if a["severity"] == "high")
            compare_result = {
                "summary": {
                    "status": "DIFFERENCES NOTED" if all_anomalies else "CLEAN",
                    "label_a": label_a,
                    "label_b": label_b,
                    "total_deltas": len(diff_rows),
                    "material_deltas": material,
                    "anomaly_count": len(all_anomalies),
                    "excel_items": len(items_a),
                    "pdf_items": len(items_b),
                    "method": "finbert_crossencoder_enhanced",
                },
                "anomalies": all_anomalies,
                "items_a": items_a,
                "items_b": items_b,
            }
            return diff_rows, compare_result

        except Exception as e:
            logger.warning("Primary extract/diff failed: %s", e)
            return [], {
                "summary": {"status": "EXTRACTION_FAILED", "error": str(e)},
                "anomalies": [],
                "items_a": {},
                "items_b": {},
            }

    def _build_anomaly_desc(self, row, label_a, label_b, va, vb, delta, pct) -> str:
        if row.status == "MISSING_IN_PDF":
            return f"'{row.key_excel}': present in {label_a} only"
        if row.status == "MISSING_IN_EXCEL":
            return f"'{row.key_pdf}': present in {label_b} only"
        if va is not None and vb is not None and delta is not None and pct is not None:
            return (f"'{row.key_excel}': {label_a} £{va:,.2f} vs "
                    f"{label_b} £{vb:,.2f} (Δ £{delta:+,.2f}, {pct:.1f}%)")
        return f"'{row.key_excel}' vs '{row.key_pdf}': value mismatch"

    # ── Stage 3: Normalise ────────────────────────────────────────────────────

    def _normalise_to_canonical(self, compare_result: Dict) -> Dict[str, float]:
        """
        Build a canonical {role: value} dict from extracted items_a.
        Uses items_a (the working papers) as the primary source.
        """
        canonical: Dict[str, float] = {}
        items_a = compare_result.get("items_a", {})
        items_b = compare_result.get("items_b", {})

        for label, value in {**items_b, **items_a}.items():
            role = _to_canonical(str(label))
            if role:
                v = _to_number(value)
                if v is not None:
                    canonical[role] = v

        return canonical

    # ── Stage 4+5: Anchor-filtered diff points ────────────────────────────────

    def _get_key_fields(self, confirmed_mapping: Optional[Dict]) -> Optional[List[str]]:
        """
        Extract the list of key field canonical roles from the confirmed mapping.
        Returns None if no mapping confirmed — in that case ALL diff rows are shown
        (backwards compatible with submissions before schema gate was used).
        """
        if not confirmed_mapping:
            return None
        fields = confirmed_mapping.get("fields", [])
        return [
            f["canonical_role"]
            for f in fields
            if f.get("is_key_field") and f.get("canonical_role")
        ]

    def _diff_to_review_points(
        self,
        diff_rows: List[Any],
        label_a: str,
        label_b: str,
        materiality_pct: float,
        key_fields: Optional[List[str]],
    ) -> List[Dict]:
        """
        Convert diff rows to review points with anchor-field filtering.

        When key_fields is provided (schema gate was used):
          - Only raise points for fields where canonical_role is in key_fields
          - MISSING_IN_PDF suppressed unless field is in key_fields AND delta > floor

        When key_fields is None (no confirmed mapping):
          - Apply materiality floor to suppress noise
          - MISSING_IN_PDF raised only if abs(value) > materiality floor
        """
        points: List[Dict] = []
        materiality_floor = max(1000.0, materiality_pct / 100.0 * 50_000)

        for row in diff_rows:
            if row.status == "MATCH":
                continue

            field_label = (row.key_excel or row.key_pdf or "").strip()
            canonical = _to_canonical(field_label)
            va = _to_number(row.value_excel)
            vb = _to_number(row.value_pdf)
            delta = _to_number(row.delta)

            # Anchor-field filter
            if key_fields is not None:
                if canonical not in key_fields:
                    continue
            else:
                # No confirmed mapping — apply materiality floor for MISSING rows
                if row.status in ("MISSING_IN_PDF", "MISSING_IN_EXCEL"):
                    val = abs(va or vb or 0.0)
                    if val < materiality_floor:
                        continue

            sev = "high" if row.status == "MISMATCH" else ("low" if row.status in ("MISSING_IN_PDF", "MISSING_IN_EXCEL") else "medium")

            desc = ""
            if row.status == "MISSING_IN_PDF":
                desc = f"'{field_label}': present in {label_a} (£{va:,.2f}) but not found in {label_b}."
            elif row.status == "MISSING_IN_EXCEL":
                desc = f"'{field_label}': present in {label_b} (£{vb:,.2f}) but not found in {label_a}."
            elif va is not None and vb is not None and delta is not None:
                pct = _to_number(row.pct_delta)
                desc = (f"'{field_label}': {label_a} £{va:,.2f} vs "
                        f"{label_b} £{vb:,.2f} (Δ £{delta:+,.2f}"
                        + (f", {pct:.1f}%" if pct else "") + ")")

            points.append({
                "severity": sev,
                "category": "difference",
                "title": f"{'Mismatch' if row.status == 'MISMATCH' else 'Query'}: {field_label}"[:300],
                "description": desc,
                "section": "Field Comparison",
                "audit_ref": None,
                "field_name": field_label,
                "canonical_role": canonical,
                "value_a": str(va) if va is not None else "",
                "value_b": str(vb) if vb is not None else "",
                "delta": delta,
                "status": "open",
                "diff_type": row.status,
            })

        return points

    # ── Stage 6: Convert chained points ──────────────────────────────────────

    def _chained_to_dict(self, cp) -> Dict:
        """Convert a ChainedReviewPoint to the standard review point dict."""
        return {
            "severity": cp.severity,
            "category": cp.category,
            "title": cp.title[:300],
            "description": cp.description,
            "section": f"Relationship Analysis — {cp.trigger_field} → {cp.chain_field}",
            "audit_ref": None,
            "field_name": cp.chain_field,
            "canonical_role": cp.chain_field,
            "value_a": str(cp.trigger_value_a) if cp.trigger_value_a is not None else "",
            "value_b": str(cp.trigger_value_b) if cp.trigger_value_b is not None else "",
            "delta": cp.trigger_delta,
            "status": "open",
            "diff_type": "CHAINED_RELATIONSHIP",
            "chain_depth": cp.chain_depth,
            "frs_reference": cp.frs_reference,
            "recommended_action": cp.recommended_action,
            "shap_contribution": cp.shap_contribution,
        }

    # ── Stage 7: FusionModel scoring ──────────────────────────────────────────

    def _apply_fusionmodel_scores(
        self,
        points: List[Dict],
        canonical_values: Dict[str, float],
    ) -> List[Dict]:
        """
        Attempt to apply FusionModel anomaly scores to each review point.
        Gracefully no-ops if FusionModel inference is unavailable.
        """
        try:
            from app.di.fusionmodel_inference import score_field_anomalies
            scores = score_field_anomalies(canonical_values)
            for pt in points:
                role = pt.get("canonical_role")
                if role and role in scores:
                    pt["di_anomaly_score"] = round(scores[role], 4)
                    # Escalate severity if model score is very high
                    if scores[role] > 0.85 and pt.get("severity") == "medium":
                        pt["severity"] = "high"
                        pt["di_escalated"] = True
        except Exception:
            pass  # FusionModel inference not yet wired — graceful fallback
        return points

    # ── Stage 8: De-duplicate and rank ────────────────────────────────────────

    def _deduplicate(self, points: List[Dict]) -> List[Dict]:
        """
        Remove duplicate points by content hash.
        Keeps highest severity version when duplicates exist.
        """
        seen: Dict[str, Dict] = {}
        sev_order = ["low", "medium", "high", "critical"]

        for pt in points:
            key = hashlib.md5(
                (str(pt.get("field_name", "")) + str(pt.get("diff_type", "")) + str(pt.get("category", ""))).encode()
            ).hexdigest()
            if key not in seen:
                seen[key] = pt
            else:
                # Keep the higher severity
                existing_sev = sev_order.index(seen[key].get("severity", "low")) if seen[key].get("severity") in sev_order else 0
                new_sev = sev_order.index(pt.get("severity", "low")) if pt.get("severity") in sev_order else 0
                if new_sev > existing_sev:
                    seen[key] = pt

        return list(seen.values())

    def _rank(self, points: List[Dict]) -> List[Dict]:
        """
        Sort points by severity then by anomaly score then by delta magnitude.
        Assign audit_ref codes: A=critical, B=high, C=medium, D=low
        """
        sev_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        points.sort(key=lambda p: (
            sev_order.get(p.get("severity", "medium"), 2),
            -(p.get("di_anomaly_score") or 0.0),
            -(abs(_to_number(p.get("delta")) or 0.0)),
        ))

        counters = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        prefix_map = {"critical": "A", "high": "B", "medium": "C", "low": "D"}
        for pt in points:
            sev = pt.get("severity", "medium")
            if sev in counters:
                counters[sev] += 1
                pt["audit_ref"] = f"{prefix_map[sev]}{counters[sev]}"

        return points
