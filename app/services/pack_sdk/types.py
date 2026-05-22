from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

@dataclass
class ValidationIssue:
    code: str
    severity: str  # info|warning|error
    message: str
    field: Optional[str] = None
    evidence: Optional[Dict[str, Any]] = None

@dataclass
class PackContext:
    tenant_id: str
    vertical: str
    pack_id: str
    meta: Dict[str, Any] = field(default_factory=dict)
    options: Dict[str, Any] = field(default_factory=dict)

ValidatorFn = Callable[[Dict[str, Any], PackContext], List[ValidationIssue]]

@dataclass
class PackDefinition:
    pack_id: str
    vertical: str
    version: str
    title: str
    description: str
    schema_fields: List[str]
    validators: List[ValidatorFn] = field(default_factory=list)
    reference_adapters: Dict[str, Callable[[PackContext], Any]] = field(default_factory=dict)
