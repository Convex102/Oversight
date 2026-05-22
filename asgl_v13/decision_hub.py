from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
import json
import datetime

from .config import ASGLV13Config
from asgl_di import DecisionIntelligenceEngine, DecisionIntelligenceConfig

@dataclass
class ASGLV13DecisionHub:
    """Global Decision Intelligence orchestrator (ASGL v13).

    - Runs a DI pass per extracted table (via asgl_di)
    - Aggregates results into a single engagement-level summary
    - Writes a versioned JSON artifact for audit/traceability

    This remains deterministic and does not learn from client data.
    """
    cfg: ASGLV13Config
    per_table: List[Dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_env(cls, output_root: Path) -> "ASGLV13DecisionHub":
        cfg = ASGLV13Config.from_env(output_root)
        output_root.mkdir(parents=True, exist_ok=True)
        return cls(cfg=cfg)

    def ingest(self, record: Dict[str, Any]):
        if not self.cfg.enabled:
            return
        self.per_table.append(record)

    def finalize(self) -> Dict[str, Any]:
        if not self.cfg.enabled:
            return {"status": "disabled"}

        # Aggregate severity by max (conservative)
        severities = []
        decisions = []
        exceptions_total = 0
        for r in self.per_table:
            out = (r.get("decision_intelligence") or {}).get("output") if isinstance(r.get("decision_intelligence"), dict) else None
            if isinstance(out, dict):
                sev = out.get("severity")
                if sev is not None:
                    try:
                        severities.append(float(sev))
                    except Exception:
                        pass
                ds = out.get("decision_score")
                if ds is not None:
                    try:
                        decisions.append(float(ds))
                    except Exception:
                        pass
                exc = out.get("exceptions") or []
                if isinstance(exc, list):
                    exceptions_total += len(exc)

        overall_severity = max(severities) if severities else 0.0
        overall_decision = min(decisions) if decisions else 1.0  # conservative: worst table score

        summary = {
            "status": "ok",
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "ruleset": self.cfg.ruleset,
            "tables_processed": len(self.per_table),
            "overall_severity": round(float(overall_severity), 4),
            "overall_decision_score": round(float(overall_decision), 4),
            "exceptions_total": int(exceptions_total),
            "recommended_next_step": (
                "Hold sign-off / escalate reviewer" if overall_severity >= 0.65 else "Proceed with reviewer checks"
            ),
        }

        payload = {
            "meta": {"engine": "ASGL-V13-DecisionHub"},
            "summary": summary,
            "per_table": self.per_table,
        }

        try:
            outp = self.cfg.output_root / self.cfg.summary_filename
            outp.write_text(json.dumps(payload, indent=2))
            summary["artifact_json"] = str(outp)
        except Exception:
            pass

        return payload
