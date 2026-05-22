from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

from .schema import PensionsCanonical


@dataclass
class ValidationFinding:
    code: str
    severity: str
    message: str
    member_id: Optional[str] = None


NI_RE = re.compile(r"^[A-CEGHJ-PR-TW-Z]{2}\d{6}[A-D]$")


def validate_canonical(canon: PensionsCanonical) -> List[ValidationFinding]:
    findings: List[ValidationFinding] = []
    for m in canon.members:
        if not m.member_id:
            findings.append(ValidationFinding("P_MEM_ID_MISSING", "ERROR", "Missing member_id"))
        if m.ni_number and not NI_RE.match(m.ni_number.replace(" ", "").upper()):
            findings.append(ValidationFinding("P_NI_INVALID", "WARN", f"NI number format invalid: {m.ni_number}", member_id=m.member_id))
    for r in canon.results:
        if r.pre_value is not None and r.post_value is not None:
            dv = (r.post_value - r.pre_value)
            if r.delta_value is None:
                r.delta_value = dv
            if abs(dv) > 5000:
                findings.append(ValidationFinding("P_DELTA_LARGE", "INFO", f"Large delta detected for {r.calc_type}: {dv:.2f}", member_id=r.member_id))
    return findings
