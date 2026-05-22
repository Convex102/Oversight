from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
import datetime as dt
import math

import pandas as pd

from .pack_schema import PackSchema, PackField, load_pack_schema, is_row_based
from .semantic_match import best_label_match, normalize, fuzzy_ratio


def _parse_scalar(v: Any, t: str) -> Tuple[Any, Optional[str]]:
    """Parse a scalar to the target type. Returns (value, error)."""
    if v is None:
        return None, None
    typ = (t or "string").lower().strip()
    try:
        if typ in ("string", "text"):
            return str(v).strip(), None
        if typ in ("number", "float", "decimal", "int", "integer"):
            if isinstance(v, (int, float)) and not (isinstance(v, float) and math.isnan(v)):
                return float(v), None
            s = str(v).strip().replace(",", "")
            if s == "":
                return None, None
            return float(s), None
        if typ in ("date", "datetime"):
            # pandas handles a lot of UK formats
            d = pd.to_datetime(v, errors="coerce", dayfirst=True)
            if pd.isna(d):
                return None, f"Could not parse date: {v}"
            return d.date().isoformat(), None
        if typ in ("bool", "boolean"):
            if isinstance(v, bool):
                return v, None
            s = str(v).strip().lower()
            if s in ("true", "t", "yes", "y", "1"):
                return True, None
            if s in ("false", "f", "no", "n", "0"):
                return False, None
            return None, f"Could not parse boolean: {v}"
        # fallback
        return v, None
    except Exception as e:
        return None, str(e)


def _apply_priors(conf: float, key: str, priors: Optional[Dict[str, Any]]) -> float:
    if not priors:
        return max(0.05, min(0.98, float(conf)))
    bias = 0.0
    try:
        bias = float((priors.get("field_bias") or {}).get(key, 0.0))
    except Exception:
        bias = 0.0
    return max(0.05, min(0.98, float(conf) + bias))


def _field_match_score(field: PackField, label: str) -> Tuple[float, str]:
    """Return (score, method) where score in [0,1]."""
    lab = str(label or "").strip()
    if not lab:
        return 0.0, "none"
    # exact / alias
    for nm in field.all_names():
        if normalize(nm) == normalize(lab):
            return 1.0, "exact"
    # fuzzy
    best = 0.0
    for nm in field.all_names():
        best = max(best, fuzzy_ratio(nm, lab))
    if best >= 0.82:
        return best, "fuzzy"
    return best, "weak"


