from __future__ import annotations

import io
import json
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

from .schema import PensionsCanonical
from .validators import ValidationFinding

logger = logging.getLogger("asgl.pensions.exporters")


def export_trustee_evidence_bundle(
    *,
    canon: PensionsCanonical,
    findings: List[ValidationFinding],
    fingerprint_key: str,
    tenant_id: str,
    scheme_id: str,
    template_name: str,
) -> Dict[str, Any]:
    """
    Phase 6: produces a trustee/audit evidence bundle payload.
    Keep it simple: a structured JSON export with:
     - summary metrics
     - findings
     - canonical snapshot
     - provenance header

    This is intentionally deterministic and easy to attest.
    """
    now = datetime.utcnow().isoformat() + "Z"
    summary = {
        "generated_at": now,
        "tenant_id": tenant_id,
        "scheme_id": scheme_id,
        "pack_id": "ASGL-P",
        "template_name": template_name,
        "fingerprint_key": fingerprint_key,
        "counts": {
            "members": len(canon.members),
            "services": len(canon.services),
            "tranches": len(getattr(canon, "tranches", [])),
            "results": len(canon.results),
            "events": len(getattr(canon, "events", [])),
            "findings": len(findings),
        },
        "severity_breakdown": {
            "ERROR": sum(1 for f in findings if f.severity == "ERROR"),
            "WARN": sum(1 for f in findings if f.severity == "WARN"),
            "INFO": sum(1 for f in findings if f.severity == "INFO"),
        },
    }

    payload = {
        "summary": summary,
        "findings": [f.__dict__ for f in findings],
        "canonical": canon.to_dict(),
    }
    logger.info("pensions.export.trustee_bundle", extra={"scheme_id": scheme_id, "tenant_id": tenant_id, "findings": len(findings)})
    return payload
