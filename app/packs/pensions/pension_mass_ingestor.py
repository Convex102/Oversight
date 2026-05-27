"""
OVERSIGHT-P — Mass Pension Record Ingestor
==========================================
Handles batch ingestion of pension member records from:
  - Scanned physical documents (image files: JPEG, PNG, TIFF)
  - PDF member summaries (single or multi-member per file)
  - Excel/CSV exports from legacy administration systems
  - ZIP archives of multiple documents

Entry point for Oversight-P mass document import workflow:
  Upload → Classify → OCR/Extract → Map → Normalise → Canonical

Integrates with:
  - multi_ocr_pdf_ingest.py (5-strategy PDF extraction)
  - pension_schema_mapper.py (label → canonical field mapping)
  - pension_schema_extended.py (PensionsMember canonical schema)
  - Celery task (pension_batch_task.py) for async processing

Design principles:
  - Every record gets a confidence score
  - Low-confidence fields go to human review queue
  - Audit trail preserved end-to-end (source file → canonical record)
  - GDPR: raw OCR text never stored; only canonical mapped values
  - Graceful: failed records logged and queued for retry, never silently dropped
"""

from __future__ import annotations

import io
import json
import logging
import math
import os
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class IngestedRecord:
    """A single member record after ingestion and mapping."""
    member_id: Optional[str]
    canonical: Dict[str, Any]           # Mapped canonical field values
    raw_extracted: Dict[str, Any]       # Original OCR output (not persisted in prod)
    source_file: str                    # Filename for audit trail
    page_number: Optional[int]
    extraction_strategy: str
    mapping_confidence: float           # 0.0–1.0
    mapping_coverage: float             # fields_mapped / fields_extracted
    unmapped_labels: List[str]
    warnings: List[str] = field(default_factory=list)
    error: Optional[str] = None         # Set if this record failed


@dataclass
class BatchIngestionResult:
    """Summary of a batch ingestion run."""
    scheme_id: str
    source_files: List[str]
    total_records: int
    successful_records: int
    failed_records: int
    low_confidence_records: int         # confidence < 0.7
    records: List[IngestedRecord]
    run_id: str
    duration_seconds: float
    strategy_distribution: Dict[str, int] = field(default_factory=dict)


