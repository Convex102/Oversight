from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

MODEL_DIR = Path(os.getenv("ASGL_MODEL_DIR", "data/models"))
MODEL_DIR.mkdir(parents=True, exist_ok=True)
FLYWHEEL_DIR = Path(os.getenv("ASGL_FLYWHEEL_DIR", "data/flywheel"))
MIN_ROWS_TO_TRAIN = int(os.getenv("ASGL_MIN_TRAIN_ROWS", "20"))


def _utc() -> str:
    return datetime.utcnow().isoformat() + "Z"


@dataclass
class TrainingJob:
    job_id: str
    ts: str
    tenant_id: str
    vertical: str
    model_id: str
    base_version: str
    status: str
    reason: str
    meta: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _load_flywheel_vectors(tenant_id: str, vertical: str) -> Optional[np.ndarray]:
    """Load embeddings from flywheel JSONL for training."""
    p = FLYWHEEL_DIR / tenant_id / f"{vertical}.jsonl"
    if not p.exists():
        return None
    rows = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            # Extract embedding vector if stored, else extract numeric features
            vec = rec.get("embedding") or rec.get("vector")
            if vec:
                rows.append(np.array(vec, dtype=np.float32))
            else:
                # Fall back to numeric payload features
                payload = rec.get("payload", {})
                feats = []
                for v in payload.values():
                    try:
                        feats.append(float(v))
                    except (TypeError, ValueError):
                        pass
                if feats:
                    rows.append(np.array(feats[:32], dtype=np.float32))
    except Exception as e:
        logger.warning("flywheel load failed: %s", e)
        return None

    if not rows:
        return None

    # Pad/truncate to uniform width
    width = max(len(r) for r in rows)
    padded = []
    for r in rows:
        if len(r) < width:
            r = np.pad(r, (0, width - len(r)))
        else:
            r = r[:width]
        padded.append(r)
    return np.stack(padded, axis=0)


def _model_path(tenant_id: str, vertical: str, model_id: str) -> Path:
    d = MODEL_DIR / tenant_id / vertical
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{model_id}.joblib"


def _train_isolation_forest(X: np.ndarray, path: Path) -> Dict[str, Any]:
    """Train IsolationForest on flywheel data and persist with joblib."""
    try:
        import joblib
        from sklearn.ensemble import IsolationForest
    except ImportError as e:
        return {"error": f"sklearn/joblib not available: {e}", "trained": False}

    try:
        model = IsolationForest(
            n_estimators=200,
            contamination="auto",
            random_state=42,
            n_jobs=-1,
        )
        model.fit(X)
        joblib.dump(model, path)
        logger.info("IsolationForest trained: %d samples, %d features -> %s", X.shape[0], X.shape[1], path)
        return {
            "trained": True,
            "n_samples": int(X.shape[0]),
            "n_features": int(X.shape[1]),
            "model_path": str(path),
        }
    except Exception as e:
        logger.exception("IsolationForest training failed")
        return {"error": str(e), "trained": False}


def load_model(tenant_id: str, vertical: str, model_id: str):
    """Load a trained model from disk. Returns None if not found."""
    p = _model_path(tenant_id, vertical, model_id)
    if not p.exists():
        return None
    try:
        import joblib
        return joblib.load(p)
    except Exception as e:
        logger.warning("Model load failed %s: %s", p, e)
        return None


class TrainingScheduler:
    def __init__(self, logger=None):
        self._log = logger or logging.getLogger(__name__)
        self._jobs: Dict[str, TrainingJob] = {}

    def trigger(
        self, *, tenant_id: str, vertical: str, model_id: str,
        base_version: str, reason: str, meta: Optional[Dict[str, Any]] = None
    ) -> TrainingJob:
        job_id = f"train_{len(self._jobs)+1:06d}"
        job = TrainingJob(
            job_id=job_id, ts=_utc(), tenant_id=tenant_id, vertical=vertical,
            model_id=model_id, base_version=base_version, status="QUEUED",
            reason=reason, meta=meta or {},
        )
        self._jobs[job_id] = job
        logger.info("flywheel.training_trigger job=%s tenant=%s vertical=%s model=%s",
                    job_id, tenant_id, vertical, model_id)

        # Attempt actual training immediately
        self._run(job)
        return job

    def _run(self, job: TrainingJob) -> None:
        """Actually train the model using flywheel data."""
        job.status = "RUNNING"
        try:
            X = _load_flywheel_vectors(job.tenant_id, job.vertical)
            if X is None or len(X) < MIN_ROWS_TO_TRAIN:
                job.status = "SKIPPED"
                job.meta["reason"] = f"insufficient_data (need {MIN_ROWS_TO_TRAIN}, have {len(X) if X is not None else 0})"
                logger.info("Training skipped: insufficient flywheel data for %s/%s", job.tenant_id, job.vertical)
                return

            path = _model_path(job.tenant_id, job.vertical, job.model_id)
            result = _train_isolation_forest(X, path)
            job.meta.update(result)
            job.status = "DONE" if result.get("trained") else "FAILED"
            job.meta["completed_at"] = _utc()
        except Exception as e:
            job.status = "FAILED"
            job.meta["error"] = str(e)
            job.meta["completed_at"] = _utc()
            logger.exception("Training job %s failed", job.job_id)

    def complete(self, job_id: str, *, success: bool = True, meta: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        job = self._jobs.get(job_id)
        if not job:
            return None
        job.status = "DONE" if success else "FAILED"
        job.meta.update(meta or {})
        job.meta["completed_at"] = _utc()
        return job.to_dict()

    def list(self, *, tenant_id: str = "", vertical: str = "") -> List[Dict[str, Any]]:
        out = []
        for j in self._jobs.values():
            if tenant_id and j.tenant_id != tenant_id:
                continue
            if vertical and j.vertical != vertical:
                continue
            out.append(j.to_dict())
        return sorted(out, key=lambda x: x["ts"], reverse=True)
