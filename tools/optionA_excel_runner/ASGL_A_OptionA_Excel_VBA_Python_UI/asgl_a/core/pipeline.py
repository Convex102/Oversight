from __future__ import annotations

import os
import time
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Dict, Any

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

STAGES = [
    "Loading financial pack(s)",
    "Extracting tables (2 detected)",
    "Canonicalizing to schema",
    "Validating output",
    "Running Review Intelligence",
    "Comparing Documents",
    "Generating PDF report",
    "Rendering SHAP analysis",
]

@dataclass
class PipelineConfig:
    input_file: str
    compare: bool = True
    output_dir: str = "outputs"
    logs_dir: str = "logs"
    schema_name: str = "Accountancy_COA_v1"

@dataclass
class PipelineResult:
    pdf_path: str
    shap_png_path: str
    decision_status: str
    severity: str
    recommended_actions: List[str]
    metrics: Dict[str, Any]

def init_logger(logs_dir: str) -> logging.Logger:
    Path(logs_dir).mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("asgl_a")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    ts = time.strftime("%Y%m%d_%H%M%S")
    fh = logging.FileHandler(Path(logs_dir) / f"asgl_a_{ts}.log", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)

    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    fh.setFormatter(fmt)
    ch.setFormatter(fmt)

    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger

def _simulate_work(logger: logging.Logger, seconds: float = 0.8):
    logger.debug("Simulating work for %.2fs", seconds)
    time.sleep(seconds)

def _make_placeholder_dataframe(input_file: str) -> pd.DataFrame:
    # Placeholder: in real life you'd parse the workbook. Here we create a stable demo dataset.
    rng = np.random.default_rng(7)
    dates = pd.date_range("2020-01-01", periods=60, freq="M")
    df = pd.DataFrame({
        "Date": dates,
        "Revenue": rng.normal(120000, 15000, size=len(dates)).clip(50000),
        "COGS": rng.normal(65000, 8000, size=len(dates)).clip(20000),
        "Overheads": rng.normal(25000, 4000, size=len(dates)).clip(8000),
    })
    df["GrossProfit"] = df["Revenue"] - df["COGS"]
    df["NetProfit"] = df["GrossProfit"] - df["Overheads"]
    return df

def generate_shap_like_plot(df: pd.DataFrame, out_path: str, logger: logging.Logger) -> str:
    # Create a SHAP-style horizontal dot plot (placeholder)
    features = ["Revenue", "COGS", "Overheads", "GrossProfit", "NetProfit"]
    impacts = np.array([0.62, -0.41, -0.23, 0.35, 0.18])
    x = impacts
    y = np.arange(len(features))
    sizes = (np.abs(impacts) * 600) + 80

    plt.figure(figsize=(8.6, 3.2))
    plt.scatter(x, y, s=sizes, alpha=0.85)
    plt.yticks(y, features)
    plt.axvline(0, linewidth=1)
    plt.title("SHAP Analysis (Demo)")
    plt.xlabel("Impact on Decision Score")
    plt.tight_layout()
    plt.savefig(out_path, dpi=200)
    plt.close()
    logger.info("SHAP plot saved: %s", out_path)
    return out_path

