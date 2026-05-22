from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import json


@dataclass
class PackField:
    key: str
    type: str
    description: str = ""
    required: bool = False
    aliases: List[str] = None  # type: ignore

    def all_names(self) -> List[str]:
        out = [self.key]
        for a in (self.aliases or []):
            if a and a not in out:
                out.append(a)
        return out


@dataclass
class PackSchema:
    name: str
    version: str
    description: str = ""
    fields: List[PackField] = None  # type: ignore

    def field_by_key(self) -> Dict[str, PackField]:
        return {f.key: f for f in (self.fields or [])}

    def required_keys(self) -> List[str]:
        return [f.key for f in (self.fields or []) if bool(f.required)]


def load_pack_schema(schema_dict: Dict[str, Any]) -> PackSchema:
    fields = []
    for f in (schema_dict.get("fields") or []):
        fields.append(PackField(
            key=str(f.get("key") or "").strip(),
            type=str(f.get("type") or "string").strip(),
            description=str(f.get("description") or "").strip(),
            required=bool(f.get("required", False)),
            aliases=list(f.get("aliases") or []),
        ))
    return PackSchema(
        name=str(schema_dict.get("name") or "schema"),
        version=str(schema_dict.get("version") or "0.0.0"),
        description=str(schema_dict.get("description") or "").strip(),
        fields=fields,
    )


def load_pack_schema_file(path: str | Path) -> PackSchema:
    p = Path(path)
    d = json.loads(p.read_text(encoding="utf-8"))
    return load_pack_schema(d)


def is_row_based(schema: PackSchema) -> bool:
    """Heuristic: detect if schema represents a row-level table (e.g. trial balance)."""
    keys = set([f.key for f in (schema.fields or [])])
    hits = 0
    for k in ["account_code", "account_name", "debit", "credit", "net", "balance", "amount"]:
        if k in keys:
            hits += 1
    return hits >= 3
