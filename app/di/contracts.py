from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional, Literal
import time, uuid

SourceType = Literal["excel_cell","pdf_text","pdf_table","ocr_text","user_mapping","derived"]

class Evidence(BaseModel):
    source_type: SourceType
    source_id: str = ""              # e.g. sheet!A1, pdf page block id, etc.
    location: Dict[str,Any] = Field(default_factory=dict)  # {sheet, addr} or {page, bbox}
    snippet: str = ""
    extractor: str = ""              # which extractor produced this
    confidence: float = 0.75
    ts: int = Field(default_factory=lambda: int(time.time()))

class Fact(BaseModel):
    key: str                         # schema field name (ASGL-C)
    value: Any
    dtype: str = "string"            # number|string|date|bool|json
    confidence: float = 0.75
    evidence: List[Evidence] = Field(default_factory=list)
    meta: Dict[str,Any] = Field(default_factory=dict)

class FactBundle(BaseModel):
    bundle_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: str = ""
    job_id: str = ""
    schema_domain: str = ""
    schema_id: str = ""
    schema_version: str = ""
    facts: Dict[str,Fact] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
    drift: Dict[str,Any] = Field(default_factory=dict)
    tables: Dict[str,Any] = Field(default_factory=dict)   # extracted tables + semantics
    raw: Dict[str,Any] = Field(default_factory=dict)      # raw extractor outputs