def generate_pdf_report(df: pd.DataFrame, shap_png_path: str, out_path: str, logger: logging.Logger, decision: dict) -> str:
    c = canvas.Canvas(out_path, pagesize=A4)
    width, height = A4

    # Header bar
    c.setFillColorRGB(0.09, 0.22, 0.43)  # ASGL blue
    c.rect(0, height-70, width, 70, fill=1, stroke=0)
    c.setFillColorRGB(1,1,1)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(40, height-45, "ASGL-A | Client Working Papers vs Accounts Report")
    c.setFont("Helvetica", 10)
    c.drawString(40, height-62, "Automated Schema & Governance Layer • Decision Intelligence • Explainability")

    # Decision card
    c.setFillColorRGB(0.95, 0.95, 0.96)
    c.roundRect(40, height-185, width-80, 95, 10, fill=1, stroke=0)
    c.setFillColorRGB(0.15, 0.15, 0.15)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(60, height-120, "Decision Intelligence")
    c.setFont("Helvetica", 11)
    c.drawString(60, height-140, f"Status: {decision['status']}")
    c.drawString(60, height-158, f"Severity: {decision['severity']}")

    c.setFont("Helvetica-Bold", 12)
    c.drawString(320, height-120, "Recommended Actions")
    c.setFont("Helvetica", 10)
    y = height-140
    for a in decision["actions"][:3]:
        c.drawString(320, y, f"• {a}")
        y -= 14

    # Mini table summary
    c.setFont("Helvetica-Bold", 12)
    c.drawString(40, height-225, "Key Financial Summary (Demo)")
    c.setFont("Helvetica", 9)
    cols = ["Date", "Revenue", "COGS", "GrossProfit", "NetProfit"]
    x0, y0 = 40, height-245
    colw = [80, 90, 70, 90, 90]
    for i, col in enumerate(cols):
        c.setFont("Helvetica-Bold", 9)
        c.drawString(x0 + sum(colw[:i]) + 4, y0, col)
    c.setFont("Helvetica", 9)
    y = y0 - 14
    tail = df.tail(8)
    for _, row in tail.iterrows():
        vals = [
            row["Date"].strftime("%Y-%m"),
            f"{row['Revenue']:,.0f}",
            f"{row['COGS']:,.0f}",
            f"{row['GrossProfit']:,.0f}",
            f"{row['NetProfit']:,.0f}",
        ]
        for i, v in enumerate(vals):
            c.drawString(x0 + sum(colw[:i]) + 4, y, str(v))
        y -= 12

    # SHAP image
    c.setFont("Helvetica-Bold", 12)
    c.drawString(40, 235, "Explainability")
    c.setFont("Helvetica-Bold", 14)
    c.drawString(40, 215, "SHAP Analysis")
    try:
        c.drawImage(shap_png_path, 40, 60, width=520, height=145, preserveAspectRatio=True, mask='auto')
    except Exception as e:
        logger.exception("Failed to embed SHAP image: %s", e)
        c.setFont("Helvetica", 10)
        c.drawString(40, 120, f"(Could not embed SHAP image: {e})")

    c.showPage()
    c.save()
    logger.info("PDF report saved: %s", out_path)
    return out_path

def run_pipeline(cfg: PipelineConfig, on_stage: Optional[Callable[[int, str], None]] = None) -> PipelineResult:
    logger = init_logger(cfg.logs_dir)
    out_dir = Path(cfg.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    logger.info("ASGL-A pipeline started")
    logger.info("Input file: %s", cfg.input_file)
    logger.info("Compare enabled: %s", cfg.compare)
    logger.info("Schema: %s", cfg.schema_name)

    # Stage 1: Load
    for idx, stage in enumerate(STAGES):
        if on_stage:
            on_stage(idx, stage)
        logger.info("Stage %d/%d: %s", idx+1, len(STAGES), stage)
        _simulate_work(logger, 0.7 if idx < 6 else 0.6)

    # Create demo dataset
    df = _make_placeholder_dataframe(cfg.input_file)

    # Decision placeholder (these are what your UI shows)
    decision = {
        "status": "Hold Sign Off",
        "severity": "Major",
        "actions": [
            "Compare exception pack to schema",
            "Discuss discrepancies with Finance team",
            "Verify correct version of working papers and accounts",
        ]
    }

    # Output artifacts
    ts = time.strftime("%Y%m%d_%H%M%S")
    shap_png = str(out_dir / f"shap_demo_{ts}.png")
    pdf_path = str(out_dir / f"ASGL-A_Report_{ts}.pdf")

    generate_shap_like_plot(df, shap_png, logger)
    generate_pdf_report(df, shap_png, pdf_path, logger, decision)

    metrics = {
        "tables_detected": 2,
        "rows_processed": int(df.shape[0]),
        "schema": cfg.schema_name,
    }

    logger.info("ASGL-A pipeline complete")
    return PipelineResult(
        pdf_path=pdf_path,
        shap_png_path=shap_png,
        decision_status=decision["status"],
        severity=decision["severity"],
        recommended_actions=decision["actions"],
        metrics=metrics
    )
