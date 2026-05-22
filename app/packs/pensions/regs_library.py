from __future__ import annotations

import os
import json
import logging
from dataclasses import dataclass, asdict
from typing import Dict, Any, List, Optional

logger = logging.getLogger("asgl.pensions.regs")


@dataclass
class RegClause:
    id: str
    title: str
    body: str
    tags: List[str]
    source: str
    url: str = ""
    jurisdiction: str = "UK"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _default_library_path() -> str:
    return os.environ.get("ASGL_PENSIONS_REGS_LIBRARY", "app/packs/pensions/resources/pensions_regulations_library.json")


class PensionsRegLibrary:
    """
    Phase 11: A deterministic 'schema library' for pensions regulations and guidance.
    This is NOT a generative LLM library; it's a structured, citeable knowledge base.

    It is used for:
      - mapping validation rules to regulatory context
      - evidence packs (citations)
      - reviewer UI help/tooltips
      - future ML retrieval-augmented mapping (RAG) without hallucinations
    """
    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or _default_library_path()
        self._cache: List[RegClause] | None = None

    def _load(self) -> List[RegClause]:
        if self._cache is not None:
            return self._cache
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            clauses = [RegClause(**c) for c in raw.get("clauses", [])]
            self._cache = clauses
            return clauses
        except FileNotFoundError:
            logger.warning("regs_library_missing", extra={"path": self.path})
            self._cache = []
            return []
        except Exception as e:
            logger.exception("regs_library_load_failed", extra={"path": self.path})
            self._cache = []
            return []

    def list(self, *, tag: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
        clauses = self._load()
        if tag:
            clauses = [c for c in clauses if tag.lower() in [t.lower() for t in (c.tags or [])]]
        return [c.to_dict() for c in clauses[:limit]]

    def search(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        q = (query or "").lower().strip()
        if not q:
            return []
        clauses = self._load()
        scored = []
        for c in clauses:
            txt = (c.title + " " + c.body + " " + " ".join(c.tags or [])).lower()
            score = 0
            for token in q.split():
                if token and token in txt:
                    score += 1
            if score > 0:
                scored.append((score, c))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [c.to_dict() for _, c in scored[:limit]]


REGS_LIBRARY = PensionsRegLibrary()
