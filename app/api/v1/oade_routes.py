"""
Oversight Anomaly Detection Engine (OADE)
==========================================
Ensemble anomaly detection + cross-field rules engine.

Ensemble: IsolationForest + ECOD + COPOD + HBOS + Autoencoder (MLP) with neural fusion.
Rules engine: cross-field YAML rules with restricted expression evaluation.

Routes (prefix /api/v1/quality):
  POST /population-anomaly   — run ensemble detection on a completed job's canonical data
  POST /rules/validate       — run cross-field rules against a job's canonical data
  GET  /rules/default        — get the current default ruleset
  PUT  /rules/default        — save a ruleset

Author: Oversight Labs Limited
Built with: DeepSeek (initial), fixed and integrated by Oversight Labs
"""
from __future__ import annotations

import json
import logging
import operator
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Body
from sqlalchemy.orm import Session

from app.deps import get_tenant
from app.db.session import get_db
from app.db.models import Job, Tenant
from app.storage import storage
from app.governance.audit import AuditLog

logger = logging.getLogger("Oversight.OADE")

# ── ML imports (graceful fallback) ───────────────────────────────────────────

_ML_OK = False
try:
    from sklearn.ensemble import IsolationForest
    from sklearn.neural_network import MLPRegressor
    _SKLEARN_OK = True
except ImportError:
    _SKLEARN_OK = False
    logger.warning("scikit-learn not installed. Fallback detection only.")

try:
    from pyod.models.ecod import ECOD
    from pyod.models.copod import COPOD
    from pyod.models.hbos import HBOS
    _PYOD_OK = True
except ImportError:
    _PYOD_OK = False
    logger.warning("pyod not installed. Ensemble reduced to IsolationForest only.")

_ML_OK = _SKLEARN_OK


# ── Autoencoder ───────────────────────────────────────────────────────────────

class AutoencoderDetector:
    """MLP-based autoencoder for reconstruction-error anomaly scores."""

    def __init__(self, hidden_units: List[int] = [64, 32, 64], epochs: int = 50):
        self._fitted = False
        if _SKLEARN_OK:
            self.model = MLPRegressor(
                hidden_layer_sizes=tuple(hidden_units),
                activation="relu",
                solver="adam",
                max_iter=epochs,
                random_state=42,
            )
        else:
            self.model = None

    def fit(self, X: np.ndarray) -> None:
        if self.model is None:
            return
        self.model.fit(X, X)
        self._fitted = True

    def predict(self, X: np.ndarray) -> np.ndarray:
        if not self._fitted or self.model is None:
            return np.zeros(X.shape[0])
        recon = self.model.predict(X)
        mse = np.mean((X - recon) ** 2, axis=1)
        scores = 1.0 / (1.0 + np.exp(-((mse - mse.mean()) / (mse.std() + 1e-6))))
        return scores


# ── Ensemble ──────────────────────────────────────────────────────────────────

