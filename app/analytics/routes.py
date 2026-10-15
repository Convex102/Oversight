"""
Oversight Advanced Analytics — BOCPD, Hurst Exponent, Per-Record Entropy
=========================================================================
Production-hardened version with all integration bugs fixed:

  Fixed from DeepSeek original:
  ├── [CRASH] add_to_review_queue doesn't exist → uses ActiveLearningQueue.add()
  ├── [CRASH] app.core.config → app.config
  ├── [CRASH] fcntl (Unix-only) → threading.Lock + atomic write (Windows-safe)
  ├── [CRASH] deque not imported → added
  ├── [FAIL]  storage.write() / storage.read() → storage.put() / storage.get()
  ├── [FAIL]  AuditLog missing client_id arg → fixed
  ├── [FAIL]  add_to_review_queue as standalone → ActiveLearningQueue().add()
  ├── [FAIL]  Pydantic v2: regex= → pattern=, @validator → @field_validator
  ├── [WARN]  /tmp hardcoded path → tempfile.gettempdir() (Windows-safe)
  ├── [WARN]  np.load() lazy inside lock → forced dict() load
  ├── [WARN]  prometheus/structlog/tenacity/scipy → graceful optional imports
  └── [WARN]  job.artifacts mutation → flag_modified()

Routes (prefix /api/v1/analytics):
  POST /changepoint        BOCPD change-point detection on a job field
  POST /hurst              Rolling Hurst exponent on a job field
  POST /entropy-per-record Per-record Shannon entropy over numeric fields
  GET  /status/{job_id}   Retrieve stored analytics results for a job
"""

from __future__ import annotations

import json
import logging
import math
import os
import sys
import tempfile
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.config import settings  # FIXED: was app.core.config
from app.deps import get_tenant
from app.db.session import get_db
from app.db.models import Job
from app.storage import storage
from app.governance.audit import AuditLog

logger = logging.getLogger("Oversight.Analytics")

# ── Optional deps — graceful degradation ─────────────────────────────────────

try:
    from scipy.special import gammaln
    _SCIPY_OK = True
except ImportError:
    _SCIPY_OK = False
    logger.warning("scipy not installed — BOCPD will use approximation")

try:
    from prometheus_client import Counter, Histogram
    _analytics_requests = Counter("analytics_requests_total", "Total analytics requests", ["endpoint", "status"])
    _analytics_duration = Histogram("analytics_duration_seconds", "Analytics duration", ["endpoint"])
    def _track(endpoint: str, status: str, duration: float):
        _analytics_requests.labels(endpoint=endpoint, status=status).inc()
        _analytics_duration.labels(endpoint=endpoint).observe(duration)
    _PROM_OK = True
except ImportError:
    _PROM_OK = False
    def _track(endpoint: str, status: str, duration: float): pass  # no-op

try:
    import structlog as _structlog
    logger = _structlog.get_logger("Oversight.Analytics")
except ImportError:
    pass  # use stdlib logger set above


# ── Configuration ─────────────────────────────────────────────────────────────

# Windows-safe temp dir (not hardcoded /tmp)
_CHECKPOINT_ROOT = Path(tempfile.gettempdir()) / "oversight_analytics_cache"
_CHECKPOINT_ROOT.mkdir(parents=True, exist_ok=True)

_MAX_SERIES_LENGTH = 5_000_000
_MAX_HURST_WINDOW  = 2000
_BOCPD_MAX_HAZARD  = 0.1
_HURST_DEFAULT_MAX_LAG = 50
_CHUNK_SIZE = 10_000


# ── Cross-platform file locking ───────────────────────────────────────────────
# FIXED: fcntl is Unix-only. Use threading.Lock for in-process safety
# plus atomic temp-file replacement for write safety across workers.

_path_locks: Dict[str, threading.Lock] = {}
_locks_mutex = threading.Lock()


def _get_lock(path: Path) -> threading.Lock:
    key = str(path)
    with _locks_mutex:
        if key not in _path_locks:
            _path_locks[key] = threading.Lock()
        return _path_locks[key]


