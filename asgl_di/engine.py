from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, List
import json
import datetime
import os

from .config import DecisionIntelligenceConfig
from .uncertainty import compute_decision_confidence
from .graph_rag import extract_relevant_subgraph
from .macro_regime import infer_macro_regime

@dataclass
class DecisionIntelligenceEngine:
    cfg: DecisionIntelligenceConfig

    @classmethod
    def from_env(cls, output_root: Path) -> "DecisionIntelligenceEngine":
        cfg = DecisionIntelligenceConfig.from_env(output_root)
        cfg.ensure_dirs()
        return cls(cfg)

    def _rule_universal_v1(
        self,
        *,
        gold_df,
        asgl_a: Dict[str, Any],
        dq: Dict[str, Any],
        asgl_c: Dict[str, Any],
        ocr: Dict[str, Any],
        macro: Dict[str, Any],
        uncertainty: Dict[str, Any],
        subgraph: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        exceptions: List[Dict[str, Any]] = []
        severity = 0.0

        # 1) Uncertainty gating
        gate = uncertainty.get("gating", "warn")
        conf = float(uncertainty.get("confidence", 0.75))
        ood = float(uncertainty.get("ood_risk", 0.25))
        if gate == "block":
            sev = max(0.75, 1.0-conf)
            severity = max(severity, sev)
            exceptions.append({
                "type": "confidence_block",
                "message": f"Low confidence gating triggered (confidence={conf:.2f}, ood_risk={ood:.2f}).",
                "severity": sev,
                "recommended_action": "Hold sign-off. Confirm workbook template mapping, OCR evidence, and key extraction tables."
            })
        elif gate == "warn":
            sev = max(0.45, 1.0-conf)
            severity = max(severity, sev)
            exceptions.append({
                "type": "confidence_warn",
                "message": f"Moderate confidence (confidence={conf:.2f}, ood_risk={ood:.2f}).",
                "severity": sev,
                "recommended_action": "Proceed with caution. Reviewer should focus on flagged and low-confidence extractions."
            })

        # 2) Macro context prior
        if macro.get("status") == "ok" and macro.get("regime") == "stress":
            sev = 0.55
            severity = max(severity, sev)
            exceptions.append({
                "type": "macro_regime_stress",
                "message": f"Macro regime indicates stress (p_stress={macro.get('p_stress')}).",
                "severity": sev,
                "recommended_action": "Tighten tolerances/materiality and increase review depth on liquidity/leverage items."
            })

        # 3) ASGL-A flags
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

        # 4) ASGL-C coverage/confidence (if provided)
        def _f(x):
            try:
                return float(x)
            except Exception:
                return None
        cov = _f(asgl_c.get("coverage") or asgl_c.get("mapping_coverage"))
        mconf = _f(asgl_c.get("confidence") or asgl_c.get("mapping_confidence"))
        if cov is not None and cov < 0.70:
            sev = min(1.0, (0.70 - cov) / 0.70 + 0.35)
            severity = max(severity, sev)
            exceptions.append({
                "type": "semantic_coverage_low",
                "message": f"ASGL-C mapping coverage low ({cov:.2f}).",
                "severity": sev,
                "recommended_action": "Confirm control-doc/COA selection and update firm template profile for key tables."
            })
        if mconf is not None and mconf < 0.70:
            sev = min(1.0, (0.70 - mconf) / 0.70 + 0.35)
            severity = max(severity, sev)
            exceptions.append({
                "type": "semantic_confidence_low",
                "message": f"ASGL-C mapping confidence low ({mconf:.2f}).",
                "severity": sev,
                "recommended_action": "Review semantic mapping outputs and manually validate a sample of mapped line items."
            })

        decision_score = max(0.0, 1.0 - severity)

        actions: List[str] = []
        if severity >= self.cfg.severity_threshold:
            actions.append("Hold sign-off: requires reviewer attention.")
        if subgraph and subgraph.get("nodes"):
            actions.append("Attach evidence subgraph (GraphRAG) to reviewer pack for traceability.")
        if not actions:
            actions.append("Proceed with standard reviewer checks (no critical exceptions detected).")

        return {
            "ruleset": self.cfg.ruleset,
            "decision_score": round(decision_score, 4),
            "severity": round(severity, 4),
            "uncertainty": uncertainty,
            "macro_context": macro,
            "exceptions": exceptions,
            "evidence_subgraph": subgraph,
            "recommended_actions": actions,
        }

    def run(
        self,
        *,
        gold_df=None,
        asgl_a: Optional[Dict[str, Any]] = None,
        dq: Optional[Dict[str, Any]] = None,
        asgl_c: Optional[Dict[str, Any]] = None,
        ocr: Optional[Dict[str, Any]] = None,
        meta: Optional[Dict[str, Any]] = None,
        graph_for_rag: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        if not self.cfg.enabled:
            return {"status": "disabled"}

        asgl_a = asgl_a or {}
        dq = dq or {}
        asgl_c = asgl_c or {}
        ocr = ocr or {}
        meta = meta or {}

        uncertainty = compute_decision_confidence(dq=dq, asgl_a=asgl_a, asgl_c=asgl_c, ocr=ocr)

        macro_csv = os.getenv("ASGL_MACRO_CSV")
        macro_res = infer_macro_regime(Path(macro_csv) if macro_csv else None)
        macro = {
            "status": macro_res.status,
            "regime": macro_res.regime,
            "p_stress": macro_res.p_stress,
            "p_normal": macro_res.p_normal,
            "drivers": macro_res.drivers,
            "note": macro_res.note,
        }

        # GraphRAG
        subgraph = None
        try:
            queries = []
            flags = asgl_a.get("flags") or asgl_a.get("issues") or []
            if isinstance(flags, list):
                queries.extend([str(f) for f in flags[:20]])
            if meta:
                queries.append(str(meta))
            g = graph_for_rag or asgl_c.get("graph") or asgl_c.get("semantic_graph")
            if g:
                subgraph = extract_relevant_subgraph(g, queries=queries, max_nodes=40, hop_depth=2)
        except Exception:
            subgraph = None

        out = self._rule_universal_v1(
            gold_df=gold_df,
            asgl_a=asgl_a,
            dq=dq,
            asgl_c=asgl_c,
            ocr=ocr,
            macro=macro,
            uncertainty=uncertainty,
            subgraph=subgraph
        )

        payload = {
            "status": "ok",
            "meta": meta,
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "output": out,
        }

        try:
            fname = meta.get("adapter") or meta.get("table_id") or "decision_intelligence"
            safe = "".join(ch if ch.isalnum() or ch in ("-","_") else "_" for ch in str(fname))[:120]
            p = self.cfg.output_root / f"{safe}__di.json"
            p.write_text(json.dumps(payload, indent=2))
            payload["artifact_json"] = str(p)
        except Exception:
            pass

        return payload
