from __future__ import annotations
from typing import Dict, Any, Optional, List
import os, math
from app.di.contracts import FactBundle, Fact, Evidence
from app.di.dag import DAGPipeline, Node, NodeResult
from app.di.ocr_engines import ocr_fallback
from app.di.table_semantics import enrich_table
from app.di.drift import fingerprint_mapping, detect_template_drift
from app.di.adapters import discover_ingestion

ENABLE_OCR = os.getenv("ASGL_ENABLE_OCR","0") == "1"

def conf_from_sources(count:int)->float:
    # simple heuristic: more supporting evidence increases confidence
    return min(0.95, 0.60 + 0.10*max(0, count-1))

def node_extract_spreadsheet(ctx: Dict[str,Any])->NodeResult:
    adapters = ctx["adapters"]
    fn = adapters.get("extract_spreadsheet")
    if fn:
        out = fn(ctx.get("spreadsheet_path"), mapping=ctx.get("mapping", {}))
        # expect dict of field->value or richer
        return NodeResult("extract_spreadsheet", True, {"sheet_extracted": out})
    return NodeResult("extract_spreadsheet", True, {"sheet_extracted": ctx.get("sheet_extracted", {}) or {}})

def node_extract_pdf(ctx: Dict[str,Any])->NodeResult:
    adapters = ctx["adapters"]
    fn = adapters.get("extract_pdf")
    if fn and ctx.get("pdf_path"):
        out = fn(ctx["pdf_path"])
        return NodeResult("extract_pdf", True, {"pdf_extracted": out})
    return NodeResult("extract_pdf", True, {"pdf_extracted": ctx.get("pdf_extracted", {}) or {}})

def node_ocr_pdf(ctx: Dict[str,Any])->NodeResult:
    if not ENABLE_OCR or not ctx.get("pdf_path"):
        return NodeResult("ocr_pdf", True, {"ocr": {"skipped": True}})
    o = ocr_fallback(ctx["pdf_path"])
    return NodeResult("ocr_pdf", True, {"ocr": o}, metrics={"ocr_pages": o.get("pages",0), "ocr_engine": o.get("engine")})

def node_extract_tables(ctx: Dict[str,Any])->NodeResult:
    adapters = ctx["adapters"]
    fn = adapters.get("extract_tables")
    tables = {}
    if fn and ctx.get("pdf_path"):
        try:
            tables = fn(ctx["pdf_path"]) or {}
        except Exception:
            tables = {}
    # enrich semantics
    if isinstance(tables, dict):
        for k,t in list(tables.items()):
            if isinstance(t, dict):
                tables[k] = enrich_table(t)
    return NodeResult("extract_tables", True, {"tables": tables})

def node_drift(ctx: Dict[str,Any])->NodeResult:
    fp = fingerprint_mapping(ctx.get("mapping", {}) or {})
    drift = detect_template_drift(ctx.get("previous_mapping_fp",""), fp)
    return NodeResult("drift", True, {"mapping_fp": fp, "drift": drift})

def node_factbundle(ctx: Dict[str,Any])->NodeResult:
    fb = FactBundle(
        tenant_id=ctx.get("tenant_id",""),
        job_id=ctx.get("job_id",""),
        schema_domain=ctx.get("schema_domain",""),
        schema_id=ctx.get("schema_id",""),
        schema_version=ctx.get("schema_version",""),
        drift=ctx.get("drift", {}),
        tables=ctx.get("tables", {}),
        raw={"sheet": ctx.get("sheet_extracted", {}), "pdf": ctx.get("pdf_extracted", {}), "ocr": ctx.get("ocr", {})},
    )

    # Build facts from sheet_extracted (support {field: value} OR {field:{value, sheet, addr}})
    sheet = ctx.get("sheet_extracted", {}) or {}
    for k,v in sheet.items():
        if isinstance(v, dict) and "value" in v:
            ev = Evidence(source_type="excel_cell", source_id=f"{v.get('sheet','')}{v.get('addr','')}".strip(),
                          location={"sheet": v.get("sheet"), "addr": v.get("addr")},
                          snippet=str(v.get("value",""))[:120], extractor="spreadsheet", confidence=float(v.get("confidence",0.8)))
            val = v.get("value")
            c = float(v.get("confidence", ev.confidence))
        else:
            ev = Evidence(source_type="derived", source_id="spreadsheet", snippet=str(v)[:120], extractor="spreadsheet", confidence=0.75)
            val = v; c = 0.75
        fb.facts[k] = Fact(key=k, value=val, dtype="string", confidence=c, evidence=[ev])

    # Merge pdf_extracted if it includes recognized fields
    pdf = ctx.get("pdf_extracted", {}) or {}
    if isinstance(pdf, dict):
        for k,v in pdf.items():
            if k in fb.facts:
                f = fb.facts[k]
                ev = Evidence(source_type="pdf_text", source_id="pdf", snippet=str(v)[:120], extractor="pdf", confidence=0.70)
                f.evidence.append(ev)
                f.confidence = conf_from_sources(len(f.evidence))
            else:
                ev = Evidence(source_type="pdf_text", source_id="pdf", snippet=str(v)[:120], extractor="pdf", confidence=0.70)
                fb.facts[k] = Fact(key=k, value=v, dtype="string", confidence=0.70, evidence=[ev])

    # Low confidence queue
    low = [k for k,f in fb.facts.items() if f.confidence < 0.70]
    if low:
        fb.warnings.append(f"Low confidence fields: {', '.join(low[:20])}" + ("..." if len(low)>20 else ""))

    return NodeResult("factbundle", True, {"factbundle": fb.model_dump()} )

def run_di_v3(ctx: Dict[str,Any], progress_cb=None)->Dict[str,Any]:
    ctx = dict(ctx)
    ctx["adapters"] = discover_ingestion()
    nodes = [
        Node("extract_spreadsheet", node_extract_spreadsheet, [], max_retries=1),
        Node("extract_pdf", node_extract_pdf, [], max_retries=0),
        Node("ocr_pdf", node_ocr_pdf, ["extract_pdf"], max_retries=0),
        Node("extract_tables", node_extract_tables, ["extract_pdf","ocr_pdf"], max_retries=0),
        Node("drift", node_drift, ["extract_spreadsheet"], max_retries=0),
        Node("factbundle", node_factbundle, ["extract_spreadsheet","extract_pdf","extract_tables","drift"], max_retries=0),
    ]
    dag = DAGPipeline(nodes)
    return dag.run(ctx, progress_cb=progress_cb)