def _atomic_save_npz(path: Path, **arrays) -> None:
    """Atomically write a .npz file using a temp file + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.npz")
    lock = _get_lock(path)
    with lock:
        np.savez(str(tmp), **arrays)
        tmp.replace(path)


def _load_npz_safe(path: Path) -> Optional[Dict[str, Any]]:
    """Load and fully materialise an npz file (fixes lazy-load bug)."""
    lock = _get_lock(path)
    with lock:
        if not path.exists():
            return None
        try:
            # dict() forces eager load before the file handle is released
            return dict(np.load(str(path), allow_pickle=True))
        except Exception as e:
            logger.warning("npz load failed", error=str(e), path=str(path))
            return None


# ═══════════════════════════════════════════════════════════════════════════════
# 1. BOCPD — Bayesian Online Change-Point Detection
# ═══════════════════════════════════════════════════════════════════════════════

def _gammaln(x):
    """gammaln with scipy or Stirling approximation fallback."""
    if _SCIPY_OK:
        return gammaln(x)
    # Lanczos approximation — accurate to ~1e-10 for x > 0
    if x <= 0:
        return float("inf")
    coeffs = [76.18009172947146, -86.50532032941677, 24.01409824083091,
              -1.231739572450155, 0.001208650973866179, -5.395239384953e-6]
    y = x; tmp = x + 5.5
    tmp = (x + 0.5) * math.log(tmp) - tmp
    ser = 1.000000000190015
    for c in coeffs:
        y += 1; ser += c / y
    return tmp + math.log(2.5066282746310005 * ser / x)


class BOCPD:
    """
    Numerically-stable BOCPD (Adams & MacKay 2007).
    Log-space updates, truncated run-length distribution, checkpointable.

    For accountants: detects when a numerical series (e.g. closing balances,
    credit movements) undergoes a sudden structural shift — flagging the
    specific transaction index where it happened.
    """

    def __init__(self, hazard: float = 1 / 100, max_runs: int = 1000):
        self.hazard    = hazard
        self.max_runs  = max_runs
        self._reset()

    def _reset(self):
        self.log_R = np.array([0.0])
        self.mu    = np.array([0.0])
        self.k     = np.array([1.0])
        self.a     = np.array([1.0])
        self.b     = np.array([1.0])

    def _predictive_logp(self, x: float) -> np.ndarray:
        df  = 2 * self.a
        var = self.b * (self.k + 1) / (self.a * self.k + 1e-12)
        d   = (x - self.mu) / np.sqrt(var + 1e-12)
        logp = (
            np.vectorize(_gammaln)((df + 1) / 2)
            - np.vectorize(_gammaln)(df / 2)
            - 0.5 * np.log(np.maximum(df * math.pi * var, 1e-300))
            - 0.5 * (df + 1) * np.log1p(np.clip(d**2 / (df + 1e-12), -1 + 1e-10, None))
        )
        return np.clip(logp, -700, 700)

    def update(self, x: float) -> float:
        """Single-observation update. Returns change-point probability [0,1]."""
        if not np.isfinite(x):
            return 0.0

        log_pred    = self._predictive_logp(x)
        log_growth  = self.log_R + log_pred + np.log(1 - self.hazard)
        log_cp_mass = np.logaddexp.reduce(self.log_R + log_pred + np.log(self.hazard))
        new_log_R   = np.append(log_growth, log_cp_mass)

        # Normalise (subtract max for numerical stability)
        mx = np.max(new_log_R)
        if mx > -np.inf:
            new_log_R -= mx
        lin_R = np.exp(new_log_R)
        lin_R = lin_R / (lin_R.sum() + 1e-300)

        # Update sufficient statistics
        new_k   = np.append(self.k + 1, 1.0)
        new_mu  = np.append((self.k * self.mu + x) / (self.k + 1), 0.0)
        new_a   = np.append(self.a + 0.5, 1.0)
        new_b   = np.append(self.b + self.k * (x - self.mu)**2 / (2 * (self.k + 1)), 1.0)

        # Truncate (keep most probable run lengths)
        if len(lin_R) > self.max_runs:
            excess   = lin_R[:-self.max_runs].sum()
            lin_R    = lin_R[-self.max_runs:]
            lin_R[-1] += excess
            lin_R    = lin_R / lin_R.sum()
            new_k    = new_k[-self.max_runs:]
            new_mu   = new_mu[-self.max_runs:]
            new_a    = new_a[-self.max_runs:]
            new_b    = new_b[-self.max_runs:]

        self.log_R = np.log(lin_R + 1e-300)
        self.k = new_k; self.mu = new_mu; self.a = new_a; self.b = new_b
        return float(np.exp(log_cp_mass))

    def process_series(
        self,
        values: List[float],
        checkpoint_path: Optional[Path] = None,
    ) -> List[float]:
        """
        Process a list of values. Resumes from checkpoint if available.
        Returns list of change-point probabilities (same length as input).
        """
        results: List[float] = []
        start_idx = 0

        if checkpoint_path:
            ckpt = _load_npz_safe(checkpoint_path)
            if ckpt is not None:
                saved_n = int(ckpt.get("n", 0))
                if 0 < saved_n <= len(values):
                    results   = list(ckpt["results"][:saved_n])
                    self.log_R = ckpt["log_R"]
                    self.k     = ckpt["k"]
                    self.mu    = ckpt["mu"]
                    self.a     = ckpt["a"]
                    self.b     = ckpt["b"]
                    start_idx  = saved_n
                    logger.info("BOCPD resumed from checkpoint", saved=saved_n)

        for idx in range(start_idx, len(values)):
            cp_prob = self.update(values[idx])
            results.append(cp_prob)

            # Periodic checkpoint (every 5000 points)
            if checkpoint_path and (idx + 1) % 5000 == 0:
                _atomic_save_npz(
                    checkpoint_path,
                    n=idx + 1,
                    results=np.array(results, dtype=np.float32),
                    log_R=self.log_R, k=self.k, mu=self.mu, a=self.a, b=self.b,
                )

        # Clean up checkpoint on completion
        if checkpoint_path and checkpoint_path.exists():
            try: checkpoint_path.unlink()
            except Exception: pass

        return results

    def state_dict(self) -> Dict[str, Any]:
        return {"log_R": self.log_R, "k": self.k, "mu": self.mu, "a": self.a, "b": self.b}


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Hurst Exponent
# ═══════════════════════════════════════════════════════════════════════════════

def hurst_exponent(series: np.ndarray, max_lag: Optional[int] = None) -> float:
    """
    R/S Hurst exponent.

    Interpretation for accountants:
      H > 0.7 — strong persistent trend (unusual, warrants review)
      H ≈ 0.5 — random walk (normal for most financial series)
      H < 0.3 — mean-reverting (also unusual)
    """
    series = np.asarray(series, dtype=np.float64)
    series = series[np.isfinite(series)]
    if len(series) < 10:
        return 0.5

    max_lag = max_lag or min(len(series) // 4, _HURST_DEFAULT_MAX_LAG)
    lags    = range(2, max(3, max_lag + 1))
    tau     = []
    for lag in lags:
        diff = series[lag:] - series[:-lag]
        std  = np.std(diff)
        tau.append(std if std > 0 else 1e-8)

    tau = np.array(tau)
    if np.std(tau) < 1e-10 or len(tau) < 2:
        return 0.5

    try:
        coeff = np.polyfit(np.log(list(lags)), np.log(tau + 1e-12), 1)[0]
        return float(np.clip(coeff * 2, 0.05, 0.95))
    except Exception:
        return 0.5


class RollingHurst:
    """Memory-efficient rolling Hurst using a sliding deque."""

    def __init__(self, window: int, max_lag: Optional[int] = None):
        self.window  = window
        self.max_lag = max_lag or _HURST_DEFAULT_MAX_LAG
        self.buffer: deque = deque(maxlen=window)  # FIXED: deque now imported
        self.results: List[Tuple[int, float]] = []

    def add(self, idx: int, value: float) -> Optional[float]:
        self.buffer.append(value)
        if len(self.buffer) == self.window:
            arr = np.array(self.buffer, dtype=np.float64)
            h   = hurst_exponent(arr, self.max_lag)
            self.results.append((idx, h))
            return h
        return None

    def compute_all(self, values: List[float]) -> List[Tuple[int, float]]:
        for idx, v in enumerate(values):
            self.add(idx, v)
        return self.results


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Per-Record Shannon Entropy
# ═══════════════════════════════════════════════════════════════════════════════

def compute_entropy_batch(
    records: List[Dict[str, Any]],
    numeric_fields: List[str],
) -> List[Dict[str, Any]]:
    """
    Shannon entropy over the numeric_fields values within each record.

    Higher entropy = more evenly distributed values across fields.
    An unusually high-entropy record (vs peers) may indicate data quality issues.
    """
    results = []
    for idx, rec in enumerate(records):
        vals = []
        for f in numeric_fields:
            v = rec.get(f)
            if v is not None and isinstance(v, (int, float)) and np.isfinite(v):
                vals.append(abs(float(v)))

        if len(vals) < 2 or sum(abs(v) for v in vals) < 1e-8:
            ent = 0.0  # all-zero or single value → no meaningful entropy
        else:
            vals_arr = np.array(vals) + 1e-10
            probs    = vals_arr / vals_arr.sum()
            ent      = float(-np.sum(probs * np.log2(probs + 1e-10)))

        results.append({
            "record_index": idx,
            "record_id":    rec.get("id") or rec.get("_id") or idx,
            "entropy":      ent,
            "fields_used":  len(vals),
        })
    return results


# ═══════════════════════════════════════════════════════════════════════════════
# Canonical record extraction
# ═══════════════════════════════════════════════════════════════════════════════

def _extract_canonical_records(job: Job) -> List[Dict[str, Any]]:
    """
    Get canonical records from a completed job.
    Tries storage first (large jobs), falls back to inline artifacts.
    """
    artifacts = getattr(job, "artifacts", None) or {}
    if isinstance(artifacts, str):
        try:
            artifacts = json.loads(artifacts)
        except Exception:
            artifacts = {}

    # Try storage key first
    canon_key = artifacts.get("canonical_json")
    if canon_key:
        try:
            raw = storage.get(canon_key)  # FIXED: was storage.read()
            if raw:
                data = json.loads(raw.decode("utf-8"))
                if isinstance(data, list):
                    return data
        except Exception as e:
            logger.warning("canonical_json storage fetch failed", error=str(e), key=canon_key)

    # Inline fallback (small jobs)
    inline = artifacts.get("canonical") or artifacts.get("canonical_rows")
    if isinstance(inline, list):
        return inline

    # Last resort: from v1 routes in-memory job store
    try:
        from app.api.v1.routes import _JOBS
        mem_job = _JOBS.get(str(job.id))
        if mem_job and mem_job.get("results"):
            r = mem_job["results"]
            rows = r.get("canonical_rows") or r.get("canonical")
            if isinstance(rows, list):
                return rows
    except Exception:
        pass

    return []


def _get_numeric_field_values(records: List[Dict], field: str) -> List[float]:
    """Extract numeric values for a single field, dropping NaN/null."""
    values = []
    for rec in records:
        v = rec.get(field)
        if v is not None and isinstance(v, (int, float)) and np.isfinite(float(v)):
            values.append(float(v))
    return values


def _store_analytics_result(
    job: Job,
    db: Session,
    result_type: str,
    key: str,
    data: Dict[str, Any],
) -> None:
    """Persist analytics result to storage and update job artifacts."""
    try:
        raw = json.dumps(data, default=str).encode("utf-8")
        storage.put(key, raw, "application/json")  # FIXED: was storage.write()
    except Exception as e:
        logger.warning("analytics result storage failed", error=str(e))

    try:
        from sqlalchemy.orm.attributes import flag_modified  # FIXED: JSON mutation
        if job.artifacts is None:
            job.artifacts = {}
        if "analytics" not in job.artifacts:
            job.artifacts["analytics"] = {}
        job.artifacts["analytics"][result_type] = key
        flag_modified(job, "artifacts")
        db.commit()
    except Exception as e:
        logger.warning("job artifacts update failed", error=str(e))


def _queue_for_review(
    tenant_id: str,
    run_id: str,
    kind: str,
    priority: float,
    payload: Dict[str, Any],
) -> None:
    """Queue an item for human review. FIXED: uses ActiveLearningQueue.add()."""
    try:
        from app.flywheel.queue import ActiveLearningQueue
        q = ActiveLearningQueue()
        q.add(
            tenant_id=tenant_id,
            client_id=tenant_id,
            run_id=run_id,
            vertical="accountancy",
            kind=kind,
            priority=priority,
            confidence=1.0 - priority,
            payload=payload,
        )
    except Exception as e:
        logger.warning("review queue add failed", error=str(e))


# ═══════════════════════════════════════════════════════════════════════════════
# Request/Response schemas — Pydantic v2
# ═══════════════════════════════════════════════════════════════════════════════

class ChangepointRequest(BaseModel):
    job_id:        str
    field:         str   = Field(..., min_length=1, description="Canonical field name")
    hazard:        float = Field(0.01, ge=0.0001, le=_BOCPD_MAX_HAZARD)
    store_results: bool  = True
    create_review: bool  = True

    @field_validator("field")                        # FIXED: Pydantic v2 syntax
    @classmethod
    def field_alphanumeric(cls, v: str) -> str:
        import re
        if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", v):
            raise ValueError("field must be a valid identifier")
        return v


_BOCPD_MAX_HAZARD = 0.1   # referenced in Field above — redeclare after class


class HurstRequest(BaseModel):
    job_id:        str
    field:         str = Field(..., min_length=1)
    window:        int = Field(100, ge=10, le=_MAX_HURST_WINDOW)
    store_results: bool = True
    create_review: bool = True


class EntropyRequest(BaseModel):
    job_id:         str
    numeric_fields: List[str] = Field(..., min_length=1, max_length=50)  # FIXED: min_items/max_items
    store_results:  bool = True
    create_review:  bool = True


# ═══════════════════════════════════════════════════════════════════════════════
# FastAPI Router
# ═══════════════════════════════════════════════════════════════════════════════

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _get_job(job_id: str, tenant_id: str, db: Session) -> Job:
    try:
        tid = int(tenant_id)
    except (TypeError, ValueError):
        raise HTTPException(404, f"Job {job_id} not found")
    job = db.query(Job).filter(
        Job.id == job_id,
        Job.tenant_id == tid,
    ).first()
    if not job:
        raise HTTPException(404, f"Job {job_id} not found")
    return job


# ── POST /changepoint ─────────────────────────────────────────────────────────

@router.post("/changepoint", summary="BOCPD change-point detection on a job field")
async def run_changepoint(
    req: ChangepointRequest,
    background_tasks: BackgroundTasks,
    tenant=Depends(get_tenant),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Bayesian Online Change-Point Detection on a named numeric field.

    What it tells you (in plain English):
      - Which specific records in the dataset mark a structural shift in the
        series — e.g. the point where debits suddenly jumped from a normal
        range to an abnormal one.
      - Each alarm shows the record index and how certain the detection is.
      - Use this to find: period-end stuffing, one-off large postings,
        systematic changes in posting behaviour.
    """
    t0 = time.time()
    tenant_id = str(tenant.id)  # SECURITY-FIX 2026-10-15: was an undefined name (NameError, HTTP 500)
    try:
        job     = _get_job(req.job_id, tenant_id, db)
        records = await run_in_threadpool(_extract_canonical_records, job)

        if not records:
            raise HTTPException(400, "No canonical data in this job. Run the pipeline first.")

        if req.field not in records[0]:
            available = [k for k, v in records[0].items()
                        if isinstance(v, (int, float))]
            raise HTTPException(
                400,
                f"Field '{req.field}' not found. Numeric fields available: {available[:10]}"
            )

        values = await run_in_threadpool(_get_numeric_field_values, records, req.field)
        if len(values) < 10:
            raise HTTPException(400, f"Not enough numeric values in '{req.field}' (got {len(values)}, need ≥10)")

        if len(values) > _MAX_SERIES_LENGTH:
            raise HTTPException(400, f"Series too long ({len(values):,} > {_MAX_SERIES_LENGTH:,})")

        # Run BOCPD in thread (CPU-heavy)
        checkpoint_path = _CHECKPOINT_ROOT / tenant_id / req.job_id / f"bocpd_{req.field}.npz"
        detector = BOCPD(hazard=req.hazard, max_runs=1000)
        cp_probs = await run_in_threadpool(
            detector.process_series, values, checkpoint_path
        )

        alarms = [
            {
                "record_index": i,
                "record_id":    records[i].get("id") or records[i].get("code") or i,
                "probability":  float(p),
                "value":        values[i],
                "interpretation": (
                    "Highly likely change point — this record marks a structural shift" if p > 0.9
                    else "Probable change point" if p > 0.7
                    else "Possible change point"
                ),
            }
            for i, p in enumerate(cp_probs)
            if p > 0.5
        ]

        # Plain-English summary
        def _plain_english_summary():
            if not alarms:
                return f"No structural shifts detected in '{req.field}'. The series behaves consistently throughout."
            high = [a for a in alarms if a["probability"] > 0.9]
            return (
                f"Detected {len(alarms)} change point(s) in '{req.field}'. "
                + (f"{len(high)} are high-certainty (>90%). " if high else "")
                + f"Review record(s) at index {[a['record_index'] for a in alarms[:5]]}."
            )

        results = {
            "job_id":            req.job_id,
            "field":             req.field,
            "hazard":            req.hazard,
            "series_length":     len(cp_probs),
            "alarm_count":       len(alarms),
            "alarms":            alarms,
            "summary":           _plain_english_summary(),
            "interpretation":    {
                "method":   "Bayesian Online Change-Point Detection (Adams & MacKay 2007)",
                "plain":    "This analysis detects the specific records where the numerical series "
                            "underwent a sudden statistical shift. For accountants: these are the "
                            "transactions that 'broke the pattern' and warrant manual review.",
            },
        }

        if req.store_results:
            _store_analytics_result(
                job, db,
                f"bocpd_{req.field}",
                f"analytics/{req.job_id}/bocpd_{req.field}.json",
                results,
            )

        # FIXED: AuditLog now gets client_id
        audit = AuditLog(tenant_id, tenant_id, req.job_id, logger=None)
        audit.emit(
            "analytics.bocpd",
            f"BOCPD on '{req.field}': {len(alarms)} change points in {len(values)} values",
            severity="WARN" if alarms else "INFO",
            meta={"field": req.field, "alarm_count": len(alarms), "series_len": len(values)},
        )

        # Queue high-certainty alarms for review
        if req.create_review:
            for alarm in alarms:
                if alarm["probability"] > 0.9:
                    background_tasks.add_task(
                        _queue_for_review,
                        tenant_id=tenant_id,
                        run_id=req.job_id,
                        kind="CHANGEPOINT_ALARM",
                        priority=alarm["probability"],
                        payload={
                            "field":          req.field,
                            "record_index":   alarm["record_index"],
                            "record_id":      alarm["record_id"],
                            "probability":    alarm["probability"],
                            "value":          alarm["value"],
                            "message":        alarm["interpretation"],
                        },
                    )

        _track("changepoint", "success", time.time() - t0)
        return results

    except HTTPException:
        _track("changepoint", "client_error", time.time() - t0)
        raise
    except Exception as e:
        _track("changepoint", "server_error", time.time() - t0)
        logger.exception("BOCPD internal error", job_id=req.job_id)
        raise HTTPException(500, f"Change-point detection failed: {e}")