class PensionMassIngestor:
    """
    Mass ingestion pipeline for pension member records.

    Handles single files and entire batch uploads. Produces
    List[IngestedRecord] which feeds directly into:
      - pension_anomaly_engine.detect_batch()
      - pack.build_canonical()
    """

    def __init__(
        self,
        confidence_floor: float = 0.6,
        low_confidence_threshold: float = 0.75,
    ):
        self.confidence_floor = confidence_floor
        self.low_confidence_threshold = low_confidence_threshold
        self._mapper = None
        self._pdf_extractor = None
        self._load_components()

    def _load_components(self) -> None:
        try:
            try:
                from app.packs.pensions.pension_schema_mapper import map_record, compute_confidence
            except ImportError:
                from pension_schema_mapper import map_record, compute_confidence
            self._map_record = map_record
            self._compute_confidence = compute_confidence
            logger.info("PensionSchemaMapper loaded")
        except ImportError as e:
            logger.warning("PensionSchemaMapper unavailable: %s", e)
            self._map_record = None
            self._compute_confidence = None

        try:
            sys_path_add = str(Path(__file__).parent.parent.parent.parent)
            import sys
            if sys_path_add not in sys.path:
                sys.path.insert(0, sys_path_add)
            from oversight_enhancements.multi_ocr_pdf_ingest import extract_pdf_kv
            self._extract_pdf = extract_pdf_kv
            logger.info("multi_ocr_pdf_ingest loaded (5-strategy chain)")
        except ImportError:
            # Fallback to repo's original pdf_ingest
            try:
                from app.api.v1.asgla_compare.compare.pdf_ingest import extract_pdf_kv as _orig
                self._extract_pdf = _orig
                logger.info("Fell back to original pdf_ingest")
            except ImportError:
                self._extract_pdf = None
                logger.warning("No PDF extractor available")

    # ── Main entry points ─────────────────────────────────────────────────────

    def ingest_file(
        self,
        file_path: str,
        scheme_id: str = "unknown",
        run_id: str = "batch-001",
    ) -> List[IngestedRecord]:
        """
        Ingest a single file (PDF, image, Excel, CSV, or ZIP).
        Returns a list because one PDF may contain multiple member records.
        """
        path = Path(file_path)
        suffix = path.suffix.lower()

        if suffix == ".zip":
            return self._ingest_zip(path, scheme_id, run_id)
        elif suffix in (".pdf",):
            return self._ingest_pdf(path, scheme_id, run_id)
        elif suffix in (".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp"):
            return self._ingest_image(path, scheme_id, run_id)
        elif suffix in (".xlsx", ".xls", ".xlsm"):
            return self._ingest_excel(path, scheme_id, run_id)
        elif suffix == ".csv":
            return self._ingest_csv(path, scheme_id, run_id)
        else:
            logger.warning("Unsupported file type: %s", suffix)
            return [IngestedRecord(
                member_id=None,
                canonical={},
                raw_extracted={},
                source_file=path.name,
                page_number=None,
                extraction_strategy="unsupported",
                mapping_confidence=0.0,
                mapping_coverage=0.0,
                unmapped_labels=[],
                error=f"Unsupported file type: {suffix}",
            )]

    def ingest_batch(
        self,
        file_paths: List[str],
        scheme_id: str = "unknown",
        run_id: str = "batch-001",
        progress_callback=None,
    ) -> BatchIngestionResult:
        """
        Ingest multiple files as a batch.
        progress_callback(completed: int, total: int) called after each file.
        """
        import time
        t0 = time.time()
        all_records: List[IngestedRecord] = []
        strategy_dist: Dict[str, int] = {}

        for i, fp in enumerate(file_paths):
            try:
                records = self.ingest_file(fp, scheme_id=scheme_id, run_id=run_id)
                all_records.extend(records)
                for r in records:
                    s = r.extraction_strategy
                    strategy_dist[s] = strategy_dist.get(s, 0) + 1
            except Exception as e:
                logger.exception("Batch ingest failed for %s: %s", fp, e)
                all_records.append(IngestedRecord(
                    member_id=None,
                    canonical={},
                    raw_extracted={},
                    source_file=Path(fp).name,
                    page_number=None,
                    extraction_strategy="error",
                    mapping_confidence=0.0,
                    mapping_coverage=0.0,
                    unmapped_labels=[],
                    error=str(e),
                ))
            if progress_callback:
                progress_callback(i + 1, len(file_paths))

        elapsed = time.time() - t0
        successful = [r for r in all_records if not r.error]
        failed = [r for r in all_records if r.error]
        low_conf = [r for r in successful if r.mapping_confidence < self.low_confidence_threshold]

        logger.info(
            "BatchIngestion complete: %d records, %d ok, %d failed, %d low-conf in %.2fs",
            len(all_records), len(successful), len(failed), len(low_conf), elapsed,
        )

        return BatchIngestionResult(
            scheme_id=scheme_id,
            source_files=file_paths,
            total_records=len(all_records),
            successful_records=len(successful),
            failed_records=len(failed),
            low_confidence_records=len(low_conf),
            records=all_records,
            run_id=run_id,
            duration_seconds=round(elapsed, 3),
            strategy_distribution=strategy_dist,
        )

    # ── Strategy: PDF ─────────────────────────────────────────────────────────

    def _ingest_pdf(
        self, path: Path, scheme_id: str, run_id: str
    ) -> List[IngestedRecord]:
        """Ingest a PDF using the 5-strategy OCR chain."""
        if not self._extract_pdf:
            return [self._error_record(path.name, "No PDF extractor available")]

        result = self._extract_pdf(str(path))
        if not result.items:
            return [self._error_record(path.name, "PDF extraction returned no data")]

        # Check if this looks like a multi-member PDF (table-style)
        if self._is_tabular_multi_member(result):
            return self._split_tabular_records(result, path.name, run_id)

        # Single member record
        return [self._build_record(
            raw=result.items,
            source_file=path.name,
            strategy=result.strategy_used,
            page_number=None,
            warnings=result.warnings,
        )]

    # ── Strategy: Image ───────────────────────────────────────────────────────

    def _ingest_image(
        self, path: Path, scheme_id: str, run_id: str
    ) -> List[IngestedRecord]:
        """
        Ingest a scanned image using Gemini Flash OCR then Tesseract fallback.
        The image is a scanned physical pension record.
        """
        # Try Gemini Flash first (best structured extraction from images)
        result = self._try_gemini_image(path)
        if result:
            return [self._build_record(
                raw=result,
                source_file=path.name,
                strategy="gemini_flash_ocr",
                page_number=None,
            )]

        # Fallback: convert image to single-page PDF then use PDF pipeline
        try:
            from PIL import Image as PILImage
            import tempfile
            img = PILImage.open(str(path))
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                img.save(tmp.name, "PDF")
                pdf_records = self._ingest_pdf(Path(tmp.name), scheme_id, run_id)
                os.unlink(tmp.name)
                for r in pdf_records:
                    r.source_file = path.name
                    r.extraction_strategy = "image_to_pdf_" + r.extraction_strategy
                return pdf_records
        except Exception as e:
            logger.warning("Image fallback failed for %s: %s", path.name, e)
            return [self._error_record(path.name, f"Image extraction failed: {e}")]

    def _try_gemini_image(self, path: Path) -> Optional[Dict[str, Any]]:
        """Call Gemini Flash with the image directly for structured extraction."""
        try:
            import base64
            import json as _json
            import urllib.request

            api_key = os.environ.get("GOOGLE_API_KEY", "")
            if not api_key:
                return None

            with open(str(path), "rb") as f:
                img_data = f.read()
            img_b64 = base64.b64encode(img_data).decode()

            suffix = path.suffix.lower()
            mime_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                        ".png": "image/png", ".tiff": "image/tiff", ".tif": "image/tiff"}
            mime_type = mime_map.get(suffix, "image/jpeg")

            prompt = (
                "This is a scanned pension member record. "
                "Extract ALL fields and values as a JSON object. "
                "Use the exact label text as keys and the values as values. "
                "Include all sections: member identity, scheme details, financial, investment, beneficiary. "
                "Return ONLY the JSON object, no other text."
            )

            payload = _json.dumps({
                "contents": [{"parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": mime_type, "data": img_b64}},
                ]}],
                "generationConfig": {"temperature": 0.0, "maxOutputTokens": 2048},
            }).encode()

            url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
                   f"gemini-2.0-flash:generateContent?key={api_key}")
            req = urllib.request.Request(
                url, data=payload,
                headers={"Content-Type": "application/json"}, method="POST"
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = _json.loads(resp.read().decode())

            text = (body.get("candidates", [{}])[0]
                    .get("content", {}).get("parts", [{}])[0].get("text", ""))
            clean = text.strip()
            if clean.startswith("```"):
                lines = clean.split("\n")
                clean = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

            return _json.loads(clean)
        except Exception as e:
            logger.debug("Gemini image extraction failed: %s", e)
            return None

    # ── Strategy: ZIP archive ─────────────────────────────────────────────────

    def _ingest_zip(
        self, path: Path, scheme_id: str, run_id: str
    ) -> List[IngestedRecord]:
        """Extract ZIP archive and ingest each member file."""
        records = []
        try:
            with zipfile.ZipFile(str(path), "r") as zf:
                for member_name in zf.namelist():
                    if member_name.startswith("__MACOSX"):
                        continue
                    if not any(member_name.lower().endswith(ext)
                               for ext in (".pdf", ".jpg", ".jpeg", ".png", ".xlsx", ".csv")):
                        continue
                    try:
                        data = zf.read(member_name)
                        import tempfile
                        suffix = Path(member_name).suffix
                        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                            tmp.write(data)
                            tmp_path = tmp.name
                        file_records = self.ingest_file(tmp_path, scheme_id, run_id)
                        os.unlink(tmp_path)
                        for r in file_records:
                            r.source_file = member_name
                        records.extend(file_records)
                    except Exception as e:
                        logger.warning("ZIP member %s failed: %s", member_name, e)
                        records.append(self._error_record(member_name, str(e)))
        except Exception as e:
            records.append(self._error_record(path.name, f"ZIP extraction failed: {e}"))
        return records

    # ── Strategy: Excel ───────────────────────────────────────────────────────

    def _ingest_excel(
        self, path: Path, scheme_id: str, run_id: str
    ) -> List[IngestedRecord]:
        """
        Ingest Excel member extract.
        Each row = one member record. Headers = field labels.
        """
        try:
            import openpyxl
            wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
            ws = wb.active
            rows = list(ws.iter_rows(values_only=True))
            wb.close()

            if not rows or len(rows) < 2:
                return [self._error_record(path.name, "Excel file has no data rows")]

            headers = [str(h or "").strip() for h in rows[0]]
            records = []

            for row_idx, row in enumerate(rows[1:], start=2):
                raw = {}
                for h, v in zip(headers, row):
                    if h and v is not None and str(v).strip():
                        raw[h] = v
                if not raw:
                    continue
                records.append(self._build_record(
                    raw=raw,
                    source_file=path.name,
                    strategy="excel_row",
                    page_number=row_idx,
                ))

            logger.info("Excel ingest: %d member records from %s", len(records), path.name)
            return records
        except Exception as e:
            return [self._error_record(path.name, f"Excel extraction failed: {e}")]

    # ── Strategy: CSV ─────────────────────────────────────────────────────────

    def _ingest_csv(
        self, path: Path, scheme_id: str, run_id: str
    ) -> List[IngestedRecord]:
        """Ingest CSV member extract. Each row = one member."""
        try:
            import csv
            records = []
            with open(str(path), "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row_idx, row in enumerate(reader, start=2):
                    raw = {k: v for k, v in row.items() if k and v and str(v).strip()}
                    if raw:
                        records.append(self._build_record(
                            raw=raw,
                            source_file=path.name,
                            strategy="csv_row",
                            page_number=row_idx,
                        ))
            return records
        except Exception as e:
            return [self._error_record(path.name, f"CSV extraction failed: {e}")]

    # ── Record builder ────────────────────────────────────────────────────────

    def _build_record(
        self,
        raw: Dict[str, Any],
        source_file: str,
        strategy: str,
        page_number: Optional[int],
        warnings: Optional[List[str]] = None,
    ) -> IngestedRecord:
        """Map raw extracted dict to canonical IngestedRecord."""
        warnings = warnings or []

        if self._map_record:
            canonical, mapping_results, unmapped = self._map_record(raw)
            if self._compute_confidence:
                conf = self._compute_confidence(mapping_results)
            else:
                conf = len(canonical) / len(raw) if raw else 0.0
        else:
            canonical = raw
            unmapped = []
            conf = 0.5

        coverage = len(canonical) / len(raw) if raw else 0.0
        member_id = (canonical.get("member_id") or
                     str(raw.get("member_id") or raw.get("member reference") or ""))

        if conf < self.confidence_floor:
            warnings.append(
                f"Low mapping confidence ({conf:.0%}) — manual review recommended."
            )

        return IngestedRecord(
            member_id=member_id or None,
            canonical=canonical,
            raw_extracted=raw,
            source_file=source_file,
            page_number=page_number,
            extraction_strategy=strategy,
            mapping_confidence=round(conf, 3),
            mapping_coverage=round(coverage, 3),
            unmapped_labels=unmapped,
            warnings=warnings,
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _is_tabular_multi_member(self, result) -> bool:
        """Detect if a PDF contains a table of multiple member records."""
        return bool(result.tables and len(result.tables) > 0 and
                    any(len(t.get("rows", [])) > 1 for t in result.tables))

    def _split_tabular_records(self, result, source_file: str, run_id: str) -> List[IngestedRecord]:
        """Split a tabular PDF into individual member records."""
        records = []
        for tbl in result.tables:
            for row in tbl.get("rows", []):
                records.append(self._build_record(
                    raw=row,
                    source_file=source_file,
                    strategy=result.strategy_used + "_table",
                    page_number=tbl.get("page"),
                ))
        return records if records else [self._build_record(
            raw=result.items,
            source_file=source_file,
            strategy=result.strategy_used,
            page_number=None,
        )]

    def _error_record(self, source_file: str, error: str) -> IngestedRecord:
        return IngestedRecord(
            member_id=None,
            canonical={},
            raw_extracted={},
            source_file=source_file,
            page_number=None,
            extraction_strategy="error",
            mapping_confidence=0.0,
            mapping_coverage=0.0,
            unmapped_labels=[],
            error=error,
        )
