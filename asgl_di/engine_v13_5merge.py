from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, List
import json
import datetime

from .config import DecisionIntelligenceConfig

def _safe_float(x, default=None):
    try:
        return float(x)
    except Exception:
        return default

@dataclass
class DecisionIntelligenceEngine:
    """Deterministic decision intelligence engine.

    Consumes existing ASGL artifacts (DQ, ASGL-A, ASGL-C graph, extracted features)
    and produces a structured exception ledger + recommended actions.

    This intentionally does **not** learn from client data and does not update model weights.
    """
    cfg: DecisionIntelligenceConfig

    @classmethod
    def from_env(cls, output_root: Path) -> "DecisionIntelligenceEngine":
        cfg = DecisionIntelligenceConfig.from_env(output_root)
        cfg.ensure_dirs()
        return cls(cfg)

    def _graph_signals(self, graph) -> Dict[str, Any]:
        if graph is None:
            return {"present": False}
        nodes = getattr(graph, "nodes", None)
        edges = getattr(graph, "edges", None)
        n_nodes = len(nodes) if isinstance(nodes, dict) else (len(nodes) if nodes else 0)
        n_edges = len(edges) if isinstance(edges, list) else (len(edges) if edges else 0)

        node_confs = []
        if isinstance(nodes, dict):
            for n in nodes.values():
                c = getattr(n, "confidence", None)
                if c is not None:
                    node_confs.append(float(c))
        edge_confs = []
        if isinstance(edges, list):
            for e in edges:
                c = getattr(e, "confidence", None)
                if c is not None:
                    edge_confs.append(float(c))

        return {
            "present": True,
            "nodes": n_nodes,
            "edges": n_edges,
            "min_node_conf": min(node_confs) if node_confs else None,
            "avg_node_conf": (sum(node_confs)/len(node_confs)) if node_confs else None,
            "min_edge_conf": min(edge_confs) if edge_confs else None,
            "avg_edge_conf": (sum(edge_confs)/len(edge_confs)) if edge_confs else None,
        }

    def _rule_universal_v1(
        self,
        gold_df,
        asgl_a: Dict[str, Any],
        dq: Dict[str, Any],
        asgl_c_graph=None,
        features=None,
    ) -> Dict[str, Any]:
        exceptions: List[Dict[str, Any]] = []
        severity = 0.0

        # 1) Data Quality
        dq_score = None
        if isinstance(dq, dict):
            dq_score = dq.get("score") or dq.get("overall") or dq.get("overall_score")
        dq_score_f = _safe_float(dq_score, None)
        if dq_score_f is not None and dq_score_f < 0.75:
            sev = min(1.0, (0.75 - dq_score_f) / 0.75)
            severity = max(severity, sev)
            exceptions.append({
                "type": "data_quality",
                "message": f"Data quality score below expected threshold ({dq_score_f:.2f}).",
                "severity": sev,
                "recommended_action": "Confirm authoritative sheet/table, header row, sign convention, and missingness."
            })

        # 2) ASGL-A flags
        flags = []
        if isinstance(asgl_a, dict):
            flags = asgl_a.get("flags") or asgl_a.get("issues") or []
        if isinstance(flags, list) and flags:
            sev = min(1.0, 0.25 + 0.05*len(flags))
            severity = max(severity, sev)
            exceptions.append({
                "type": "asgl_a_flags",
                "message": f"ASGL-A produced {len(flags)} review flags.",
                "severity": sev,
                "recommended_action": "Review ASGL-A flag list and validate referenced items against source evidence."
            })

        # 3) ASGL-C semantic graph signals (confidence/coverage)
        gs = self._graph_signals(asgl_c_graph)
        if gs.get("present"):
            min_node = gs.get("min_node_conf")
            if min_node is not None and float(min_node) < 0.45:
                sev = min(1.0, 0.55)
                severity = max(severity, sev)
                exceptions.append({
                    "type": "semantic_low_confidence",
                    "message": f"ASGL-C semantic graph contains low-confidence nodes (min={float(min_node):.2f}).",
                    "severity": sev,
                    "recommended_action": "Check concept mapping (COA/control docs), and confirm that the correct table is mapped to the correct schema."
                })
            if gs.get("nodes", 0) < 1:
                sev = min(1.0, 0.5)
                severity = max(severity, sev)
                exceptions.append({
                    "type": "semantic_no_nodes",
                    "message": "ASGL-C semantic graph produced no nodes (no canonical concepts inferred).",
                    "severity": sev,
                    "recommended_action": "Provide or select an appropriate control doc (COA/reg taxonomy) and re-run mapping."
                })

        # 4) Basic balancing check (if Dr/Cr present)
        if gold_df is not None:
            cols = [str(c).strip().lower() for c in getattr(gold_df, "columns", [])]
            if any("debit" in c or c in ("dr","debits") for c in cols) and any("credit" in c or c in ("cr","credits") for c in cols):
                try:
                    import pandas as pd
                    debit_col = next(c for c in gold_df.columns if str(c).strip().lower() in ("debit","dr","debits") or "debit" in str(c).lower())
                    credit_col = next(c for c in gold_df.columns if str(c).strip().lower() in ("credit","cr","credits") or "credit" in str(c).lower())
                    dsum = pd.to_numeric(gold_df[debit_col], errors="coerce").fillna(0).sum()
                    csum = pd.to_numeric(gold_df[credit_col], errors="coerce").fillna(0).sum()
                    diff = float(dsum - csum)
                    if abs(diff) > max(1.0, 0.001*max(abs(dsum), abs(csum))):
                        sev = 0.6
                        severity = max(severity, sev)
                        exceptions.append({
                            "type": "trial_balance_not_netting",
                            "message": f"Debit/Credit totals do not net within tolerance (Dr-Cr = {diff:,.2f}).",
                            "severity": sev,
                            "recommended_action": "Confirm whether credits are negatives in a single balance column or whether journals are incomplete."
                        })
                except Exception:
                    pass

        decision_score = max(0.0, 1.0 - severity)
        actions = []
        if severity >= self.cfg.severity_threshold:
            actions.append("Hold sign-off: requires reviewer attention.")
        if exceptions:
            actions.append("Generate exception ledger for reviewer pack.")
        if not actions:
            actions.append("Proceed with standard reviewer checks (no critical exceptions detected).")

        return {
            "ruleset": self.cfg.ruleset,
            "decision_score": round(decision_score, 4),
            "severity": round(severity, 4),
            "graph_signals": gs,
            "exceptions": exceptions,
            "recommended_actions": actions,
        }

    def run(
        self,
        *,
        gold_df=None,
        asgl_a: Optional[Dict[str, Any]] = None,
        dq: Optional[Dict[str, Any]] = None,
        asgl_c_graph=None,
        features=None,
        meta: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        if not self.cfg.enabled:
            return {"status": "disabled"}

        asgl_a = asgl_a or {}
        dq = dq or {}
        meta = meta or {}

        # currently supported rulesets
        if self.cfg.ruleset in ("finance_v1", "universal_v1", "all_v1"):
            out = self._rule_universal_v1(gold_df, asgl_a, dq, asgl_c_graph=asgl_c_graph, features=features)
        else:
            out = self._rule_universal_v1(gold_df, asgl_a, dq, asgl_c_graph=asgl_c_graph, features=features)

        payload = {
            "status": "ok",
            "meta": meta,
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "output": out,
        }

        # persist JSON alongside existing artifacts
        try:
            fname = meta.get("adapter") or meta.get("table_id") or "decision_intelligence"
            safe = "".join(ch if ch.isalnum() or ch in ("-","_") else "_" for ch in str(fname))[:120]
            p = self.cfg.output_root / f"{safe}__di.json"
            p.write_text(json.dumps(payload, indent=2))
            payload["artifact_json"] = str(p)
        except Exception:
            pass

        return payload