def canonicalize(
    df: Optional[pd.DataFrame],
    schema_dict: Dict[str, Any],
    *,
    template_values: Optional[Dict[str, Any]] = None,
    asgl_a_out: Optional[Dict[str, Any]] = None,
    priors: Optional[Dict[str, Any]] = None,
    max_rows_sample: int = 200,
) -> Dict[str, Any]:
    """Canonicalize inputs into ASGL-C output.

    This function is intentionally deterministic and does not require external services.
    """
    schema = load_pack_schema(schema_dict)
    mode = "row" if (df is not None and is_row_based(schema)) else "fact"

    now = dt.datetime.utcnow().isoformat() + "Z"
    out: Dict[str, Any] = {
        "status": "ok",
        "ts": now,
        "schema": {"name": schema.name, "version": schema.version, "description": schema.description},
        "mode": mode,
        "quality": {},
        "validation": {"status": "ok", "violations": [], "violations_count": 0},
        "mapping": {"entries": []},
    }

    violations: List[Dict[str, Any]] = []

    if mode == "row":
        if df is None:
            out["status"] = "skipped"
            out["reason"] = "no_dataframe"
            return out

        cols = [str(c) for c in df.columns]
        mapping: Dict[str, Dict[str, Any]] = {}

        for f in (schema.fields or []):
            # best match a column for this field
            candidates = []
            for nm in f.all_names():
                candidates.append(nm)
            # create a set of column names as candidates, match each field name against columns
            best_col = None
            best_score = 0.0
            best_method = "none"
            for col in cols:
                score, method = _field_match_score(f, col)
                if score > best_score:
                    best_score = score
                    best_col = col
                    best_method = method
            if best_col and best_score >= 0.70:
                base_conf = 0.92 if best_method == "exact" else (0.84 if best_method == "fuzzy" else 0.70)
                conf = _apply_priors(base_conf * float(best_score), f.key, priors)
                mapping[f.key] = {"source": best_col, "method": best_method, "score": float(best_score), "confidence": conf}
            else:
                mapping[f.key] = {"source": None, "method": "missing", "score": float(best_score), "confidence": _apply_priors(0.20, f.key, priors)}

        # sample rows
        sample: List[Dict[str, Any]] = []
        parse_errors = 0
        required_missing = 0

        for i in range(min(int(len(df)), int(max_rows_sample))):
            row = df.iloc[i]
            rec: Dict[str, Any] = {}
            for f in (schema.fields or []):
                src = mapping.get(f.key, {}).get("source")
                if src:
                    val, err = _parse_scalar(row.get(src), f.type)
                else:
                    val, err = None, None
                rec[f.key] = val
                if f.required and (val is None or val == ""):
                    required_missing += 1
                if err:
                    parse_errors += 1
                    violations.append({
                        "type": "type_parse_error",
                        "field": f.key,
                        "source": src,
                        "row_index": i,
                        "message": err,
                        "severity": 0.45,
                    })
            sample.append(rec)

        # required checks (at least one non-empty in sample)
        for f in (schema.fields or []):
            if not f.required:
                continue
            any_present = any((r.get(f.key) is not None and r.get(f.key) != "") for r in sample)
            if not any_present:
                violations.append({
                    "type": "required_missing",
                    "field": f.key,
                    "message": "Required field not populated in sampled rows.",
                    "severity": 0.75,
                })

        out["rows"] = {
            "row_count": int(len(df)),
            "sample_count": int(len(sample)),
            "sample": sample,
        }
        out["mapping"]["entries"] = [
            {"key": k, **v} for k, v in mapping.items()
        ]
        avg_conf = sum([float(v.get("confidence", 0.0)) for v in mapping.values()]) / max(1, len(mapping))
        out["quality"] = {
            "avg_mapping_confidence": round(avg_conf, 4),
            "parse_errors_in_sample": int(parse_errors),
            "required_missing_in_sample": int(required_missing),
        }

    else:
        # FACT mode — combine template values + ASGL-A KPIs into a canonical fact bundle.
        candidates: List[Tuple[str, Any, Dict[str, Any]]] = []

        # 1) template mapped values (cell selections)
        tv = template_values or {}
        for k, v in tv.items():
            meta = {}
            val = v
            if isinstance(v, dict) and "value" in v:
                val = v.get("value")
                meta = {kk: v.get(kk) for kk in ("sheet", "range", "aggregation") if kk in v}
            candidates.append((str(k), val, {"source": "template", **meta}))

        # 2) ASGL-A KPIs
        ao = asgl_a_out or {}
        kpis = {}
        try:
            kpis = (ao.get("kpis") or {}) if isinstance(ao, dict) else {}
        except Exception:
            kpis = {}
        for k, v in (kpis or {}).items():
            candidates.append((str(k), v, {"source": "asgl_a_kpi"}))

        # match candidates to schema fields
        facts: Dict[str, Dict[str, Any]] = {}
        map_entries: List[Dict[str, Any]] = []
        low_conf: List[Dict[str, Any]] = []

        for label, value, meta in candidates:
            best_field = None
            best_score = 0.0
            best_method = "none"
            for f in (schema.fields or []):
                sc, mth = _field_match_score(f, label)
                if sc > best_score:
                    best_score = sc
                    best_field = f
                    best_method = mth

            if best_field is None:
                continue

            base_conf = 0.92 if best_method == "exact" else (0.80 if best_method == "fuzzy" else 0.55)
            conf = _apply_priors(base_conf * float(best_score), best_field.key, priors)

            parsed, err = _parse_scalar(value, best_field.type)
            if err:
                violations.append({
                    "type": "type_parse_error",
                    "field": best_field.key,
                    "label": label,
                    "message": err,
                    "severity": 0.45,
                })

            # keep the best evidence for each field
            prev = facts.get(best_field.key)
            if (prev is None) or float(conf) > float(prev.get("confidence", 0.0)):
                facts[best_field.key] = {
                    "value": parsed,
                    "type": best_field.type,
                    "confidence": float(conf),
                    "evidence": [{
                        "label": label,
                        "raw_value": value,
                        **meta,
                    }],
                }

            map_entries.append({
                "key": best_field.key,
                "source_label": label,
                "method": best_method,
                "score": float(best_score),
                "confidence": float(conf),
                "source": meta.get("source"),
            })

        # required checks
        for req in schema.required_keys():
            if req not in facts or facts[req].get("value") in (None, ""):
                violations.append({
                    "type": "required_missing",
                    "field": req,
                    "message": "Required field missing.",
                    "severity": 0.75,
                })

        # low confidence queue suggestions
        threshold = 0.72
        for k, f in facts.items():
            if float(f.get("confidence", 0.0)) < threshold:
                low_conf.append({
                    "field": k,
                    "proposed_value": f.get("value"),
                    "confidence": float(f.get("confidence", 0.0)),
                    "evidence": f.get("evidence"),
                    "reason": "low_confidence_fact",
                })

        avg_conf = (sum(float(v.get("confidence", 0.0)) for v in facts.values()) / max(1, len(facts))) if facts else 0.0
        out["facts"] = facts
        out["mapping"]["entries"] = map_entries
        out["quality"] = {
            "facts_count": int(len(facts)),
            "avg_fact_confidence": round(avg_conf, 4),
            "low_confidence_count": int(len(low_conf)),
        }
        out["review_queue_suggestions"] = low_conf

    out["validation"] = {
        "status": "ok" if not violations else "issues",
        "violations": violations[:250],
        "violations_count": int(len(violations)),
    }
    return out
