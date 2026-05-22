from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
import json
import datetime

@dataclass
class ASGLV13DecisionHub:
    output_root: Path

    def __post_init__(self):
        self.output_root.mkdir(parents=True, exist_ok=True)
        self._table_results: List[Dict[str, Any]] = []
        self._macro: Optional[Dict[str, Any]] = None

    def add_table_result(self, di_payload: Dict[str, Any]):
        if not isinstance(di_payload, dict):
            return
        self._table_results.append(di_payload)
        try:
            mc = di_payload.get("output", {}).get("macro_context")
            if isinstance(mc, dict) and mc.get("status") == "ok":
                self._macro = mc
        except Exception:
            pass

    def summarize(self) -> Dict[str, Any]:
        severities = []
        scores = []
        blocks = 0
        warns = 0
        exceptions = 0
        for p in self._table_results:
            out = p.get("output", {}) if isinstance(p, dict) else {}
            sev = out.get("severity")
            sc = out.get("decision_score")
            unc = out.get("uncertainty", {}) if isinstance(out.get("uncertainty"), dict) else {}
            gating = unc.get("gating")
            if gating == "block":
                blocks += 1
            elif gating == "warn":
                warns += 1
            if isinstance(out.get("exceptions"), list):
                exceptions += len(out.get("exceptions"))
            try:
                if sev is not None: severities.append(float(sev))
            except Exception:
                pass
            try:
                if sc is not None: scores.append(float(sc))
            except Exception:
                pass

        overall_sev = max(severities) if severities else 0.0
        overall_score = min(scores) if scores else 1.0

        rec = []
        if blocks > 0:
            rec.append("Hold sign-off: one or more tables are confidence-blocked.")
        if self._macro and self._macro.get("regime") == "stress":
            rec.append("Macro stress regime: tighten tolerances and escalate review.")
        if not rec:
            rec.append("Proceed with standard reviewer checks.")

        summary = {
            "status": "ok",
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "overall_severity": round(float(overall_sev), 4),
            "overall_decision_score": round(float(overall_score), 4),
            "table_count": len(self._table_results),
            "gating": {"block_tables": blocks, "warn_tables": warns},
            "exceptions_count": exceptions,
            "macro_context": self._macro,
            "recommended_actions": rec
        }

        try:
            p = self.output_root / "asgl_v13_decision_summary.json"
            p.write_text(json.dumps(summary, indent=2))
            summary["artifact_json"] = str(p)
        except Exception:
            pass

        return summary