# ── POST /hurst ───────────────────────────────────────────────────────────────

@router.post("/hurst", summary="Rolling Hurst exponent on a job field")
async def run_hurst(
    req: HurstRequest,
    background_tasks: BackgroundTasks,
    tenant=Depends(get_tenant),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Rolling Hurst exponent — measures trend persistence.

    What it tells you (in plain English):
      - H > 0.65: persistent trend — values tend to keep moving in one direction.
        In an expense account: could indicate systematic over- or under-accrual.
      - H ≈ 0.50: random walk — normal for most financial series.
      - H < 0.35: mean-reverting — values oscillate around a mean. Usually OK.
    """
    t0 = time.time()
    tenant_id = str(tenant.id)  # SECURITY-FIX 2026-10-15: was an undefined name (NameError, HTTP 500)
    try:
        job     = _get_job(req.job_id, tenant_id, db)
        records = await run_in_threadpool(_extract_canonical_records, job)

        if not records:
            raise HTTPException(400, "No canonical data in this job.")

        if req.field not in records[0]:
            available = [k for k, v in records[0].items() if isinstance(v, (int, float))]
            raise HTTPException(400, f"Field '{req.field}' not found. Available: {available[:10]}")

        values = await run_in_threadpool(_get_numeric_field_values, records, req.field)
        if len(values) < req.window:
            raise HTTPException(
                400,
                f"Not enough values for window={req.window} (got {len(values)}). "
                f"Use a smaller window."
            )

        rh           = RollingHurst(req.window)
        hurst_points = await run_in_threadpool(rh.compute_all, values)

        if not hurst_points:
            raise HTTPException(400, "Rolling Hurst produced no results.")

        hurst_vals = [h for _, h in hurst_points]
        mean_h     = float(np.mean(hurst_vals))
        max_h      = float(np.max(hurst_vals))

        def _interpret_hurst(h: float) -> str:
            if h > 0.7:   return "Strong persistent trend — review recommended"
            if h > 0.6:   return "Mild trend persistence — worth noting"
            if h > 0.4:   return "Near random walk — normal"
            if h > 0.3:   return "Mildly mean-reverting — normal"
            return             "Strong mean reversion — unusual"

        results = {
            "job_id":       req.job_id,
            "field":        req.field,
            "window":       req.window,
            "series_length": len(values),
            "mean_hurst":   mean_h,
            "max_hurst":    max_h,
            "interpretation": _interpret_hurst(mean_h),
            "rolling_hurst": [{"index": i, "hurst": h, "interpretation": _interpret_hurst(h)}
                               for i, h in hurst_points],
            "plain_english": {
                "method": "R/S Hurst Exponent (rolling window)",
                "summary": (
                    f"The mean Hurst exponent for '{req.field}' is {mean_h:.3f}. "
                    + _interpret_hurst(mean_h) + ". "
                    + (f"Peak value was {max_h:.3f}."
                       if max_h > mean_h + 0.1 else "")
                ),
            },
        }

        if req.store_results:
            _store_analytics_result(
                job, db,
                f"hurst_{req.field}",
                f"analytics/{req.job_id}/hurst_{req.field}.json",
                results,
            )

        audit = AuditLog(tenant_id, tenant_id, req.job_id)
        audit.emit(
            "analytics.hurst",
            f"Hurst on '{req.field}': mean={mean_h:.3f}",
            severity="WARN" if mean_h > 0.65 else "INFO",
            meta={"field": req.field, "mean_hurst": mean_h},
        )

        if req.create_review and mean_h > 0.65:
            background_tasks.add_task(
                _queue_for_review,
                tenant_id=tenant_id,
                run_id=req.job_id,
                kind="HURST_TREND",
                priority=min(0.95, (mean_h - 0.5) * 2),
                payload={
                    "field":       req.field,
                    "mean_hurst":  mean_h,
                    "message":     f"Persistent trend detected (H={mean_h:.3f}) in '{req.field}'",
                    "action":      "Review this field for systematic over/under-accrual",
                },
            )

        _track("hurst", "success", time.time() - t0)
        return results

    except HTTPException:
        _track("hurst", "client_error", time.time() - t0)
        raise
    except Exception as e:
        _track("hurst", "server_error", time.time() - t0)
        logger.exception("Hurst internal error", job_id=req.job_id)
        raise HTTPException(500, f"Hurst computation failed: {e}")


# ── POST /entropy-per-record ──────────────────────────────────────────────────

@router.post("/entropy-per-record", summary="Per-record Shannon entropy over numeric fields")
async def run_entropy(
    req: EntropyRequest,
    background_tasks: BackgroundTasks,
    tenant=Depends(get_tenant),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Shannon entropy across specified numeric fields within each record.

    What it tells you (in plain English):
      - A record with high entropy has values that are very evenly spread
        across the selected fields — unusual if one field normally dominates.
      - Outliers (records with entropy > mean + 2σ) are flagged automatically.
      - Useful for: detecting records where a large amount has been split
        across multiple nominal codes, or where the pattern of postings
        is statistically unlike the rest of the dataset.
    """
    t0 = time.time()
    tenant_id = str(tenant.id)  # SECURITY-FIX 2026-10-15: was an undefined name (NameError, HTTP 500)
    try:
        job     = _get_job(req.job_id, tenant_id, db)
        records = await run_in_threadpool(_extract_canonical_records, job)

        if not records:
            raise HTTPException(400, "No canonical data in this job.")

        # Validate fields exist
        available = set(records[0].keys())
        missing = [f for f in req.numeric_fields if f not in available]
        if missing:
            raise HTTPException(
                400,
                f"Fields not found: {missing}. "
                f"Available: {sorted(available)[:15]}"
            )

        entropies   = await run_in_threadpool(compute_entropy_batch, records, req.numeric_fields)
        ent_vals    = [e["entropy"] for e in entropies]
        mean_ent    = float(np.mean(ent_vals))
        std_ent     = float(np.std(ent_vals))
        threshold   = mean_ent + 2 * std_ent
        high_entropy = [e for e in entropies if e["entropy"] > threshold]

        # Add interpretation to each flagged record
        for e in entropies:
            e["above_threshold"] = e["entropy"] > threshold
            if e["above_threshold"]:
                e["interpretation"] = (
                    f"This record has unusually high entropy ({e['entropy']:.3f}) "
                    f"compared to the dataset average ({mean_ent:.3f}). "
                    f"The numeric values are more evenly distributed than typical "
                    f"— investigate whether amounts have been split across fields."
                )

        results = {
            "job_id":           req.job_id,
            "fields":           req.numeric_fields,
            "record_count":     len(records),
            "mean_entropy":     mean_ent,
            "std_entropy":      std_ent,
            "threshold":        threshold,
            "high_entropy_count": len(high_entropy),
            "records":          entropies,
            "plain_english": {
                "method":  "Shannon entropy (bits) per record",
                "summary": (
                    f"Analysed {len(records)} records across {len(req.numeric_fields)} fields. "
                    f"Average entropy: {mean_ent:.3f} bits. "
                    + (
                        f"{len(high_entropy)} record(s) are statistical outliers "
                        f"(>2σ above mean) and have been flagged for review."
                        if high_entropy else
                        "No statistical outliers detected — entropy distribution is consistent."
                    )
                ),
            },
        }

        if req.store_results:
            _store_analytics_result(
                job, db,
                "entropy_per_record",
                f"analytics/{req.job_id}/entropy_per_record.json",
                results,
            )

        audit = AuditLog(tenant_id, tenant_id, req.job_id)
        audit.emit(
            "analytics.entropy",
            f"Per-record entropy: {len(high_entropy)} outliers in {len(records)} records",
            severity="WARN" if high_entropy else "INFO",
            meta={"mean_entropy": mean_ent, "outliers": len(high_entropy)},
        )

        if req.create_review and high_entropy:
            for rec in high_entropy:
                background_tasks.add_task(
                    _queue_for_review,
                    tenant_id=tenant_id,
                    run_id=req.job_id,
                    kind="HIGH_ENTROPY_RECORD",
                    priority=min(0.95, rec["entropy"] / (threshold + 1e-8)),
                    payload={
                        "record_index": rec["record_index"],
                        "record_id":    rec["record_id"],
                        "entropy":      rec["entropy"],
                        "threshold":    threshold,
                        "message":      rec.get("interpretation", "High entropy record"),
                    },
                )

        _track("entropy", "success", time.time() - t0)
        return results

    except HTTPException:
        _track("entropy", "client_error", time.time() - t0)
        raise
    except Exception as e:
        _track("entropy", "server_error", time.time() - t0)
        logger.exception("Per-record entropy error", job_id=req.job_id)
        raise HTTPException(500, f"Entropy computation failed: {e}")


# ── GET /status/{job_id} ──────────────────────────────────────────────────────

@router.get("/status/{job_id}", summary="Retrieve stored analytics for a job")
async def analytics_status(
    job_id: str,
    tenant=Depends(get_tenant),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Return all analytics results stored for a completed job."""
    # SECURITY-FIX 2026-10-15: tenant_id was a client supplied query parameter, so any caller could read
    # any tenant's analytics by passing ?tenant_id=. It now comes from the authenticated tenant.
    tenant_id = str(tenant.id)
    job = _get_job(job_id, tenant_id, db)
    artifacts = getattr(job, "artifacts", None) or {}
    if isinstance(artifacts, str):
        artifacts = json.loads(artifacts) if artifacts else {}

    analytics = artifacts.get("analytics", {})
    results   = {"job_id": job_id, "available": list(analytics.keys()), "results": {}}

    for key, storage_key in analytics.items():
        try:
            raw = storage.get(storage_key)  # FIXED: was storage.read()
            if raw:
                results["results"][key] = json.loads(raw)
        except Exception:
            results["results"][key] = {"error": "Could not load from storage", "key": storage_key}

    return results
