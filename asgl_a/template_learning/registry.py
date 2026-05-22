from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
import json
import time

from .fingerprint import TemplateSignature
from .drift import drift_score


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _jaccard(a: List[str], b: List[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    return len(sa & sb) / max(1, len(sa | sb))


@dataclass
class TemplateProfile:
    id: str
    tenant_id: str
    doc_type: str
    fingerprint: str
    tokens: List[str]
    shape: Tuple[int, int]
    version: int = 1
    parent_id: Optional[str] = None

    # What the system learned / stored
    field_selections: Dict[str, Any] = None      # review intent (fields, compare axes)
    column_role_overrides: Dict[str, str] = None # optional overrides: col -> role
    coa_mapping_overrides: Dict[str, str] = None # optional overrides: account -> canonical coa id

    created_at: str = None
    updated_at: str = None
    last_seen_at: str = None

    def to_record(self) -> Dict[str, Any]:
        d = asdict(self)
        # ensure non-None dicts
        d["field_selections"] = d["field_selections"] or {}
        d["column_role_overrides"] = d["column_role_overrides"] or {}
        d["coa_mapping_overrides"] = d["coa_mapping_overrides"] or {}
        return d


class TemplateRegistry:
    """Append-only JSONL registry with in-memory index.

    Why JSONL:
    - human inspectable
    - easy to sync/backup
    - avoids DB dependencies
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._index: Dict[str, TemplateProfile] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                p = TemplateProfile(**rec)
                self._index[p.id] = p
        except Exception:
            # best-effort; corrupted line(s) shouldn't brick runtime
            return

    def _append(self, profile: TemplateProfile) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(profile.to_record(), ensure_ascii=False) + "\n")
        self._index[profile.id] = profile

    def list(self, tenant_id: str, doc_type: Optional[str] = None) -> List[TemplateProfile]:
        out = [p for p in self._index.values() if p.tenant_id == tenant_id and (doc_type is None or p.doc_type == doc_type)]
        out.sort(key=lambda p: (p.doc_type, p.updated_at or p.created_at or ""), reverse=True)
        return out

    def match(self, tenant_id: str, doc_type: str, sig: TemplateSignature) -> Dict[str, Any]:
        candidates = [p for p in self._index.values() if p.tenant_id == tenant_id and p.doc_type == doc_type]
        if not candidates:
            return {"match": None, "similarity": 0.0, "drift": 1.0}

        best = None
        best_sim = -1.0
        for p in candidates:
            sim = _jaccard(p.tokens, sig.tokens)
            if sim > best_sim:
                best_sim = sim
                best = p

        d = drift_score(best.tokens, sig.tokens, best.shape, sig.shape) if best else 1.0
        return {"match": best, "similarity": float(best_sim), "drift": float(d)}

    def upsert_from_signature(
        self,
        tenant_id: str,
        doc_type: str,
        sig: TemplateSignature,
        field_selections: Optional[Dict[str, Any]] = None,
        column_role_overrides: Optional[Dict[str, str]] = None,
        coa_mapping_overrides: Optional[Dict[str, str]] = None,
        force_new_version: bool = False,
        drift_threshold: float = 0.18,
    ) -> TemplateProfile:
        # See if exact fingerprint exists
        for p in self._index.values():
            if p.tenant_id == tenant_id and p.doc_type == doc_type and p.fingerprint == sig.fingerprint:
                p.last_seen_at = _now()
                p.updated_at = _now()
                self._append(p)
                return p

        # Find closest match to decide versioning
        m = self.match(tenant_id, doc_type, sig)
        best: Optional[TemplateProfile] = m["match"]
        d = float(m["drift"])
        create_new_version = force_new_version or (best is not None and d >= drift_threshold)

        if best is None:
            profile_id = f"tpl_{sig.fingerprint[:12]}"
            p = TemplateProfile(
                id=profile_id,
                tenant_id=tenant_id,
                doc_type=doc_type,
                fingerprint=sig.fingerprint,
                tokens=sig.tokens,
                shape=sig.shape,
                version=1,
                parent_id=None,
                field_selections=field_selections or {},
                column_role_overrides=column_role_overrides or {},
                coa_mapping_overrides=coa_mapping_overrides or {},
                created_at=_now(),
                updated_at=_now(),
                last_seen_at=_now(),
            )
            self._append(p)
            return p

        if create_new_version:
            profile_id = f"{best.id}_v{best.version + 1}"
            p = TemplateProfile(
                id=profile_id,
                tenant_id=tenant_id,
                doc_type=doc_type,
                fingerprint=sig.fingerprint,
                tokens=sig.tokens,
                shape=sig.shape,
                version=best.version + 1,
                parent_id=best.id,
                field_selections=field_selections or (best.field_selections or {}),
                column_role_overrides=column_role_overrides or (best.column_role_overrides or {}),
                coa_mapping_overrides=coa_mapping_overrides or (best.coa_mapping_overrides or {}),
                created_at=_now(),
                updated_at=_now(),
                last_seen_at=_now(),
            )
            self._append(p)
            return p

        # Otherwise: update best profile fingerprint/tokens (same version) cautiously
        best.fingerprint = sig.fingerprint
        best.tokens = sig.tokens
        best.shape = sig.shape
        best.field_selections = field_selections or (best.field_selections or {})
        best.column_role_overrides = column_role_overrides or (best.column_role_overrides or {})
        best.coa_mapping_overrides = coa_mapping_overrides or (best.coa_mapping_overrides or {})
        best.updated_at = _now()
        best.last_seen_at = _now()
        self._append(best)
        return best
