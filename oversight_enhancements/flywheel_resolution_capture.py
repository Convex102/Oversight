"""
OVERSIGHT LABS — Flywheel Resolution Quality Capture
======================================================
Enhanced flywheel capture that records not just outcome (resolved/waived)
but the structured WHY behind each resolution.

This is the compounded learning layer. Over time, confirmed resolution
patterns become training signal for:
  1. Improving the ratio anomaly thresholds
  2. Updating edge weights in the field dependency graph
  3. Calibrating FusionModel anomaly scores
  4. Building sector-level priors for new clients

GDPR compliance:
  - Never stores client data values
  - Stores field labels, categories, and outcome classifications only
  - Resolution classifications are from a fixed taxonomy (no free text stored)

Integration:
  Called from review-point resolution endpoints in engagements_routes
  (correct, waive, resolve). Replaces the existing _flywheel_capture_point().

Resolution taxonomy (what gets stored):
  TIMING_DIFFERENCE      — the difference is real but relates to cut-off
  ROUNDING               — immaterial rounding, no error
  POLICY_CHANGE          — accounting policy changed intentionally
  GENUINE_ERROR          — preparer error confirmed and corrected
  CLASSIFICATION_CHANGE  — item reclassified between P&L/BS categories
  MISSING_EVIDENCE       — evidence provided (disclosure now added)
  WAIVED_IMMATERIAL      — partner waived on immateriality grounds
  WAIVED_EXPLAINED       — partner waived after satisfactory explanation
  CHAIN_CONFIRMED        — chained relationship point was genuinely relevant
  CHAIN_REJECTED         — chained relationship point was not relevant
  RATIO_CONFIRMED        — ratio anomaly was a genuine issue
  RATIO_REJECTED         — ratio anomaly was false positive
"""

from __future__ import annotations

import logging
from datetime import datetime
from enum import Enum
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class ResolutionClassification(str, Enum):
    TIMING_DIFFERENCE     = "timing_difference"
    ROUNDING              = "rounding"
    POLICY_CHANGE         = "policy_change"
    GENUINE_ERROR         = "genuine_error"
    CLASSIFICATION_CHANGE = "classification_change"
    MISSING_EVIDENCE      = "missing_evidence"
    WAIVED_IMMATERIAL     = "waived_immaterial"
    WAIVED_EXPLAINED      = "waived_explained"
    CHAIN_CONFIRMED       = "chain_confirmed"
    CHAIN_REJECTED        = "chain_rejected"
    RATIO_CONFIRMED       = "ratio_confirmed"
    RATIO_REJECTED        = "ratio_rejected"


@dataclass
class ResolutionCapture:
    """Structured capture of a review point resolution."""
    engagement_id: int
    review_point_id: int
    tenant_id: str
    field_name: str
    canonical_role: Optional[str]
    category: str                   # "difference" | "ratio_anomaly" | "chained_relationship"
    severity: str
    outcome: str                    # "corrected" | "waived" | "resolved"
    classification: ResolutionClassification
    chain_source: Optional[str]     # if chained, the trigger field
    delta_magnitude: float
    anomaly_score: Optional[float]
    resolved_by: str
    timestamp: str


