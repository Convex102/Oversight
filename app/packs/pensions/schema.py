from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List


@dataclass
class PensionsMember:
    member_id: str
    ni_number: Optional[str] = None
    surname: Optional[str] = None
    forename: Optional[str] = None
    date_of_birth: Optional[str] = None
    status: Optional[str] = None


@dataclass
class PensionsService:
    member_id: str
    service_start: Optional[str] = None
    service_end: Optional[str] = None
    pensionable_salary: Optional[float] = None


@dataclass
class PensionsCalcResult:
    member_id: str
    calc_type: str
    pre_value: Optional[float] = None
    post_value: Optional[float] = None
    delta_value: Optional[float] = None
    assumptions: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PensionsCanonical:
    scheme_id: str
    members: List[PensionsMember] = field(default_factory=list)
    services: List[PensionsService] = field(default_factory=list)
    results: List[PensionsCalcResult] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scheme_id": self.scheme_id,
            "members": [m.__dict__ for m in self.members],
            "services": [s.__dict__ for s in self.services],
            "results": [r.__dict__ for r in self.results],
            "meta": dict(self.meta),
        }
