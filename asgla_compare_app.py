from __future__ import annotations
import argparse
import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox

from asgla_compare.utils.logging_config import setup_logger
from asgla_compare.pipeline.run_compare import run

def _pick_file(title: str, filetypes):
    root = tk.Tk()
    root.withdraw()
    path = filedialog.askopenfilename(title=title, filetypes=filetypes)
    root.destroy()
    return path

def _pick_dir(title: str):
    root = tk.Tk()
    root.withdraw()
    path = filedialog.askdirectory(title=title)
    root.destroy()
    return path

def gui():
    excel_path = _pick_file("Select Excel working papers", [("Excel files", "*.xlsx *.xls"), ("All files", "*.*")])
    if not excel_path:
        return
    pdf_path = _pick_file("Select PDF output (e.g., TaxCalc/SA302/accounts)", [("PDF files", "*.pdf"), ("All files", "*.*")])
    if not pdf_path:
        return
    out_dir = _pick_dir("Select output folder")
    if not out_dir:
        out_dir = os.path.join(os.getcwd(), "asgla_compare_out")

    logger = setup_logger(out_dir=out_dir, debug=True)
    try:
        summary = run(excel_path, pdf_path, out_dir, logger)
        messagebox.showinfo("ASGL-A Compare", f"Done!\n\nPDF report: {summary['pdf']}\nCSV report: {summary['csv']}\n\nLogs: {os.path.join(out_dir, 'logs', 'asgla_compare.log')}")
    except Exception as e:
        logger.exception("Run failed")
        messagebox.showerror("ASGL-A Compare - Error", str(e))

def cli(args):
    out_dir = args.out or os.path.join(os.getcwd(), "asgla_compare_out")
    logger = setup_logger(out_dir=out_dir, debug=args.debug)
    summary = run(args.excel, args.pdf, out_dir, logger,
                  abs_tolerance=args.abs_tol,
                  pct_tolerance=args.pct_tol,
                  min_match_score=args.min_match_score)
    print("OK")
    print(summary)

def main():
    p = argparse.ArgumentParser(description="ASGL-A Excel vs PDF reconciliation")
    p.add_argument("--excel", help="Path to Excel working papers (.xlsx)")
    p.add_argument("--pdf", help="Path to PDF output (.pdf)")
    p.add_argument("--out", help="Output directory")
    p.add_argument("--abs-tol", dest="abs_tol", type=float, default=0.5, help="Absolute tolerance")
    p.add_argument("--pct-tol", dest="pct_tol", type=float, default=0.5, help="Percent tolerance")
    p.add_argument("--min-match-score", dest="min_match_score", type=float, default=75.0, help="Fuzzy match score threshold")
    p.add_argument("--debug", action="store_true", help="Enable debug logging")
    p.add_argument("--gui", action="store_true", help="Run GUI file picker")
    ns = p.parse_args()

    if ns.gui or (not ns.excel and not ns.pdf):
        gui()
        return

    if not ns.excel or not ns.pdf:
        print("ERROR: --excel and --pdf are required (or run with --gui).", file=sys.stderr)
        sys.exit(2)

    cli(ns)

if __name__ == "__main__":
    main()
