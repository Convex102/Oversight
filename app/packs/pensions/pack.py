from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Any, List

from .validators import validate_canonical
from .templates import build_fingerprint
from .schema import PensionsCanonical, PensionsMember, PensionsService, PensionsCalcResult


@dataclass
class PackSpec:
    pack_id: str
    name: str
    version: str
    canonical_schema: str
    template_types: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__.copy()


PACK_ID = "ASGL-P"


def _build_canonical(scheme_id: str, rows: List[Dict[str, Any]], meta: Dict[str, Any]) -> PensionsCanonical:
    canon = PensionsCanonical(scheme_id=scheme_id, meta=dict(meta))
    for r in rows:
        mid = str(r.get("member_id") or r.get("MemberID") or r.get("member") or "").strip()
        if not mid:
            continue
        canon.members.append(PensionsMember(
            member_id=mid,
            ni_number=r.get("ni_number") or r.get("NINumber") or r.get("ni"),
            surname=r.get("surname") or r.get("Surname"),
            forename=r.get("forename") or r.get("Forename"),
            date_of_birth=r.get("date_of_birth") or r.get("DOB"),
            status=r.get("status") or r.get("Status"),
        ))
        if any(k in r for k in ("service_start", "service_end", "pensionable_salary")):
            try:
                sal = r.get("pensionable_salary")
                sal_f = float(sal) if sal not in (None, "") else None
            except Exception:
                sal_f = None
            canon.services.append(PensionsService(member_id=mid, service_start=r.get("service_start"), service_end=r.get("service_end"), pensionable_salary=sal_f))
        if any(k in r for k in ("pre_value", "post_value", "calc_type")):
            try:
                pre = float(r.get("pre_value")) if r.get("pre_value") not in (None, "") else None
                post = float(r.get("post_value")) if r.get("post_value") not in (None, "") else None
            except Exception:
                pre, post = None, None
            canon.results.append(PensionsCalcResult(member_id=mid, calc_type=str(r.get("calc_type") or "UNKNOWN"), pre_value=pre, post_value=post, delta_value=None, assumptions={}))
    return canon


pack = {
    "id": PACK_ID,
    "name": "Pensions Vertical Pack",
    "version": "0.1.0",
    "spec": PackSpec(
        pack_id=PACK_ID,
        name="Pensions Vertical Pack",
        version="0.1.0",
        canonical_schema="PensionsCanonical",
        template_types=["SCHEME_MEMBER_EXTRACT", "SERVICE_HISTORY", "GMP_EQUALISATION", "REMEDIATION_EXCEPTION_LIST", "MIGRATION_VALIDATION"],
    ),
    "build_canonical": lambda scheme_id, rows, meta=None: _build_canonical(scheme_id, rows, meta or {}),
    "validate": lambda canon: validate_canonical(canon),
    "fingerprint": lambda tenant_id, template_name, sheet_name, headers, column_count: build_fingerprint(
        tenant_id=tenant_id,
        pack_id=PACK_ID,
        template_name=template_name,
        sheet_name=sheet_name,
        headers=headers,
        column_count=column_count,
    ),
}