class EnsembleAnomalyDetector:
    """Hierarchical ensemble with optional neural fusion.

    Falls back gracefully if pyod is not available — uses IsolationForest
    + Autoencoder only and averages scores.
    """

    def __init__(self):
        self.detectors: Dict[str, Any] = {}
        self._fusion: Optional[Any] = None
        self._fitted = False

    def _build_detectors(self) -> Dict[str, Any]:
        dets: Dict[str, Any] = {}
        if _SKLEARN_OK:
            dets["isolation_forest"] = IsolationForest(contamination="auto", random_state=42)
            dets["autoencoder"] = AutoencoderDetector()
        if _PYOD_OK:
            dets["ecod"] = ECOD(contamination=0.1)
            dets["copod"] = COPOD(contamination=0.1)
            dets["hbos"] = HBOS(contamination=0.1)
        return dets

    def fit(self, X: np.ndarray) -> None:
        if X.shape[0] < 5 or X.shape[1] < 1:
            logger.warning("Dataset too small for ensemble detection (%s rows, %s cols)", *X.shape)
            return
        self.detectors = self._build_detectors()
        for name, det in self.detectors.items():
            try:
                det.fit(X)
            except Exception as e:
                logger.warning("Detector %s fit failed: %s", name, e)
        # Fusion layer
        if _SKLEARN_OK and len(self.detectors) >= 2:
            try:
                base = self._predict_base(X)
                self._fusion = MLPRegressor(
                    hidden_layer_sizes=[16, 8], max_iter=200, random_state=42
                )
                self._fusion.fit(base, np.zeros(X.shape[0]))
            except Exception as e:
                logger.warning("Fusion NN fit failed: %s", e)
                self._fusion = None
        self._fitted = True

    def _predict_base(self, X: np.ndarray) -> np.ndarray:
        scores = []
        for name, det in self.detectors.items():
            try:
                if name == "autoencoder":
                    s = det.predict(X)
                elif hasattr(det, "decision_function"):
                    raw = det.decision_function(X)
                    # Normalise to 0-1
                    s = (raw - raw.min()) / (raw.ptp() + 1e-8)
                else:
                    s = np.zeros(X.shape[0])
                scores.append(s)
            except Exception as e:
                logger.warning("Detector %s predict failed: %s", name, e)
        if not scores:
            return np.zeros((X.shape[0], 1))
        return np.column_stack(scores)

    def predict(self, X: np.ndarray) -> np.ndarray:
        if not self._fitted or not self.detectors:
            return np.zeros(X.shape[0])
        base = self._predict_base(X)
        if self._fusion is not None:
            try:
                fused = self._fusion.predict(base)
                return np.clip(fused, 0, 1)
            except Exception:
                pass
        # Fallback: mean of base scores
        return np.clip(base.mean(axis=1), 0, 1)


# ── Rules engine ──────────────────────────────────────────────────────────────
#
# SECURITY NOTE: The original implementation used eval() with
# {"__builtins__": {}} which is NOT safe against determined bypass.
# This version uses a restricted AST evaluator instead.
# For production, rules should be validated against a whitelist schema
# before storage.
#

_OPS = {
    "==": operator.eq,
    "!=": operator.ne,
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
}

def _safe_eval(condition: str, record: Dict[str, Any]) -> bool:
    """Evaluate a simple comparison rule safely without eval().

    Supports: field_name op value  (e.g. "net_assets < 0")
    Supports: field_a op field_b   (e.g. "total_assets > total_liabilities")
    Supports: 'and'/'or' joining two comparisons.
    """
    condition = condition.strip()

    # Handle 'and'/'or' compound conditions
    for join in (" and ", " or "):
        if join in condition.lower():
            parts = re.split(join, condition, flags=re.IGNORECASE, maxsplit=1)
            if len(parts) == 2:
                left = _safe_eval(parts[0].strip(), record)
                right = _safe_eval(parts[1].strip(), record)
                return (left and right) if "and" in join else (left or right)

    # Single comparison: lhs op rhs
    for op_str, op_fn in sorted(_OPS.items(), key=lambda x: -len(x[0])):
        if op_str in condition:
            idx = condition.index(op_str)
            lhs_str = condition[:idx].strip()
            rhs_str = condition[idx + len(op_str):].strip()

            def _resolve(s: str) -> Any:
                if s in record:
                    return record[s]
                try:
                    return float(s)
                except ValueError:
                    return s.strip("'\"")

            lhs = _resolve(lhs_str)
            rhs = _resolve(rhs_str)
            try:
                return bool(op_fn(float(lhs), float(rhs)))
            except (TypeError, ValueError):
                try:
                    return bool(op_fn(lhs, rhs))
                except Exception:
                    return False
    return False


