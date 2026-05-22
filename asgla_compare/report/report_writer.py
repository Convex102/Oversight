from __future__ import annotations
import os
import csv
from typing import List
from dataclasses import asdict
from datetime import datetime

from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors

from asgla_compare.compare.diff_engine import DiffRow

def write_csv(rows: List[DiffRow], out_path: str) -> None:
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(asdict(rows[0]).keys()) if rows else [])
        if rows:
            w.writeheader()
            for r in rows:
                w.writerow(asdict(r))

def write_pdf(rows: List[DiffRow], out_path: str, title: str = "ASGL-A Reconciliation Report") -> None:
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(out_path, pagesize=A4, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40)
    story = []
    story.append(Paragraph(title, styles["Heading1"]))
    story.append(Paragraph(f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}", styles["Normal"]))
    story.append(Spacer(1, 12))

    # summary counts
    counts = {}
    for r in rows:
        counts[r.status] = counts.get(r.status, 0) + 1
    summary = [["Status", "Count"]] + [[k, str(v)] for k, v in sorted(counts.items(), key=lambda x: x[0])]
    t = Table(summary, colWidths=[260, 80])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.black),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("GRID", (0,0), (-1,-1), 0.5, colors.lightgrey),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.whitesmoke, colors.white]),
    ]))
    story.append(t)
    story.append(Spacer(1, 16))

    # exceptions first
    exceptions = [r for r in rows if r.status in ("MISMATCH", "REVIEW", "MISSING_IN_PDF", "MISSING_IN_EXCEL")]
    story.append(Paragraph("Exceptions (review first)", styles["Heading2"]))
    if not exceptions:
        story.append(Paragraph("No exceptions detected within configured tolerances.", styles["Normal"]))
    else:
        table_rows = [["Excel Key", "PDF Key", "Excel", "PDF", "Δ", "%Δ", "Status", "Score"]]
        for r in exceptions[:120]:
            table_rows.append([
                r.key_excel[:34],
                r.key_pdf[:34],
                "" if r.value_excel is None else f"{r.value_excel:,.2f}",
                "" if r.value_pdf is None else f"{r.value_pdf:,.2f}",
                "" if r.delta is None else f"{r.delta:,.2f}",
                "" if r.pct_delta is None else f"{r.pct_delta:,.2f}%",
                r.status,
                f"{r.match_score:.0f}"
            ])
        et = Table(table_rows, colWidths=[90, 90, 55, 55, 45, 45, 60, 35])
        et.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), colors.darkblue),
            ("TEXTCOLOR", (0,0), (-1,0), colors.white),
            ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
            ("GRID", (0,0), (-1,-1), 0.25, colors.lightgrey),
            ("VALIGN", (0,0), (-1,-1), "TOP"),
            ("FONTSIZE", (0,0), (-1,-1), 8),
        ]))
        story.append(et)

    doc.build(story)
