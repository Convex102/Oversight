from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional, Any, List, Tuple

class RoleGroup(str, Enum):
    time = "time"
    state = "state"     # stocks / levels / balances
    flow = "flow"       # inflows/outflows (P&L like series)
    driver = "driver"   # exogenous drivers / covariates
    control = "control" # operator-controlled levers (optional)
    outcome = "outcome" # target vars (optional)
    id = "id"           # entity identifier columns
    text = "text"       # free-text columns
    unknown = "unknown"

@dataclass
class ColumnRole:
    column: str
    group: RoleGroup
    subtype: Optional[str] = None
    confidence: float = 0.0
    evidence: Dict[str, Any] = field(default_factory=dict)

@dataclass
class CanonicalSchema:
    """Canonical schema describing a dataset in role terms."""
    fingerprint: str
    domain_hint: Optional[str] = None
    time_column: Optional[ColumnRole] = None
    ids: Dict[str, ColumnRole] = field(default_factory=dict)
    states: Dict[str, ColumnRole] = field(default_factory=dict)
    flows: Dict[str, ColumnRole] = field(default_factory=dict)
    drivers: Dict[str, ColumnRole] = field(default_factory=dict)
    controls: Dict[str, ColumnRole] = field(default_factory=dict)
    outcomes: Dict[str, ColumnRole] = field(default_factory=dict)
    texts: Dict[str, ColumnRole] = field(default_factory=dict)
    unknowns: Dict[str, ColumnRole] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def all_roles(self) -> List[ColumnRole]:
        out: List[ColumnRole] = []
        if self.time_column:
            out.append(self.time_column)
        for d in [self.ids, self.states, self.flows, self.drivers, self.controls, self.outcomes, self.texts, self.unknowns]:
            out.extend(list(d.values()))
        return out

@dataclass
class InferenceResult:
    schema: CanonicalSchema
    warnings: List[str] = field(default_factory=list)
    debug: Dict[str, Any] = field(default_factory=dict)