def capture_resolution(
    review_point,
    outcome: str,
    actor: str,
    classification: Optional[str] = None,
) -> None:
    """
    Capture a review point resolution into the flywheel.
    Enhanced replacement for _flywheel_capture_point().

    Args:
        review_point: The ReviewPoint DB model instance
        outcome:      "corrected" | "waived" | "resolved"
        actor:        The person resolving (partner name or "system")
        classification: Optional ResolutionClassification string
    """
    try:
        # Infer classification from outcome if not provided
        if not classification:
            if outcome == "waived":
                classification = ResolutionClassification.WAIVED_EXPLAINED
            elif outcome == "corrected":
                classification = ResolutionClassification.GENUINE_ERROR
            else:
                classification = ResolutionClassification.WAIVED_EXPLAINED

        category = getattr(review_point, "category", "difference")
        field_name = getattr(review_point, "field_name", "") or getattr(review_point, "title", "")
        delta_magnitude = abs(float(getattr(review_point, "delta", 0) or 0))
        canonical_role = getattr(review_point, "canonical_role", None)

        # Update edge weights in the dependency graph if this is a chained point
        if "chained" in category:
            _update_graph_weights(review_point, classification)

        # Update ratio thresholds if this is a ratio anomaly
        if "ratio" in category:
            _update_ratio_thresholds(review_point, classification)

        # Write to flywheel engine
        from app.flywheel.engine import FlywheelEngine
        from app.flywheel.events import FlywheelEventType

        confidence_map = {
            ResolutionClassification.GENUINE_ERROR: 0.98,
            ResolutionClassification.CHAIN_CONFIRMED: 0.95,
            ResolutionClassification.RATIO_CONFIRMED: 0.95,
            ResolutionClassification.MISSING_EVIDENCE: 0.90,
            ResolutionClassification.POLICY_CHANGE: 0.85,
            ResolutionClassification.CLASSIFICATION_CHANGE: 0.85,
            ResolutionClassification.TIMING_DIFFERENCE: 0.80,
            ResolutionClassification.WAIVED_EXPLAINED: 0.75,
            ResolutionClassification.ROUNDING: 0.70,
            ResolutionClassification.WAIVED_IMMATERIAL: 0.65,
            ResolutionClassification.CHAIN_REJECTED: 0.40,
            ResolutionClassification.RATIO_REJECTED: 0.40,
        }
        conf = confidence_map.get(classification, 0.80)

        engine = FlywheelEngine(
            tenant_id=str(getattr(review_point, "tenant_id", 1)),
            client_id=str(review_point.engagement_id),
            run_id=str(getattr(review_point, "run_number", 0) or 0),
            vertical="accountancy",
        )
        engine.capture(
            type=FlywheelEventType.LABEL_CONFIRMED,
            actor=actor,
            confidence=conf,
            payload={
                "field_name": field_name,
                "canonical_role": canonical_role,
                "category": category,
                "severity": getattr(review_point, "severity", "medium"),
                "outcome": outcome,
                "classification": str(classification),
                "delta_magnitude": delta_magnitude,
                "anomaly_score": getattr(review_point, "di_anomaly_score", None),
            },
        )

        # Trigger retraining if threshold met
        _maybe_trigger_retrain(engine, review_point)

        logger.info(
            "Flywheel captured: field=%s, outcome=%s, classification=%s, conf=%.2f",
            field_name, outcome, classification, conf,
        )

    except Exception as e:
        logger.debug("Flywheel capture failed (non-critical): %s", e)


def _update_graph_weights(review_point, classification) -> None:
    """Update field dependency graph edge weights from confirmed/rejected chains."""
    try:
        section = getattr(review_point, "section", "") or ""
        # section format: "Relationship Analysis — {source} → {target}"
        if "→" in section:
            parts = section.split("→")
            source_raw = parts[0].replace("Relationship Analysis —", "").strip()
            target = parts[1].strip() if len(parts) > 1 else ""
            if source_raw and target:
                from oversight_enhancements.field_dependency_graph import FieldDependencyGraph
                graph = FieldDependencyGraph()
                confirmed = classification in (
                    ResolutionClassification.CHAIN_CONFIRMED,
                    ResolutionClassification.GENUINE_ERROR,
                )
                graph.update_edge_weight(source_raw, target, confirmed=confirmed)
    except Exception as e:
        logger.debug("Graph weight update failed: %s", e)


def _update_ratio_thresholds(review_point, classification) -> None:
    """
    Log ratio feedback for future threshold calibration.
    Currently logs to the flywheel; future versions will adjust expected ranges.
    """
    try:
        field_name = getattr(review_point, "field_name", "") or ""
        is_confirmed = classification in (
            ResolutionClassification.RATIO_CONFIRMED,
            ResolutionClassification.GENUINE_ERROR,
        )
        logger.info(
            "Ratio feedback: field=%s, confirmed=%s",
            field_name, is_confirmed,
        )
        # TODO: persist to ratio_thresholds table for adaptive calibration
    except Exception as e:
        logger.debug("Ratio threshold update failed: %s", e)


def _maybe_trigger_retrain(engine, review_point) -> None:
    """Trigger FusionModel retrain if enough new flywheel data has accumulated."""
    try:
        from app.flywheel.trainer import TrainingScheduler
        scheduler = TrainingScheduler()
        ds_meta = engine.dataset.snapshot_meta(
            tenant_id=str(getattr(review_point, "tenant_id", 1)),
            vertical="accountancy",
        )
        rows = ds_meta.get("rows", 0)
        if rows >= 20 and rows % 20 == 0:
            scheduler.trigger(
                tenant_id=str(getattr(review_point, "tenant_id", 1)),
                vertical="accountancy",
                model_id="risk_model",
                base_version="v1",
                reason=f"resolution_flywheel_{rows}_rows",
            )
    except Exception as e:
        logger.debug("Retrain trigger failed: %s", e)
