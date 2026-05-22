from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

@dataclass
class StatementPack:
    pnl: Dict[str, Any] = field(default_factory=dict)
    balance_sheet: Dict[str, Any] = field(default_factory=dict)
    cashflow: Dict[str, Any] = field(default_factory=dict)
    line_items: List[Dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.0
    warnings: List[str] = field(default_factory=list)

@dataclass
class AnalysisPack:
    statements: Dict[str, Any]
    kpis: Dict[str, Any]
    flags: List[Dict[str, Any]]
    ml: Dict[str, Any]
    narrative: Optional[str]
    facts: Dict[str, Any]
    debug: Dict[str, Any]
