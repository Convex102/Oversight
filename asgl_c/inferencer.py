from __future__ import annotations

from dataclasses import asdict
from typing import Optional, Dict, Any, List, Tuple
from pathlib import Path
import pandas as pd
import numpy as np
import re

from .config import ASGLCConfig
from .schema import CanonicalSchema, ColumnRole, RoleGroup, InferenceResult
from .exceptions import SchemaInferenceError
from .logging_utils import get_logger
from .fingerprint import dataset_fingerprint
from .validators import validate_schema
from .debug import DebugSnapshotter

_TIME_PATTERNS = [
    re.compile(r"^\d{4}-\d{2}-\d{2}$"),  # YYYY-MM-DD
    re.compile(r"^\d{4}/\d{2}/\d{2}$"),
    re.compile(r"^\d{4}-\d{2}$"),         # YYYY-MM
    re.compile(r"^\d{4}$"),                # YYYY
]

_MONTH_NAMES = {"jan","feb","mar","apr","may","jun","jul","aug","sep","oct","nov","dec"}

class SchemaInferencer:
    """Infer a CanonicalSchema from a DataFrame.

    Phase-1 implementation:
    - Strong heuristics + confidence scoring
    - Optional registry-based reuse
    - Optional embeddings hook can be added later without changing contract
    """

    def __init__(self, config: ASGLCConfig, registry=None):
        self.config = config
        self.config.ensure_dirs()
        self.logger = get_logger("asgl_c.inferencer", self.config.log_dir, self.config.log_level)
        self.debugger = DebugSnapshotter(self.config.debug_dir)
        self.registry = registry

    def infer(self, df: pd.DataFrame, domain_hint: Optional[str] = None) -> InferenceResult:
        try:
            if df is None or df.shape[1] == 0:
                raise SchemaInferenceError("Empty dataframe")

            dom = domain_hint or self.config.domain_hint
            fp = dataset_fingerprint(df)

            # Prefer registry mapping if configured
            if self.registry and self.config.prefer_registry:
                cached = self.registry.get(fp)
                if cached:
                    self.logger.info(f"Using cached schema from registry (fp={fp[:8]})")
                    warnings = validate_schema(df, cached)
                    return InferenceResult(schema=cached, warnings=warnings, debug={"source": "registry", "fingerprint": fp, "debug_path": None})

            schema = CanonicalSchema(fingerprint=fp, domain_hint=dom, metadata={"rows": len(df), "cols": len(df.columns)})

            # Infer time column first
            time_role = self._infer_time_column(df)
            if time_role:
                schema.time_column = time_role

            # Infer IDs / text columns
            for col in df.columns:
                if schema.time_column and col == schema.time_column.column:
                    continue
                s = df[col]
                role = self._infer_id_or_text(col, s)
                if role:
                    if role.group == RoleGroup.id:
                        schema.ids[col] = role
                    else:
                        schema.texts[col] = role

            # Infer numeric roles: states / flows / drivers
            for col in df.columns:
                if schema.time_column and col == schema.time_column.column:
                    continue
                if col in schema.ids or col in schema.texts:
                    continue

                s = df[col]
                if not pd.api.types.is_numeric_dtype(s):
                    schema.unknowns[col] = ColumnRole(column=str(col), group=RoleGroup.unknown, confidence=0.2)
                    continue

                role = self._infer_numeric_role(col, s, dom)
                if role.group == RoleGroup.state:
                    schema.states[col] = role
                elif role.group == RoleGroup.flow:
                    schema.flows[col] = role
                else:
                    schema.drivers[col] = role

            warnings = validate_schema(df, schema)

            dbg_path = None

            # Persist registry
            if self.registry and self.config.persist_registry:
                self.registry.save(schema)

            # Debug snapshot
            if self.config.debug_enabled:
                dbg = {
                    "fingerprint": fp,
                    "domain_hint": dom,
                    "schema": self._schema_as_dict(schema),
                    "warnings": warnings,
                    "head": df.head(8).to_dict(orient="list"),
                    "dtypes": {str(c): str(df[c].dtype) for c in df.columns},
                }
                dbg_path = self.debugger.save(dbg, prefix="asglc_schema")
                self.logger.info(f"Saved debug snapshot: {dbg_path}")

            return InferenceResult(schema=schema, warnings=warnings, debug={"source": "inferred", "fingerprint": fp, "debug_path": dbg_path})

        except Exception as e:
            self.logger.exception("Schema inference failed")
            raise SchemaInferenceError(str(e))

    def _infer_time_column(self, df: pd.DataFrame) -> Optional[ColumnRole]:
        best = None
        best_score = 0.0

        for col in df.columns:
            s = df[col]
            score, evidence = self._time_score(col, s)
            if score > best_score:
                best_score = score
                best = (col, score, evidence)

        if best and best_score >= 0.65:
            col, score, evidence = best
            return ColumnRole(column=str(col), group=RoleGroup.time, subtype=evidence.get("frequency"), confidence=float(score), evidence=evidence)
        return None

    def _time_score(self, col: Any, s: pd.Series) -> Tuple[float, Dict[str, Any]]:
        evidence: Dict[str, Any] = {}
        name = str(col).lower()

        name_score = 0.0
        if any(k in name for k in ["date","dt","time","month","year","period","quarter","week"]):
            name_score = 0.35

        # Try datetime parsing
        nonnull = s.dropna()
        if nonnull.shape[0] == 0:
            return 0.0, {"reason": "empty"}

        parsed = pd.to_datetime(nonnull.astype(str), errors="coerce", utc=True)
        parse_rate = float(parsed.notna().mean())

        pattern_rate = 0.0
        try:
            vals = nonnull.astype(str).head(200).tolist()
            hits = 0
            for v in vals:
                if any(p.match(v.strip()) for p in _TIME_PATTERNS):
                    hits += 1
            pattern_rate = hits / max(1, len(vals))
        except Exception:
            pass

        score = name_score + 0.55 * max(parse_rate, pattern_rate)

        freq = self._infer_frequency_from_parsed(parsed.dropna())
        evidence.update({"name_score": name_score, "parse_rate": parse_rate, "pattern_rate": pattern_rate, "frequency": freq})

        return min(1.0, score), evidence

    def _infer_frequency_from_parsed(self, dt: pd.Series) -> str:
        try:
            if dt.shape[0] < 4:
                return "unknown"
            # sort and diff days
            d = dt.sort_values()
            diffs = d.diff().dropna().dt.total_seconds() / 86400.0
            med = float(np.median(diffs.values))
            if med <= 2:
                return "daily"
            if 25 <= med <= 35:
                return "monthly"
            if 80 <= med <= 110:
                return "quarterly"
            if 300 <= med <= 430:
                return "yearly"
            return "unknown"
        except Exception:
            return "unknown"

    def _infer_id_or_text(self, col: Any, s: pd.Series) -> Optional[ColumnRole]:
        name = str(col).lower()
        nonnull = s.dropna()
        if nonnull.shape[0] == 0:
            return None

        # Text-like columns
        if pd.api.types.is_string_dtype(s) or pd.api.types.is_object_dtype(s):
            uniq_ratio = float(nonnull.nunique() / max(1, nonnull.shape[0]))
            avg_len = float(nonnull.astype(str).str.len().mean())

            # ID heuristic: high uniqueness, short average length
            if uniq_ratio > 0.85 and avg_len < 24 and any(k in name for k in ["id","account","acct","ref","code","uid","isin","cusip","ticker"]):
                return ColumnRole(column=str(col), group=RoleGroup.id, confidence=0.8, evidence={"uniq_ratio": uniq_ratio, "avg_len": avg_len})

            # Generic ID: very high uniqueness and short strings
            if uniq_ratio > 0.92 and avg_len < 18:
                return ColumnRole(column=str(col), group=RoleGroup.id, confidence=0.65, evidence={"uniq_ratio": uniq_ratio, "avg_len": avg_len})

            # Text: long average length or low uniqueness
            if avg_len >= 30 or uniq_ratio < 0.6 or any(k in name for k in ["desc","description","note","narrative","comment"]):
                return ColumnRole(column=str(col), group=RoleGroup.text, confidence=0.7, evidence={"uniq_ratio": uniq_ratio, "avg_len": avg_len})

        return None

    def _infer_numeric_role(self, col: Any, s: pd.Series, domain_hint: Optional[str]) -> ColumnRole:
        # Evidence features
        x = s.dropna().astype(float)
        if x.shape[0] < 6:
            return ColumnRole(column=str(col), group=RoleGroup.driver, confidence=0.35, evidence={"reason": "too_few_points"})

        name = str(col).lower()
        mean_abs = float(np.mean(np.abs(x.values)))
        if mean_abs == 0:
            return ColumnRole(column=str(col), group=RoleGroup.driver, confidence=0.35, evidence={"reason": "all_zero"})

        # Volatility and delta characteristics
        diffs = np.diff(x.values)
        mean_diff = float(np.mean(np.abs(diffs))) if diffs.size else 0.0
        rel_diff = mean_diff / (mean_abs + 1e-9)

        # Stationarity-ish indicator: smaller rel_diff suggests stock/level
        # Larger rel_diff suggests flow/movement.
        balance_keywords = ["cash","balance","asset","liability","equity","inventory","debt","loan","reserve","capital"]
        flow_keywords = ["revenue","sales","turnover","cost","expense","cogs","profit","loss","ebit","margin","tax","interest","dividend"]

        kw_balance = any(k in name for k in balance_keywords)
        kw_flow = any(k in name for k in flow_keywords)

        score_state = 0.0
        score_flow = 0.0
        score_driver = 0.15  # default baseline

        # keyword boosts
        if kw_balance:
            score_state += 0.25
        if kw_flow:
            score_flow += 0.25

        # statistical boosts
        if rel_diff < 0.25:
            score_state += 0.45
        elif rel_diff > 0.55:
            score_flow += 0.45
        else:
            score_driver += 0.25

        # domain hints
        if (domain_hint or "").lower() in {"accountancy","accounts","audit"}:
            # In accounts, numeric columns are often either balances (BS) or flows (P&L)
            score_state += 0.05
            score_flow += 0.05

        # choose
        best_group = RoleGroup.driver
        best_score = score_driver
        subtype = None

        if score_state >= best_score:
            best_group = RoleGroup.state
            best_score = score_state
            subtype = "stock"
        if score_flow > best_score:
            best_group = RoleGroup.flow
            best_score = score_flow
            subtype = "flow"

        evidence = {
            "mean_abs": mean_abs,
            "mean_diff": mean_diff,
            "rel_diff": rel_diff,
            "kw_balance": kw_balance,
            "kw_flow": kw_flow,
            "score_state": score_state,
            "score_flow": score_flow,
            "score_driver": score_driver,
        }
        return ColumnRole(column=str(col), group=best_group, subtype=subtype, confidence=float(min(1.0, best_score)), evidence=evidence)

    def _schema_as_dict(self, s: CanonicalSchema) -> Dict[str, Any]:
        # compact representation for debugging
        def r(role: ColumnRole) -> dict:
            return {"column": role.column, "group": role.group.value, "subtype": role.subtype, "confidence": role.confidence}
        return {
            "fingerprint": s.fingerprint,
            "domain_hint": s.domain_hint,
            "time_column": r(s.time_column) if s.time_column else None,
            "ids": {k: r(v) for k, v in s.ids.items()},
            "states": {k: r(v) for k, v in s.states.items()},
            "flows": {k: r(v) for k, v in s.flows.items()},
            "drivers": {k: r(v) for k, v in s.drivers.items()},
            "texts": {k: r(v) for k, v in s.texts.items()},
            "unknowns": {k: r(v) for k, v in s.unknowns.items()},
            "metadata": s.metadata,
        }