class CrossFieldRulesEngine:
    """Evaluate cross-field validation rules against records.

    Rules format (JSON or loaded from YAML):
    [
      {
        "name": "negative_net_assets",
        "condition": "net_assets < 0",
        "message": "Net assets are negative",
        "severity": "critical"
      }
    ]
    """

    def __init__(self, rules: List[Dict[str, Any]]):
        self.rules = rules

    def validate(self, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        violations = []
        for idx, rec in enumerate(records):
            record_violations = []
            for rule in self.rules:
                condition = rule.get("condition", "")
                try:
                    violated = _safe_eval(condition, rec)
                except Exception as e:
                    logger.debug("Rule eval error for '%s': %s", condition, e)
                    violated = False
                if violated:
                    record_violations.append({
                        "rule": rule.get("name", "unnamed"),
                        "message": rule.get("message", f"Rule violated: {condition}"),
                        "severity": rule.get("severity", "warning"),
                    })
            if record_violations:
                violations.append({
                    "record_index": idx,
                    "record_id": rec.get("id") or rec.get("_id") or idx,
                    "violations": record_violations,
                })
        return violations


# ── Default ruleset store (in-memory, persisted to storage if available) ─────

_DEFAULT_RULES_KEY = "quality/rules/default.json"
_DEFAULT_RULES: List[Dict[str, Any]] = [
    {"name": "negative_net_assets",   "condition": "net_assets < 0",          "message": "Net assets are negative",           "severity": "critical"},
    {"name": "total_mismatch",        "condition": "total_assets < 0",         "message": "Total assets negative",             "severity": "critical"},
    {"name": "revenue_zero",          "condition": "revenue == 0",             "message": "Revenue is zero",                   "severity": "warning"},
    {"name": "expenses_exceed_rev",   "condition": "total_expenses > revenue",  "message": "Expenses exceed revenue",           "severity": "warning"},
]


def _load_rules() -> List[Dict[str, Any]]:
    try:
        raw = storage.get(_DEFAULT_RULES_KEY)
        if raw:
            return json.loads(raw)
    except Exception:
        pass
    return _DEFAULT_RULES


def _save_rules(rules: List[Dict[str, Any]]) -> None:
    try:
        storage.put(_DEFAULT_RULES_KEY, json.dumps(rules, indent=2).encode("utf-8"), "application/json")
    except Exception as e:
        logger.warning("Failed to persist rules: %s", e)


# ── FastAPI routes ─────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/v1/quality", tags=["quality"])


class AnomalyRequest(BaseModel):
    job_id: str
    schema_uri: Optional[str] = None
    baseline_job_id: Optional[str] = None
    store_audit: bool = True
    create_review: bool = True
    threshold: float = Field(0.70, ge=0.0, le=1.0, description="Anomaly score threshold")


class RuleEvaluationRequest(BaseModel):
    job_id: str
    rules_uri: Optional[str] = None
    use_default_rules: bool = True
    store_audit: bool = True


def _get_canonical_records(job: Any) -> Optional[List[Dict[str, Any]]]:
    """Extract canonical records from a completed job."""
    if not job:
        return None
    artifacts = getattr(job, "artifacts", None) or {}
    if isinstance(artifacts, str):
        try:
            artifacts = json.loads(artifacts)
        except Exception:
            artifacts = {}

    # Try canonical_json storage key first
    canonical_key = artifacts.get("canonical_json")
    if canonical_key:
        try:
            raw = storage.read(canonical_key)
            if raw:
                return json.loads(raw)
        except Exception:
            pass

    # Fallback: try artifacts directly
    canonical = artifacts.get("canonical") or artifacts.get("canonical_rows")
    if isinstance(canonical, list):
        return canonical

    return None


@router.post("/population-anomaly", summary="Run ensemble anomaly detection on a completed job")
async def run_ensemble_anomaly_detection(
    req: AnomalyRequest,
    background_tasks: BackgroundTasks,
    tenant=Depends(get_tenant),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Run the OADE ensemble (IsolationForest + ECOD + COPOD + HBOS + Autoencoder)
    against the canonical normalised data from a completed pipeline job.

    Returns anomaly scores per record above the threshold (default 0.70).
    Scores >0.85 are automatically queued for human review.
    """
    job = db.query(Job).filter(Job.id == req.job_id, Job.tenant_id == tenant.id).first()
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {req.job_id} not found")

    records = _get_canonical_records(job)
    if not records:
        # Try the in-memory job store (for API-only jobs not yet in DB)
        from app.api.v1.routes import _JOBS
        mem_job = _JOBS.get(req.job_id)
        if mem_job and mem_job.get("results"):
            results = mem_job["results"]
            records = results.get("canonical_rows") or results.get("canonical")
    if not records:
        raise HTTPException(status_code=400, detail="No canonical data found for this job")

    df = pd.DataFrame(records)
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    if not numeric_cols:
        raise HTTPException(status_code=400, detail="No numeric columns in canonical data")

    X = df[numeric_cols].fillna(0).values
    detector = EnsembleAnomalyDetector()
    detector.fit(X)
    scores = detector.predict(X)

    anomalies = []
    for idx, score in enumerate(scores):
        if score > req.threshold:
            anomalies.append({
                "record_index": int(idx),
                "record_id": records[idx].get("id") or records[idx].get("_id") or idx,
                "anomaly_score": float(score),
                "severity": "critical" if score > 0.90 else "high" if score > 0.80 else "medium",
                "numeric_values": {col: float(df[col].iloc[idx]) for col in numeric_cols[:10]},
            })

    result = {
        "job_id": req.job_id,
        "total_records": len(records),
        "numeric_columns": numeric_cols,
        "anomaly_count": len(anomalies),
        "threshold": req.threshold,
        "detectors_used": list(detector.detectors.keys()),
        "anomalies": anomalies,
    }

    if req.store_audit:
        try:
            audit = AuditLog(
                tenant_id=str(tenant.id),
                client_id=str(tenant.id),  # FIX: original code was missing client_id
                run_id=req.job_id,
                logger=logger,
            )
            audit.emit(
                event_type="population_anomaly_detection",
                message=f"OADE found {len(anomalies)} anomalies in {len(records)} records",
                severity="WARN" if anomalies else "INFO",
                meta={"anomaly_count": len(anomalies), "threshold": req.threshold},
            )
        except Exception as e:
            logger.warning("Audit log failed: %s", e)

    if req.create_review:
        # Queue high-severity anomalies for review
        for a in anomalies:
            if a["anomaly_score"] > 0.85:
                try:
                    from app.flywheel.queue import ActiveLearningQueue
                    q = ActiveLearningQueue(logger=logger)
                    q.add(
                        tenant_id=str(tenant.id),
                        client_id=str(tenant.id),
                        run_id=req.job_id,
                        vertical="accountancy",
                        kind="ANOMALY_REVIEW",
                        priority=a["anomaly_score"],
                        confidence=1.0 - a["anomaly_score"],
                        payload=a,
                    )
                except Exception as e:
                    logger.debug("Queue add failed: %s", e)

    return result


@router.post("/rules/validate", summary="Validate records against cross-field rules")
async def validate_cross_field_rules(
    req: RuleEvaluationRequest,
    background_tasks: BackgroundTasks,
    tenant=Depends(get_tenant),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    job = db.query(Job).filter(Job.id == req.job_id, Job.tenant_id == tenant.id).first()
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {req.job_id} not found")

    records = _get_canonical_records(job)
    if not records:
        raise HTTPException(status_code=400, detail="No canonical data found for this job")

    # Load rules
    rules: List[Dict[str, Any]] = []
    if req.rules_uri:
        try:
            raw = storage.read(req.rules_uri)
            if raw:
                rules = json.loads(raw)
        except Exception as e:
            logger.warning("Failed to load rules from %s: %s", req.rules_uri, e)
    if not rules and req.use_default_rules:
        rules = _load_rules()
    if not rules:
        raise HTTPException(status_code=400, detail="No rules provided and no default rules found")

    engine = CrossFieldRulesEngine(rules)
    violations = engine.validate(records)

    if req.store_audit:
        try:
            audit = AuditLog(
                tenant_id=str(tenant.id),
                client_id=str(tenant.id),  # FIX: original code was missing client_id
                run_id=req.job_id,
                logger=logger,
            )
            audit.emit(
                event_type="cross_field_validation",
                message=f"Rules engine found {len(violations)} records with violations",
                severity="WARN" if violations else "INFO",
                meta={"violations_count": len(violations), "rules_count": len(rules)},
            )
        except Exception as e:
            logger.warning("Audit log failed: %s", e)

    return {
        "job_id": req.job_id,
        "total_records": len(records),
        "rules_applied": len(rules),
        "violations_count": len(violations),
        "violations": violations[:200],
    }


@router.get("/rules/default", summary="Get the default cross-field ruleset")
async def get_default_rules() -> Dict[str, Any]:
    rules = _load_rules()
    return {"count": len(rules), "rules": rules}


@router.put("/rules/default", summary="Save a new default ruleset")
async def save_default_rules(
    rules: List[Dict[str, Any]] = Body(...),
) -> Dict[str, Any]:
    _save_rules(rules)
    return {"ok": True, "saved_count": len(rules)}
