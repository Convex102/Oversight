from __future__ import annotations

import json
from pathlib import Path
from typing import Optional, Dict, Any
from .schema import CanonicalSchema, ColumnRole, RoleGroup
from .exceptions import RegistryError

def _role_to_dict(r: ColumnRole) -> dict:
    return {
        "column": r.column,
        "group": r.group.value,
        "subtype": r.subtype,
        "confidence": r.confidence,
        "evidence": r.evidence,
    }

def _role_from_dict(d: dict) -> ColumnRole:
    return ColumnRole(
        column=d["column"],
        group=RoleGroup(d["group"]),
        subtype=d.get("subtype"),
        confidence=float(d.get("confidence", 0.0)),
        evidence=dict(d.get("evidence", {})),
    )

def _schema_to_dict(s: CanonicalSchema) -> dict:
    return {
        "fingerprint": s.fingerprint,
        "domain_hint": s.domain_hint,
        "time_column": _role_to_dict(s.time_column) if s.time_column else None,
        "ids": {k: _role_to_dict(v) for k, v in s.ids.items()},
        "states": {k: _role_to_dict(v) for k, v in s.states.items()},
        "flows": {k: _role_to_dict(v) for k, v in s.flows.items()},
        "drivers": {k: _role_to_dict(v) for k, v in s.drivers.items()},
        "controls": {k: _role_to_dict(v) for k, v in s.controls.items()},
        "outcomes": {k: _role_to_dict(v) for k, v in s.outcomes.items()},
        "texts": {k: _role_to_dict(v) for k, v in s.texts.items()},
        "unknowns": {k: _role_to_dict(v) for k, v in s.unknowns.items()},
        "metadata": s.metadata,
    }

def _schema_from_dict(d: dict) -> CanonicalSchema:
    s = CanonicalSchema(
        fingerprint=d["fingerprint"],
        domain_hint=d.get("domain_hint"),
        metadata=dict(d.get("metadata", {})),
    )
    if d.get("time_column"):
        s.time_column = _role_from_dict(d["time_column"])
    s.ids = {k: _role_from_dict(v) for k, v in d.get("ids", {}).items()}
    s.states = {k: _role_from_dict(v) for k, v in d.get("states", {}).items()}
    s.flows = {k: _role_from_dict(v) for k, v in d.get("flows", {}).items()}
    s.drivers = {k: _role_from_dict(v) for k, v in d.get("drivers", {}).items()}
    s.controls = {k: _role_from_dict(v) for k, v in d.get("controls", {}).items()}
    s.outcomes = {k: _role_from_dict(v) for k, v in d.get("outcomes", {}).items()}
    s.texts = {k: _role_from_dict(v) for k, v in d.get("texts", {}).items()}
    s.unknowns = {k: _role_from_dict(v) for k, v in d.get("unknowns", {}).items()}
    return s

class AdapterRegistry:
    """Persisted registry of inferred schemas, keyed by dataset fingerprint."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._store: Dict[str, dict] = self._load()

    def _load(self) -> Dict[str, dict]:
        if not self.path.exists():
            return {}
        try:
            return json.load(open(self.path, "r", encoding="utf-8"))
        except Exception as e:
            raise RegistryError(f"Failed to load registry: {e}")

    def save(self, schema: CanonicalSchema) -> None:
        try:
            self._store[schema.fingerprint] = _schema_to_dict(schema)
            json.dump(self._store, open(self.path, "w", encoding="utf-8"), indent=2)
        except Exception as e:
            raise RegistryError(f"Failed to save registry: {e}")

    def get(self, fingerprint: str) -> Optional[CanonicalSchema]:
        d = self._store.get(fingerprint)
        return _schema_from_dict(d) if d else None

    def size(self) -> int:
        return len(self._store)
