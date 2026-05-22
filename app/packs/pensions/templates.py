from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class PTemplateFingerprint:
    tenant_id: str
    pack_id: str
    template_name: str
    sheet_hint: Optional[str]
    header_text_signature: List[str]
    column_count: int
    version_tag: str = "v1"

    def key(self) -> str:
        h = hashlib.sha1()
        h.update(self.tenant_id.encode("utf-8"))
        h.update(self.pack_id.encode("utf-8"))
        h.update(str(self.column_count).encode("utf-8"))
        for t in self.header_text_signature[:64]:
            h.update(t.lower().strip().encode("utf-8"))
            h.update(b"|")
        if self.sheet_hint:
            h.update(self.sheet_hint.lower().encode("utf-8"))
        return h.hexdigest()


def build_fingerprint(*, tenant_id: str, pack_id: str, template_name: str, sheet_name: Optional[str], headers: List[str], column_count: int) -> PTemplateFingerprint:
    sig = [h for h in headers if h][:64]
    return PTemplateFingerprint(
        tenant_id=tenant_id,
        pack_id=pack_id,
        template_name=template_name,
        sheet_hint=sheet_name,
        header_text_signature=sig,
        column_count=column_count,
    )
