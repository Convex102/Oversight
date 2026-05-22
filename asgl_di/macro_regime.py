from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
import numpy as np

@dataclass
class MacroRegimeResult:
    status: str
    regime: str
    p_stress: float
    p_normal: float
    drivers: Dict[str, Any]
    note: str

def _read_csv_timeseries(path: Path) -> Optional[np.ndarray]:
    try:
        import pandas as pd
        df = pd.read_csv(path)
        for col in ["value","close","price","index","level"]:
            if col in df.columns:
                s = pd.to_numeric(df[col], errors="coerce").dropna()
                return s.values.astype(float)
        num = df.select_dtypes(include=["number"])
        if num.shape[1] >= 1:
            s = num.iloc[:,0].dropna()
            return s.values.astype(float)
    except Exception:
        return None
    return None

def _fit_two_state_hmm_1d(x: np.ndarray, iters: int = 30) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    x = x.reshape(-1,1)
    n = x.shape[0]
    pi = np.array([0.5, 0.5])
    A = np.array([[0.95,0.05],[0.05,0.95]])
    mu = np.array([np.percentile(x,25), np.percentile(x,75)]).astype(float)
    sig = np.array([np.std(x)+1e-6, np.std(x)+1e-6]).astype(float)

    def log_normpdf(v, m, s):
        return -0.5*np.log(2*np.pi*s*s) - 0.5*((v-m)/s)**2

    for _ in range(iters):
        B = np.vstack([
            log_normpdf(x[:,0], mu[0], sig[0]),
            log_normpdf(x[:,0], mu[1], sig[1])
        ]).T

        logA = np.log(A+1e-12); logpi = np.log(pi+1e-12)
        alpha = np.zeros((n,2))
        alpha[0] = logpi + B[0]
        for t in range(1,n):
            for j in range(2):
                alpha[t,j] = B[t,j] + np.logaddexp(alpha[t-1,0] + logA[0,j], alpha[t-1,1] + logA[1,j])

        beta = np.zeros((n,2))
        for t in range(n-2,-1,-1):
            for i in range(2):
                beta[t,i] = np.logaddexp(logA[i,0] + B[t+1,0] + beta[t+1,0],
                                         logA[i,1] + B[t+1,1] + beta[t+1,1])

        ll = np.logaddexp(alpha[-1,0], alpha[-1,1])
        gamma = np.exp(alpha + beta - ll)
        gamma = gamma / gamma.sum(axis=1, keepdims=True)

        xi = np.zeros((n-1,2,2))
        for t in range(n-1):
            denom = np.logaddexp.reduce([
                alpha[t,0] + logA[0,0] + B[t+1,0] + beta[t+1,0],
                alpha[t,0] + logA[0,1] + B[t+1,1] + beta[t+1,1],
                alpha[t,1] + logA[1,0] + B[t+1,0] + beta[t+1,0],
                alpha[t,1] + logA[1,1] + B[t+1,1] + beta[t+1,1],
            ])
            for i in range(2):
                for j in range(2):
                    xi[t,i,j] = np.exp(alpha[t,i] + logA[i,j] + B[t+1,j] + beta[t+1,j] - denom)

        pi = gamma[0]
        A = xi.sum(axis=0)
        A = A / A.sum(axis=1, keepdims=True)

        for k in range(2):
            w = gamma[:,k]
            wsum = w.sum() + 1e-12
            mu[k] = (w * x[:,0]).sum() / wsum
            var = (w * (x[:,0] - mu[k])**2).sum() / wsum
            sig[k] = np.sqrt(max(var, 1e-8))

    params = np.array([[mu[0], sig[0]],[mu[1], sig[1]]], dtype=float)
    return pi, A, params

def infer_macro_regime(macro_csv: Optional[Path]) -> MacroRegimeResult:
    if macro_csv is None or not Path(macro_csv).exists():
        return MacroRegimeResult("unknown","unknown",0.5,0.5,{}, "No macro time series provided (set ASGL_MACRO_CSV).")

    x = _read_csv_timeseries(Path(macro_csv))
    if x is None or len(x) < 80:
        return MacroRegimeResult("unknown","unknown",0.5,0.5,{}, "Macro CSV unreadable or too short.")

    r = np.diff(np.log(np.clip(x, 1e-12, None)))
    window = 20
    feat = np.array([np.std(r[max(0,i-window):i]) for i in range(1, len(r)+1)])
    feat = np.nan_to_num(feat, nan=float(np.nanmedian(feat)))

    pi, A, params = _fit_two_state_hmm_1d(feat, iters=30)
    sig0, sig1 = params[0,1], params[1,1]
    stress_state = 0 if sig0 >= sig1 else 1

    # posterior for last observation
    def normpdf(v,m,s):
        return (1.0/(np.sqrt(2*np.pi)*s)) * np.exp(-0.5*((v-m)/s)**2)
    last = float(feat[-1])
    e0 = normpdf(last, float(params[0,0]), float(params[0,1]))
    e1 = normpdf(last, float(params[1,0]), float(params[1,1]))
    post = np.array([pi[0]*e0, pi[1]*e1])
    post = post / (post.sum() + 1e-12)

    p_stress = float(post[stress_state])
    p_normal = float(1.0 - p_stress)
    regime = "stress" if p_stress >= 0.5 else "normal"

    return MacroRegimeResult(
        "ok",
        regime,
        round(p_stress,4),
        round(p_normal,4),
        {
            "feature": "rolling_log_return_vol",
            "last_feature_value": last,
            "state_params": {"state0": {"mu": float(params[0,0]), "sigma": float(params[0,1])},
                             "state1": {"mu": float(params[1,0]), "sigma": float(params[1,1])}},
            "transition_matrix": A.tolist()
        },
        "2-state Gaussian HMM (HMM-lite) on rolling return volatility."
    )
