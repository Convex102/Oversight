from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional


def _utc() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _sha256(obj: Any) -> str:
    raw = json.dumps(obj, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@dataclass
class ModelArtifact:
    """
    Registry record for any model used by the platform:
      - DI models (e.g., vector encoders, rankers)
      - template mappers
      - vertical classifiers
      - LLM prompts (prompt-as-artifact)
    """
    model_id: str
    version: str
    type: str  # "encoder", "ranker", "classifier", "prompt", "ruleset"
    created_at: str
    created_by: str
    tags: List[str]
    uri: str  # where the artifact is stored (s3/path)
    sha256: str
    meta: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ModelRegistry:
    """
    Lightweight model registry. Production deployments should back this with
    persistent storage, but the interface is stable.
    """
    def __init__(self, logger=None):
        self.logger = logger
        self._models: Dict[str, ModelArtifact] = {}

    def register(
        self,
        *,
        model_id: str,
        version: str,
        type: str,
        created_by: str = "system",
        tags: Optional[List[str]] = None,
        uri: str = "",
        raw_meta: Optional[Dict[str, Any]] = None,
    ) -> ModelArtifact:
        created_at = _utc()
        meta = raw_meta or {}
        sha = _sha256({"model_id": model_id, "version": version, "type": type, "created_at": created_at, "meta": meta})
        art = ModelArtifact(
            model_id=model_id,
            version=version,
            type=type,
            created_at=created_at,
            created_by=created_by,
            tags=tags or [],
            uri=uri,
            sha256=sha,
            meta=meta,
        )
        self._models[f"{model_id}:{version}"] = art
        if self.logger:
            try:
                self.logger.info("governance.model_registered", extra={"model_id": model_id, "version": version, "type": type})
            except Exception:
                pass
        return art

    def get(self, model_id: str, version: str) -> Optional[ModelArtifact]:
        return self._models.get(f"{model_id}:{version}")

    def latest(self, model_id: str) -> Optional[ModelArtifact]:
        candidates = [m for k, m in self._models.items() if k.startswith(f"{model_id}:")]
        if not candidates:
            return None
        return sorted(candidates, key=lambda a: a.version)[-1]

    def list(self, prefix: str = "") -> List[Dict[str, Any]]:
        items = []
        for k, m in sorted(self._models.items()):
            if prefix and not k.startswith(prefix):
                continue
            items.append(m.to_dict())
        return items
